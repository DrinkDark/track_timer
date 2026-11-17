#include "ref_led.h"

#include <math.h>

#include "driver/gptimer.h"
#include "esp_attr.h"
#include "hal/gpio_ll.h"
#include "pins.h"
#include "soc/gpio_struct.h"

static gptimer_handle_t s_timer;
static volatile uint32_t s_level;
static float s_actual_hz;

static bool IRAM_ATTR on_alarm(gptimer_handle_t t, const gptimer_alarm_event_data_t *ev, void *ctx)
{
    (void)t; (void)ev; (void)ctx;
    s_level ^= 1u;
    gpio_ll_set_level(&GPIO, PIN_LED_REF, s_level);
    return false;
}

float ref_led_actual_hz(void) { return s_timer ? s_actual_hz : 0.0f; }

void ref_led_stop(void)
{
    if (!s_timer) return;
    gptimer_stop(s_timer);
    gptimer_disable(s_timer);
    gptimer_del_timer(s_timer);
    s_timer = NULL;
    gpio_ll_set_level(&GPIO, PIN_LED_REF, 0);
}

void ref_led_start(float hz)
{
    ref_led_stop();
    uint32_t half_us = (uint32_t)lroundf(1e6f / (2.0f * hz));   /* 21739 us for 23 Hz: 6 ppm from exact */
    if (half_us < 1000) half_us = 1000;
    s_actual_hz = 1e6f / (2.0f * (float)half_us);
    gptimer_config_t cfg = {.clk_src = GPTIMER_CLK_SRC_DEFAULT, .direction = GPTIMER_COUNT_UP, .resolution_hz = 1000000};
    gptimer_new_timer(&cfg, &s_timer);
    gptimer_event_callbacks_t cbs = {.on_alarm = on_alarm};
    gptimer_register_event_callbacks(s_timer, &cbs, NULL);
    gptimer_alarm_config_t alarm = {.alarm_count = half_us, .reload_count = 0, .flags.auto_reload_on_alarm = true};
    gptimer_set_alarm_action(s_timer, &alarm);
    s_level = 1;
    gpio_ll_set_level(&GPIO, PIN_LED_REF, 1);
    gptimer_enable(s_timer);
    gptimer_start(s_timer);
}
