#include "camtrig.h"

#include <math.h>
#include <stdio.h>

#include "driver/gpio.h"
#include "driver/gptimer.h"
#include "esp_attr.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/gpio_ll.h"
#include "pins.h"
#include "soc/gpio_struct.h"

#define TIMER_HZ 20000000u   /* 20 MHz ticks: 120 Hz is 166667 ticks per half period pair, 2 ppm from exact */

static gptimer_handle_t s_timer;
static volatile bool s_running, s_level, s_finish;
static volatile uint32_t s_pulses, s_limit;
static volatile int64_t s_last_rise_us;
static float s_target_hz, s_actual_hz;

/* One alarm per half period. The first rising edge is made in camtrig_start(); a burst ends after the falling
 * edge that follows its last rising edge. */
static bool IRAM_ATTR on_alarm(gptimer_handle_t t, const gptimer_alarm_event_data_t *ev, void *ctx)
{
    (void)t; (void)ev; (void)ctx;
    if (s_finish) return false;
    s_level = !s_level;
    gpio_ll_set_level(&GPIO, PIN_CAM_TRIG, s_level);
    if (s_level) {
        s_pulses++;
        s_last_rise_us = esp_timer_get_time();
    } else if (s_limit && s_pulses >= s_limit) {
        s_finish = true;
    }
    return false;
}

static void reaper_task(void *arg)
{
    (void)arg;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(5));
        if (s_running && s_finish) {
            camtrig_stop();
            printf("$CAMTRIG done pulses=%lu\n", (unsigned long)s_pulses);
            fflush(stdout);
        }
    }
}

void camtrig_init(void)
{
    gpio_config_t io = {.pin_bit_mask = 1ULL << PIN_CAM_TRIG, .mode = GPIO_MODE_OUTPUT};
    gpio_config(&io);
    gpio_ll_set_level(&GPIO, PIN_CAM_TRIG, 0);
    xTaskCreate(reaper_task, "camtrig", 3072, NULL, 4, NULL);
}

void camtrig_stop(void)
{
    if (s_timer) {
        gptimer_stop(s_timer);
        gptimer_disable(s_timer);
        gptimer_del_timer(s_timer);
        s_timer = NULL;
    }
    s_running = false;
    s_level = false;
    gpio_ll_set_level(&GPIO, PIN_CAM_TRIG, 0);
}

bool camtrig_start(float hz, uint32_t burst)
{
    if (!(hz >= 1.0f && hz <= 250.0f)) return false;
    camtrig_stop();
    uint32_t half = (uint32_t)lroundf((float)TIMER_HZ / (2.0f * hz));
    s_target_hz = hz;
    s_actual_hz = (float)TIMER_HZ / (2.0f * (float)half);
    s_pulses = 0;
    s_limit = burst;
    s_finish = false;
    gptimer_config_t cfg = {.clk_src = GPTIMER_CLK_SRC_DEFAULT, .direction = GPTIMER_COUNT_UP, .resolution_hz = TIMER_HZ};
    if (gptimer_new_timer(&cfg, &s_timer) != ESP_OK) return false;
    gptimer_event_callbacks_t cbs = {.on_alarm = on_alarm};
    gptimer_register_event_callbacks(s_timer, &cbs, NULL);
    gptimer_alarm_config_t alarm = {.alarm_count = half, .reload_count = 0, .flags.auto_reload_on_alarm = true};
    gptimer_set_alarm_action(s_timer, &alarm);
    gptimer_enable(s_timer);
    s_running = true;
    s_level = true;                                   /* first rising edge now */
    gpio_ll_set_level(&GPIO, PIN_CAM_TRIG, 1);
    s_pulses = 1;
    s_last_rise_us = esp_timer_get_time();
    gptimer_start(s_timer);
    return true;
}

camtrig_state_t camtrig_state(void)
{
    return (camtrig_state_t){.running = s_running, .target_hz = s_target_hz, .actual_hz = s_actual_hz,
                             .pulses = s_pulses, .limit = s_limit, .last_rise_us = s_last_rise_us};
}
