/*
 * Beacon core (spec section 4): the first valid start packet on either link switches the start LED on,
 * in the same function call that received it. The LED stays on for hold_ms; the link indicator shows which
 * link won. A self-test can fire a second flash 8-15 s later and log the exact interval.
 */
#include "beacon.h"

#include <inttypes.h>
#include <stdio.h>

#include "driver/gpio.h"
#include "esp_attr.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "hal/gpio_ll.h"
#include "pins.h"
#include "ref_led.h"
#include "settings.h"
#include "soc/gpio_struct.h"

#define DUP_WINDOW_US 2000000

beacon_stats_t g_stats;

typedef enum { EVT_PKT, EVT_FLASH_ON, EVT_FLASH_OFF, EVT_SELFTEST, EVT_RETRIGGER } evt_kind_t;
typedef struct {
    evt_kind_t kind;
    link_t link;
    uint8_t type;
    uint16_t seq;
    uint32_t tx_us;
    int64_t t_us;
    int64_t t2_us;       /* EVT_SELFTEST: second edge; EVT_PKT: not used */
    int64_t planned_us;  /* EVT_SELFTEST */
    bool is_new;
} evt_t;

static QueueHandle_t s_evtq;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static bp_dedupe_t s_dedupe;
static reply_fn_t s_reply[2];
static esp_timer_handle_t s_off_timer, s_second_timer;
static volatile bool s_led_on, s_armed, s_second_pending;
static volatile link_t s_flash_link;
static volatile int64_t s_first_on_us, s_planned_us;

static const char *link_name(link_t l) { return l == LINK_CABLE ? "cable" : l == LINK_RADIO ? "radio" : "local"; }

static void IRAM_ATTR post_evt_isr(const evt_t *e)
{
    BaseType_t woke = pdFALSE;
    xQueueSendFromISR(s_evtq, e, &woke);
    if (woke) portYIELD_FROM_ISR();
}

static void post_evt(const evt_t *e) { xQueueSend(s_evtq, e, 0); }

/* LED on + link indicator. Must be called with s_mux held (or from an ISR). Returns the timestamp of the edge. */
static inline int64_t IRAM_ATTR led_on_locked(link_t link)
{
    gpio_ll_set_level(&GPIO, PIN_LED_START, 1);
    gpio_ll_set_level(&GPIO, PIN_LED_LINK, link == LINK_RADIO ? 1 : 0);
    int64_t t = esp_timer_get_time();
    s_led_on = true;
    s_flash_link = link;
    return t;
}

static void IRAM_ATTR restart_off_timer(void)
{
    esp_timer_stop(s_off_timer);   /* ESP_ERR_INVALID_STATE when not running: fine */
    esp_timer_start_once(s_off_timer, (uint64_t)g_cfg.hold_ms * 1000u);
}

static void IRAM_ATTR off_cb(void *arg)
{
    (void)arg;
    gpio_ll_set_level(&GPIO, PIN_LED_START, 0);
    gpio_ll_set_level(&GPIO, PIN_LED_LINK, 0);
    s_led_on = false;
    evt_t e = {.kind = EVT_FLASH_OFF, .t_us = esp_timer_get_time()};
    post_evt_isr(&e);
}

/* Second flash of the self-test: runs straight from the timer interrupt, so its edge is as exact as the first. */
static void IRAM_ATTR second_cb(void *arg)
{
    (void)arg;
    portENTER_CRITICAL_ISR(&s_mux);
    int64_t t2 = led_on_locked(s_flash_link);
    s_second_pending = false;
    portEXIT_CRITICAL_ISR(&s_mux);
    restart_off_timer();
    evt_t e = {.kind = EVT_SELFTEST, .link = s_flash_link, .t_us = s_first_on_us, .t2_us = t2,
               .planned_us = s_planned_us};
    post_evt_isr(&e);
}

static void schedule_selftest(int64_t t_first_on)
{
    esp_timer_stop(s_second_timer);
    if (!g_cfg.selftest) { s_second_pending = false; return; }
    int64_t delay = 8000000 + (int64_t)(esp_random() % 7000001u);   /* 8 to 15 s, microsecond resolution */
    s_first_on_us = t_first_on;
    s_planned_us = delay;
    s_second_pending = true;
    esp_timer_start_once(s_second_timer, (uint64_t)delay);
}

static void trigger(link_t link, const bp_packet_t *p, int64_t t_rx_us)
{
    bool fire, was_on;
    int64_t t_on = 0;
    portENTER_CRITICAL(&s_mux);
    fire = bp_dedupe_is_new(&s_dedupe, p->seq, t_rx_us);
    was_on = s_led_on;
    if (fire && !was_on) t_on = led_on_locked(link);   /* the time-critical line: LED first, bookkeeping after */
    portEXIT_CRITICAL(&s_mux);

    evt_t pk = {.kind = EVT_PKT, .link = link, .type = p->type, .seq = p->seq, .tx_us = p->time_us,
                .t_us = t_rx_us, .is_new = fire};
    post_evt(&pk);
    if (!fire) { g_stats.dups++; return; }
    g_stats.starts++;
    restart_off_timer();
    if (was_on) {                                      /* a new start while the LED is still on: just extend it */
        evt_t r = {.kind = EVT_RETRIGGER, .link = link, .seq = p->seq, .t_us = esp_timer_get_time()};
        post_evt(&r);
        return;
    }
    evt_t on = {.kind = EVT_FLASH_ON, .link = link, .seq = p->seq, .t_us = t_on};
    post_evt(&on);
    schedule_selftest(t_on);
}

