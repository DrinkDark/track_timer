/*
 * Trigger logic (spec section 3, firmware points 2, 3 and 5). A GPIO interrupt timestamps the pulse edge with the
 * hardware timer, raises the test pin, requests the sound and queues the start packet for both links.
 * Everything else (logging, counters) happens after those lines. Triggers are ignored during the lockout.
 */
#include "trigger.h"

#include <inttypes.h>
#include <stdio.h>

#include "audio.h"
#include "driver/gpio.h"
#include "esp_attr.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "hal/gpio_ll.h"
#include "links.h"
#include "pins.h"
#include "settings.h"
#include "soc/gpio_struct.h"

typedef enum { SRC_ID_PISTOL, SRC_ID_BUTTON, SRC_ID_SOFTWARE } src_id_t;
typedef struct {
    src_id_t src;
    bool accepted;
    const char *why;
    int64_t t_us;
    uint16_t seq;
} trig_evt_t;

static QueueHandle_t s_logq;
static volatile int64_t s_last_trig_us = -(1LL << 40);
static volatile uint16_t s_seq;
static volatile uint32_t s_count, s_ignored;

bool trigger_arm_switch_on(void) { return gpio_get_level(PIN_ARM_SWITCH) == 0; }
bool trigger_in_lockout(void) { return (esp_timer_get_time() - s_last_trig_us) < (int64_t)g_cfg.lockout_ms * 1000; }
bool trigger_armed(void) { return !trigger_in_lockout(); }
uint32_t trigger_count(void) { return s_count; }
uint32_t trigger_ignored(void) { return s_ignored; }

static void IRAM_ATTR on_trigger(src_id_t src)
{
    int64_t now = esp_timer_get_time();                 /* the trigger time */
    gpio_ll_set_level(&GPIO, PIN_TEST_TRIG, 1);        /* for the logic analyzer */
    trig_evt_t e = {.src = src, .t_us = now};
    BaseType_t woke = pdFALSE;

    if (now - s_last_trig_us < (int64_t)g_cfg.lockout_ms * 1000) {
        gpio_ll_set_level(&GPIO, PIN_TEST_TRIG, 0);
        s_ignored++;
        e.why = "lockout";
    } else if (src == SRC_ID_BUTTON && gpio_ll_get_level(&GPIO, PIN_ARM_SWITCH) != 0) {
        gpio_ll_set_level(&GPIO, PIN_TEST_TRIG, 0);
        s_ignored++;
        e.why = "arm switch off";
    } else {
        s_last_trig_us = now;
        uint16_t seq = ++s_seq;
        s_count++;
        if (!g_cfg.mute) audio_play_from_isr();
        links_send_start_from_isr(seq, (uint32_t)now);
        e.accepted = true;
        e.seq = seq;
    }
    xQueueSendFromISR(s_logq, &e, &woke);
    if (woke) portYIELD_FROM_ISR();
}

static void IRAM_ATTR isr_pistol(void *arg)
{
    (void)arg;
    if (g_cfg.source != SRC_BUTTON) on_trigger(SRC_ID_PISTOL);
}

static void IRAM_ATTR isr_button(void *arg)
{
    (void)arg;
    if (g_cfg.source != SRC_PISTOL) on_trigger(SRC_ID_BUTTON);
}

void trigger_fire_software(void)
{
    portDISABLE_INTERRUPTS();
    on_trigger(SRC_ID_SOFTWARE);
    portENABLE_INTERRUPTS();
}

static void log_task(void *arg)
{
    (void)arg;
    trig_evt_t e;
    static const char *names[] = {"pistol", "button", "software"};
    for (;;) {
        if (xQueueReceive(s_logq, &e, portMAX_DELAY) != pdTRUE) continue;
        if (e.accepted) {
            int64_t req, hand;
            vTaskDelay(pdMS_TO_TICKS(5));
            audio_last_handoff(&req, &hand);
            printf("$TRIG t=%" PRId64 " source=%s seq=%u mute=%d audio_handoff_us=%" PRId64 " cable=%d radio=%d\n", e.t_us,
                   names[e.src], e.seq, g_cfg.mute, g_cfg.mute || req != e.t_us ? -1 : hand - e.t_us, g_cfg.cable_on,
                   g_cfg.radio_on);
        } else {
            printf("$IGNORED t=%" PRId64 " source=%s reason=\"%s\"\n", e.t_us, names[e.src], e.why);
        }
        fflush(stdout);
    }
}

void trigger_init(void)
{
    s_logq = xQueueCreate(16, sizeof(trig_evt_t));
    gpio_config_t out = {.pin_bit_mask = (1ULL << PIN_TEST_TRIG) | (1ULL << PIN_LED_STATUS), .mode = GPIO_MODE_OUTPUT};
    gpio_config(&out);
    gpio_set_level(PIN_TEST_TRIG, 0);
    gpio_config_t in = {.pin_bit_mask = (1ULL << PIN_BUTTON) | (1ULL << PIN_ARM_SWITCH), .mode = GPIO_MODE_INPUT,
                        .pull_up_en = GPIO_PULLUP_ENABLE};
    gpio_config(&in);
    gpio_config_t pistol = {.pin_bit_mask = (1ULL << PIN_PISTOL), .mode = GPIO_MODE_INPUT,
                            .pull_up_en = GPIO_PULLUP_ENABLE};   /* the 6N137 output has its own pull-up */
    gpio_config(&pistol);
    gpio_install_isr_service(0);
    gpio_set_intr_type(PIN_PISTOL, g_cfg.edge_rising ? GPIO_INTR_POSEDGE : GPIO_INTR_NEGEDGE);
    gpio_set_intr_type(PIN_BUTTON, GPIO_INTR_NEGEDGE);
    gpio_isr_handler_add(PIN_PISTOL, isr_pistol, NULL);
    gpio_isr_handler_add(PIN_BUTTON, isr_button, NULL);
    xTaskCreate(log_task, "triglog", 4096, NULL, 3, NULL);
}

void trigger_set_edge(bool rising) { gpio_set_intr_type(PIN_PISTOL, rising ? GPIO_INTR_POSEDGE : GPIO_INTR_NEGEDGE); }
