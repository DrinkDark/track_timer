#pragma once
/* Reference LED blinker driven by a hardware timer (spec section 4, point 3). */
void ref_led_start(float hz);
void ref_led_stop(void);
/* Actual blink frequency after rounding the half period to whole microseconds. */
float ref_led_actual_hz(void);
