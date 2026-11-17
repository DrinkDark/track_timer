#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef enum { SRC_PISTOL = 0, SRC_BUTTON = 1, SRC_BOTH = 2 } start_source_t;

typedef struct {
    uint32_t version;
    uint8_t source;         /* start_source_t */
    uint8_t mute;           /* signals without sound (calibration, or a pistol that makes its own sound) */
    uint8_t cable_on;
    uint8_t radio_on;
    uint8_t edge_rising;    /* pistol pulse edge: 0 = falling (6N137 output goes low), 1 = rising */
    uint8_t volume;         /* 0-100 % */
    uint8_t channel;        /* Wi-Fi channel for ESP-NOW; must match the beacon */
    uint8_t heartbeat_s;    /* 0 = off. A ping every n s lets the beacon show "link seen"; see README */
    uint16_t lockout_ms;    /* triggers ignored for this long after a start (spec: 5 s) */
    uint16_t reserved;
} settings_t;

extern settings_t g_cfg;

void settings_defaults(void);
void settings_load(void);
bool settings_save(void);
