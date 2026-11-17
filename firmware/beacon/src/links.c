/*
 * Cable (RS485, 250 kbaud) and radio (ESP-NOW broadcast) receivers. Both hand every valid packet to
 * beacon_on_packet() with the arrival time, taken as early as possible.
 */
#include "links.h"

#include <string.h>

#include "beacon.h"
#include "driver/uart.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "pins.h"
#include "settings.h"

static const char *TAG = "links";
static const uint8_t BCAST[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

/* ---- cable -------------------------------------------------------------------------------------- */
static QueueHandle_t s_uart_q;

static void cable_reply(const uint8_t *buf, int len) { uart_write_bytes(RS485_UART, buf, len); }

static void uart_task(void *arg)
{
    (void)arg;
    /* Installed from this task so the UART interrupt runs on the same core as this task (core 1), away from Wi-Fi. */
    uart_config_t cfg = {.baud_rate = RS485_BAUD, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
                         .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
                         .source_clk = UART_SCLK_DEFAULT};
    uart_param_config(RS485_UART, &cfg);
    uart_set_pin(RS485_UART, PIN_RS485_TX, PIN_RS485_RX, PIN_RS485_DE, UART_PIN_NO_CHANGE);
    uart_driver_install(RS485_UART, 256, 256, 16, &s_uart_q, 0);
    uart_set_mode(RS485_UART, UART_MODE_RS485_HALF_DUPLEX);
    uart_set_rx_full_threshold(RS485_UART, BP_LEN);   /* one event when a whole packet is in the FIFO */
    uart_set_rx_timeout(RS485_UART, 3);               /* ...or 3 character times after the last byte */
    beacon_set_reply(LINK_CABLE, cable_reply);

    bp_stream_t st;
    bp_stream_init(&st);
    uart_event_t ev;
    uint8_t buf[64];
    for (;;) {
        if (xQueueReceive(s_uart_q, &ev, portMAX_DELAY) != pdTRUE) continue;
        if (ev.type == UART_DATA) {
            int64_t t = esp_timer_get_time();
            int n = uart_read_bytes(RS485_UART, buf, ev.size < sizeof buf ? ev.size : sizeof buf, 0);
            for (int i = 0; i < n; i++) {
                bp_packet_t p;
                if (bp_stream_push(&st, buf[i], &p)) beacon_on_packet(LINK_CABLE, &p, t);
            }
        } else if (ev.type == UART_FIFO_OVF || ev.type == UART_BUFFER_FULL || ev.type == UART_FRAME_ERR ||
                   ev.type == UART_PARITY_ERR) {
            beacon_log_bad(LINK_CABLE);
            uart_flush_input(RS485_UART);
            xQueueReset(s_uart_q);
            bp_stream_init(&st);
        }
    }
}

/* ---- radio -------------------------------------------------------------------------------------- */
static void radio_reply(const uint8_t *buf, int len) { esp_now_send(BCAST, buf, len); }

static void radio_rx(const esp_now_recv_info_t *info, const uint8_t *data, int len)
{
    int64_t t = esp_timer_get_time();   /* first thing: the arrival time */
    (void)info;
    bp_packet_t p;
    if (bp_decode(data, (size_t)len, &p)) beacon_on_packet(LINK_RADIO, &p, t);
    else beacon_log_bad(LINK_RADIO);
}

void links_set_channel(int channel) { esp_wifi_set_channel((uint8_t)channel, WIFI_SECOND_CHAN_NONE); }

static void radio_init(void)
{
    esp_netif_init();
    esp_event_loop_create_default();
    wifi_init_config_t wc = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&wc);
    esp_wifi_set_storage(WIFI_STORAGE_RAM);
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_start();
    esp_wifi_set_ps(WIFI_PS_NONE);              /* no modem sleep: it would add milliseconds of latency */
    esp_wifi_set_max_tx_power(80);              /* 20 dBm for the pong */
    links_set_channel(g_cfg.channel);
    if (esp_now_init() != ESP_OK) { ESP_LOGE(TAG, "esp_now_init failed"); return; }
    esp_now_register_recv_cb(radio_rx);
    esp_now_peer_info_t peer = {.channel = 0, .ifidx = WIFI_IF_STA, .encrypt = false};
    memcpy(peer.peer_addr, BCAST, 6);
    esp_now_add_peer(&peer);
    beacon_set_reply(LINK_RADIO, radio_reply);
    ESP_LOGI(TAG, "ESP-NOW listening on channel %u", g_cfg.channel);
}

void links_init(void)
{
    radio_init();
    xTaskCreatePinnedToCore(uart_task, "rs485", 4096, NULL, configMAX_PRIORITIES - 2, NULL, 1);
}