void beacon_on_packet(link_t link, const bp_packet_t *p, int64_t t_rx_us)
{
    if (link < 2) {
        g_stats.rx[link]++;
        g_stats.last_rx_us[link] = t_rx_us;
    }
    if (p->type == BP_START) {
        trigger(link, p, t_rx_us);
        return;
    }
    evt_t pk = {.kind = EVT_PKT, .link = link, .type = p->type, .seq = p->seq, .tx_us = p->time_us,
                .t_us = t_rx_us, .is_new = true};
    post_evt(&pk);
    if (p->type == BP_PING && link < 2 && s_reply[link]) {      /* answer on the link the ping came from */
        bp_packet_t pong = {.type = BP_PONG, .seq = p->seq, .time_us = (uint32_t)esp_timer_get_time()};
        uint8_t buf[BP_LEN];
        bp_encode(&pong, buf);
        s_reply[link](buf, BP_LEN);
    }
}

void beacon_fire_local(void)
{
    static uint16_t seq = 0xF000;
    bp_packet_t p = {.type = BP_START, .seq = seq++, .time_us = 0};
    trigger(LINK_LOCAL, &p, esp_timer_get_time());
}

void beacon_set_reply(link_t link, reply_fn_t fn) { if (link < 2) s_reply[link] = fn; }
void beacon_log_bad(link_t link) { if (link < 2) g_stats.bad[link]++; }
void beacon_arm(bool on)
{
    s_armed = on;
    if (on) ref_led_start(g_cfg.ref_hz); else ref_led_stop();
}
bool beacon_is_armed(void) { return s_armed; }
bool beacon_flash_active(void) { return s_led_on; }
bool beacon_selftest_pending(void) { return s_second_pending; }

/* Prints one line per event: "$KIND key=value ...", microsecond timestamps since boot, easy to parse. */
static void log_task(void *arg)
{
    (void)arg;
    evt_t e;
    for (;;) {
        if (xQueueReceive(s_evtq, &e, portMAX_DELAY) != pdTRUE) continue;
        switch (e.kind) {
        case EVT_PKT:
            printf("$PKT t=%" PRId64 " link=%s type=%s seq=%u tx_us=%" PRIu32 " %s\n", e.t_us, link_name(e.link),
                   e.type == BP_START ? "start" : e.type == BP_PING ? "ping" : "pong", e.seq, e.tx_us,
                   e.type != BP_START ? "" : e.is_new ? "new" : "duplicate");
            break;
        case EVT_FLASH_ON:
            printf("$FLASH_ON t=%" PRId64 " link=%s seq=%u hold_ms=%u\n", e.t_us, link_name(e.link), e.seq,
                   g_cfg.hold_ms);
            break;
        case EVT_RETRIGGER:
            printf("$RETRIGGER t=%" PRId64 " link=%s seq=%u (LED still on, hold extended)\n", e.t_us,
                   link_name(e.link), e.seq);
            break;
        case EVT_FLASH_OFF:
            printf("$FLASH_OFF t=%" PRId64 "\n", e.t_us);
            break;
        case EVT_SELFTEST:
            printf("$SELFTEST first_us=%" PRId64 " second_us=%" PRId64 " interval_us=%" PRId64 " planned_us=%" PRId64
                   " interval_s=%.6f\n",
                   e.t_us, e.t2_us, e.t2_us - e.t_us, e.planned_us, (double)(e.t2_us - e.t_us) / 1e6);
            break;
        }
        fflush(stdout);
    }
}

void beacon_init(void)
{
    gpio_config_t io = {.pin_bit_mask = (1ULL << PIN_LED_START) | (1ULL << PIN_LED_REF) | (1ULL << PIN_LED_LINK) |
                                        (1ULL << PIN_LED_STATUS),
                        .mode = GPIO_MODE_OUTPUT};
    gpio_config(&io);
    gpio_ll_set_level(&GPIO, PIN_LED_START, 0);
    gpio_ll_set_level(&GPIO, PIN_LED_REF, 0);
    gpio_ll_set_level(&GPIO, PIN_LED_LINK, 0);
    gpio_ll_set_level(&GPIO, PIN_LED_STATUS, 0);

    bp_dedupe_init(&s_dedupe, DUP_WINDOW_US);
    s_evtq = xQueueCreate(64, sizeof(evt_t));

    const esp_timer_create_args_t off = {.callback = off_cb, .dispatch_method = ESP_TIMER_ISR, .name = "led_off"};
    esp_timer_create(&off, &s_off_timer);
    const esp_timer_create_args_t sec = {.callback = second_cb, .dispatch_method = ESP_TIMER_ISR, .name = "selftest"};
    esp_timer_create(&sec, &s_second_timer);
    xTaskCreate(log_task, "log", 4096, NULL, 3, NULL);
}
