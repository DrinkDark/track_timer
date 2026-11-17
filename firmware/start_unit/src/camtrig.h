#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Camera trigger generator (spec section 11, option to evaluate): a hardware timer toggles PIN_CAM_TRIG,
 * one rising edge per frame, with a counter of the pulses sent. Not connected to the start logic. */
typedef struct {
    bool running;
    float target_hz;
    float actual_hz;       /* after rounding the half period to whole timer ticks */
    uint32_t pulses;       /* rising edges since the last start */
    uint32_t limit;        /* 0 = run until stopped, else a burst of this many pulses */
    int64_t last_rise_us;  /* esp_timer time of the last rising edge */
} camtrig_state_t;

void camtrig_init(void);
bool camtrig_start(float hz, uint32_t burst);   /* hz 1-250; false when refused */
void camtrig_stop(void);
camtrig_state_t camtrig_state(void);
