#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t version;
    uint16_t hold_ms;       /* start LED on-time (spec: 150 ms, about 18 frames at 120 fps) */
    uint8_t channel;        /* Wi-Fi channel used for ESP-NOW; start unit and beacon must agree */
    uint8_t selftest;       /* second flash 8-15 s after the first */
    uint8_t arm_on_boot;
    uint8_t reserved[3];
    float ref_hz;           /* reference LED blink frequency (spec: 23 Hz) */
} settings_t;

extern settings_t g_cfg;

void settings_defaults(void);
void settings_load(void);
bool settings_save(void);
/* Range-checked setters used by the console; return false when the value is refused. */
bool settings_set_hold_ms(int v);
bool settings_set_channel(int v);
bool settings_set_ref_hz(float v);
