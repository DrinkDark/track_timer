#pragma once
#include <stdbool.h>
#include <stdint.h>

#include "beacon_protocol.h"

typedef enum { LINK_CABLE = 0, LINK_RADIO = 1, LINK_LOCAL = 2 } link_t;

typedef struct {
    volatile uint32_t rx[2];        /* valid packets per link (cable, radio) */
    volatile uint32_t bad[2];       /* frames that failed magic/CRC/length */
    volatile uint32_t starts;       /* flashes triggered */
    volatile uint32_t dups;         /* start copies dropped */
    volatile int64_t last_rx_us[2]; /* arrival time of the last valid packet per link, 0 = never */
} beacon_stats_t;

extern beacon_stats_t g_stats;

void beacon_init(void);
void beacon_arm(bool on);
bool beacon_is_armed(void);
bool beacon_flash_active(void);
bool beacon_selftest_pending(void);
/* Called by the link drivers for every valid packet; t_rx_us is taken as early as possible. */
void beacon_on_packet(link_t link, const bp_packet_t *p, int64_t t_rx_us);
/* Local test flash from the console (same path as a start packet). */
void beacon_fire_local(void);
/* Link drivers register how to answer a ping on their link. */
typedef void (*reply_fn_t)(const uint8_t *buf, int len);
void beacon_set_reply(link_t link, reply_fn_t fn);
void beacon_log_bad(link_t link);
