/*
 * Status LED (spec section 4, point 5). Every 2 s cycle shows a number of short pulses:
 *   disarmed: off          armed: 1 pulse
 *   +1 pulse  a valid packet was seen on a link in the last 15 s
 *   +1 pulse  self-test mode is on
 * While the start LED is on, the status LED is solid on.
 */
#include "status_led.h"

#include "beacon.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "pins.h"
#include "settings.h"

#define LINK_SEEN_US 15000000LL

static void status_task(void *arg)
{
    (void)arg;
    for (;;) {
        int64_t now = esp_timer_get_time();
        int pulses = 0;
        if (beacon_is_armed()) {
            pulses = 1;
            bool seen = (g_stats.last_rx_us[0] && now - g_stats.last_rx_us[0] < LINK_SEEN_US) ||
                        (g_stats.last_rx_us[1] && now - g_stats.last_rx_us[1] < LINK_SEEN_US);
            if (seen) pulses++;
            if (g_cfg.selftest) pulses++;
        }
        int phase = (int)((now / 1000) % 2000);
        bool on = beacon_flash_active() || (phase / 250 < pulses && phase % 250 < 100);
        gpio_set_level(PIN_LED_STATUS, on);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void status_led_start(void) { xTaskCreate(status_task, "status", 2048, NULL, 2, NULL); }
