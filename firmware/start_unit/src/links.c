/*
 * Transmit side of both links. One high-priority task sends every packet: start packets go first, on the cable
 * (RS485, 250 kbaud) and then on the radio (ESP-NOW broadcast), both with the same sequence number.
 * Pongs from the beacon are received on both links for the ping test.
 */
#include "links.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_attr.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "hal/gpio_ll.h"
#include "pins.h"
#include "settings.h"
#include "soc/gpio_struct.h"

static const char *TAG = "links";
static const uint8_t BCAST[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

link_stats_t g_link_stats;

typedef struct {
    bp_packet_t p;
    bool clear_test_pin;
    int64_t t_trig_us;      /* interrupt time of a start, to report how long the packet waited for the task */
} tx_req_t;

static QueueHandle_t s_txq, s_uartq;
static volatile int64_t s_ping_sent_us[2][64];
static volatile uint16_t s_ping_base;
static volatile int s_ping_n;
static volatile int64_t s_rtt_sum[2];
static volatile int64_t s_rtt_min[2], s_rtt_max[2];

static void send_one(const bp_packet_t *p)
{
    uint8_t buf[BP_LEN];
    bp_encode(p, buf);
    if (g_cfg.cable_on) {
        uart_write_bytes(RS485_UART, buf, BP_LEN);
        g_link_stats.sent[LINK_CABLE]++;
    }
    if (g_cfg.radio_on) {
        esp_now_send(BCAST, buf, BP_LEN);
        g_link_stats.sent[LINK_RADIO]++;
    }
}

static void tx_task(void *arg)
{
    (void)arg;
    tx_req_t r;
    for (;;) {
        if (xQueueReceive(s_txq, &r, portMAX_DELAY) != pdTRUE) continue;
        int64_t t0 = esp_timer_get_time();
        send_one(&r.p);
        if (r.p.type == BP_START) {
            printf("$TX t=%" PRId64 " type=start seq=%u isr_to_tx_us=%" PRId64 " tx_call_us=%" PRId64 "\n", t0, r.p.seq,
                   t0 - r.t_trig_us, esp_timer_get_time() - t0);
        }
        if (r.clear_test_pin) {
            vTaskDelay(pdMS_TO_TICKS(1));
            gpio_ll_set_level(&GPIO, PIN_TEST_TRIG, 0);
        }
    }
}

void IRAM_ATTR links_send_start_from_isr(uint16_t seq, uint32_t time_us)
{
    tx_req_t r = {.p = {.type = BP_START, .seq = seq, .time_us = time_us}, .clear_test_pin = true,
                  .t_trig_us = (int64_t)time_us};
    BaseType_t woke = pdFALSE;
    xQueueSendToFrontFromISR(s_txq, &r, &woke);
    if (woke) portYIELD_FROM_ISR();
}

void links_send_ping(uint16_t seq)
{
    tx_req_t r = {.p = {.type = BP_PING, .seq = seq, .time_us = (uint32_t)esp_timer_get_time()}};
    xQueueSend(s_txq, &r, 0);
}

/* ---- receive pongs ------------------------------------------------------------------------------ */
static void note_pong(link_t l, const bp_packet_t *p, int64_t t)
{
    g_link_stats.pongs[l]++;
    uint16_t i = (uint16_t)(p->seq - s_ping_base);
    if (i < 64 && s_ping_sent_us[l][i]) {
        int64_t rtt = t - s_ping_sent_us[l][i];
        s_ping_sent_us[l][i] = 0;
        s_rtt_sum[l] += rtt;
        if (s_rtt_min[l] == 0 || rtt < s_rtt_min[l]) s_rtt_min[l] = rtt;
        if (rtt > s_rtt_max[l]) s_rtt_max[l] = rtt;
    }
}

static void radio_rx(const esp_now_recv_info_t *info, const uint8_t *data, int len)
{
    int64_t t = esp_timer_get_time();
    (void)info;
    bp_packet_t p;
    if (bp_decode(data, (size_t)len, &p)) { if (p.type == BP_PONG) note_pong(LINK_RADIO, &p, t); }
    else g_link_stats.bad[LINK_RADIO]++;
}

static void uart_rx_task(void *arg)
{
    (void)arg;
    uart_config_t cfg = {.baud_rate = RS485_BAUD, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
                         .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
                         .source_clk = UART_SCLK_DEFAULT};
    uart_param_config(RS485_UART, &cfg);
    uart_set_pin(RS485_UART, PIN_RS485_TX, PIN_RS485_RX, PIN_RS485_DE, UART_PIN_NO_CHANGE);
    uart_driver_install(RS485_UART, 256, 256, 16, &s_uartq, 0);
    uart_set_mode(RS485_UART, UART_MODE_RS485_HALF_DUPLEX);
    uart_set_rx_timeout(RS485_UART, 3);
    bp_stream_t st;
    bp_stream_init(&st);
    uart_event_t ev;
    uint8_t buf[64];
    for (;;) {
        if (xQueueReceive(s_uartq, &ev, portMAX_DELAY) != pdTRUE) continue;
        if (ev.type == UART_DATA) {
            int64_t t = esp_timer_get_time();
            int n = uart_read_bytes(RS485_UART, buf, ev.size < sizeof buf ? ev.size : sizeof buf, 0);
            for (int i = 0; i < n; i++) {
                bp_packet_t p;
                if (bp_stream_push(&st, buf[i], &p) && p.type == BP_PONG) note_pong(LINK_CABLE, &p, t);
            }
        } else if (ev.type == UART_FIFO_OVF || ev.type == UART_BUFFER_FULL || ev.type == UART_FRAME_ERR) {
            g_link_stats.bad[LINK_CABLE]++;
            uart_flush_input(RS485_UART);
            xQueueReset(s_uartq);
            bp_stream_init(&st);
        }
    }
}

/* ---- ping test and heartbeat --------------------------------------------------------------------- */
static void ping_test_task(void *arg)
{
    int n = (int)(intptr_t)arg;
    static uint16_t base = 0x8000;
    if (n > 64) n = 64;
    memset((void *)s_ping_sent_us, 0, sizeof s_ping_sent_us);
    s_rtt_sum[0] = s_rtt_sum[1] = s_rtt_min[0] = s_rtt_min[1] = s_rtt_max[0] = s_rtt_max[1] = 0;
    uint32_t pongs0[2] = {g_link_stats.pongs[0], g_link_stats.pongs[1]};
    s_ping_base = base;
    for (int i = 0; i < n; i++) {
        int64_t now = esp_timer_get_time();
        s_ping_sent_us[0][i] = now;
        s_ping_sent_us[1][i] = now;
        bp_packet_t p = {.type = BP_PING, .seq = (uint16_t)(base + i), .time_us = (uint32_t)now};
        tx_req_t r = {.p = p};
        xQueueSend(s_txq, &r, 0);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    vTaskDelay(pdMS_TO_TICKS(100));
    const char *name[2] = {"cable", "radio"};
    for (int l = 0; l < 2; l++) {
        uint32_t got = g_link_stats.pongs[l] - pongs0[l];
        printf("$PING link=%s sent=%d received=%lu loss_pct=%.1f rtt_us_min=%lld mean=%lld max=%lld\n", name[l], n,
               (unsigned long)got, n ? 100.0 * (n - (int)got) / n : 0.0, (long long)s_rtt_min[l],
               got ? (long long)(s_rtt_sum[l] / (int64_t)got) : 0LL, (long long)s_rtt_max[l]);
    }
    base = (uint16_t)(base + n);
    vTaskDelete(NULL);
}

void links_ping_test(int n) { xTaskCreate(ping_test_task, "pingtest", 4096, (void *)(intptr_t)n, 4, NULL); }

static void heartbeat_task(void *arg)
{
    (void)arg;
    uint16_t seq = 0xC000;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        static int elapsed;
        if (!g_cfg.heartbeat_s) { elapsed = 0; continue; }
        if (++elapsed >= g_cfg.heartbeat_s) { elapsed = 0; links_send_ping(seq++); }
    }
}

void links_set_channel(int ch) { esp_wifi_set_channel((uint8_t)ch, WIFI_SECOND_CHAN_NONE); }

void links_init(void)
{
    s_txq = xQueueCreate(16, sizeof(tx_req_t));
    esp_netif_init();
    esp_event_loop_create_default();
    wifi_init_config_t wc = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&wc);
    esp_wifi_set_storage(WIFI_STORAGE_RAM);
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_start();
    esp_wifi_set_ps(WIFI_PS_NONE);
    esp_wifi_set_max_tx_power(80);
    links_set_channel(g_cfg.channel);
    if (esp_now_init() != ESP_OK) ESP_LOGE(TAG, "esp_now_init failed");
    esp_now_register_recv_cb(radio_rx);
    esp_now_peer_info_t peer = {.channel = 0, .ifidx = WIFI_IF_STA, .encrypt = false};
    memcpy(peer.peer_addr, BCAST, 6);
    esp_now_add_peer(&peer);
    xTaskCreatePinnedToCore(uart_rx_task, "rs485rx", 4096, NULL, configMAX_PRIORITIES - 4, NULL, 1);
    xTaskCreatePinnedToCore(tx_task, "tx", 4096, NULL, configMAX_PRIORITIES - 2, NULL, 1);
    xTaskCreate(heartbeat_task, "heartbeat", 2048, NULL, 1, NULL);
}
