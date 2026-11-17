#pragma once
#include <stdint.h>

#include "beacon_protocol.h"

typedef enum { LINK_CABLE = 0, LINK_RADIO = 1 } link_t;

typedef struct {
    volatile uint32_t sent[2];
    volatile uint32_t pongs[2];
    volatile uint32_t bad[2];
} link_stats_t;
extern link_stats_t g_link_stats;

void links_init(void);
void links_set_channel(int ch);
/* Interrupt-safe: queues a start packet at the front of the transmit queue (cable first, then radio). */
void links_send_start_from_isr(uint16_t seq, uint32_t time_us);
void links_send_ping(uint16_t seq);
/* Round-trip test: n pings per link, 20 ms apart; results are printed as $PING lines. */
void links_ping_test(int n);
