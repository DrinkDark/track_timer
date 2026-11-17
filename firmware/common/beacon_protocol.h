/*
 * Link protocol shared by the start unit, the beacon and the PC tools (spec section 5).
 * Plain C, no platform dependencies, so it can be unit-tested on the PC (firmware/test).
 *
 * 9-byte packet, identical on RS485 and ESP-NOW:
 *   [0] magic 0xA5   [1] type (1 start, 2 ping, 3 pong)   [2..3] sequence, little endian
 *   [4..7] start-unit microsecond timer, little endian    [8] CRC-8 over bytes 0..7
 * CRC-8: polynomial 0x07, initial value 0, no reflection, no final xor (the spec leaves this open).
 */
#ifndef BEACON_PROTOCOL_H
#define BEACON_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define BP_MAGIC 0xA5u
#define BP_LEN 9

enum { BP_START = 1, BP_PING = 2, BP_PONG = 3 };

typedef struct {
    uint8_t type;
    uint16_t seq;
    uint32_t time_us;
} bp_packet_t;

static inline uint8_t bp_crc8(const uint8_t *d, size_t n)
{
    uint8_t c = 0;
    while (n--) {
        c ^= *d++;
        for (int i = 0; i < 8; i++) {
            c = (c & 0x80u) ? (uint8_t)((c << 1) ^ 0x07u) : (uint8_t)(c << 1);
        }
    }
    return c;
}

static inline void bp_encode(const bp_packet_t *p, uint8_t out[BP_LEN])
{
    out[0] = BP_MAGIC;
    out[1] = p->type;
    out[2] = (uint8_t)(p->seq & 0xFFu);
    out[3] = (uint8_t)(p->seq >> 8);
    out[4] = (uint8_t)(p->time_us & 0xFFu);
    out[5] = (uint8_t)((p->time_us >> 8) & 0xFFu);
    out[6] = (uint8_t)((p->time_us >> 16) & 0xFFu);
    out[7] = (uint8_t)(p->time_us >> 24);
    out[8] = bp_crc8(out, 8);
}

/* Exactly one packet: length, magic, type and CRC must all be right. */
static inline bool bp_decode(const uint8_t *buf, size_t len, bp_packet_t *p)
{
    if (len != BP_LEN || buf[0] != BP_MAGIC) return false;
    if (buf[1] < BP_START || buf[1] > BP_PONG) return false;
    if (bp_crc8(buf, 8) != buf[8]) return false;
    p->type = buf[1];
    p->seq = (uint16_t)(buf[2] | (buf[3] << 8));
    p->time_us = (uint32_t)buf[4] | ((uint32_t)buf[5] << 8) | ((uint32_t)buf[6] << 16) | ((uint32_t)buf[7] << 24);
    return true;
}

/* ---- byte stream (RS485): a sliding 9-byte window that re-synchronises after noise ---------- */
typedef struct {
    uint8_t win[BP_LEN];
    uint8_t n;
} bp_stream_t;

static inline void bp_stream_init(bp_stream_t *s) { s->n = 0; }

/* Feeds one byte; returns true when it completed a valid packet (written to *p). */
static inline bool bp_stream_push(bp_stream_t *s, uint8_t byte, bp_packet_t *p)
{
    if (s->n == BP_LEN) {                       /* window full and the last attempt failed: slide by one */
        for (uint8_t i = 1; i < BP_LEN; i++) s->win[i - 1] = s->win[i];
        s->n = BP_LEN - 1;
    }
    if (s->n == 0 && byte != BP_MAGIC) return false;   /* wait for a magic byte */
    s->win[s->n++] = byte;
    if (s->n < BP_LEN) return false;
    if (bp_decode(s->win, BP_LEN, p)) {
        s->n = 0;
        return true;
    }
    /* Not a packet: drop leading bytes up to the next magic byte and keep the rest. */
    uint8_t k = 1;
    while (k < BP_LEN && s->win[k] != BP_MAGIC) k++;
    for (uint8_t i = k; i < BP_LEN; i++) s->win[i - k] = s->win[i];
    s->n = (uint8_t)(BP_LEN - k);
    return false;
}

/* ---- duplicate filter: the same start arrives on cable and radio; only the first one counts ---- */
/* A sequence number is a duplicate only within a time window, so a start unit that rebooted
 * (sequence counter back at 0) is never mistaken for an old packet. */
#define BP_DEDUPE_SLOTS 4

typedef struct {
    uint16_t seq[BP_DEDUPE_SLOTS];
    int64_t t_us[BP_DEDUPE_SLOTS];
    bool used[BP_DEDUPE_SLOTS];
    uint8_t next;
    int64_t window_us;
} bp_dedupe_t;

static inline void bp_dedupe_init(bp_dedupe_t *d, int64_t window_us)
{
    for (int i = 0; i < BP_DEDUPE_SLOTS; i++) d->used[i] = false;
    d->next = 0;
    d->window_us = window_us;
}

/* True when this packet is new (and remembers it); false for a copy of one seen within the window. */
static inline bool bp_dedupe_is_new(bp_dedupe_t *d, uint16_t seq, int64_t now_us)
{
    for (int i = 0; i < BP_DEDUPE_SLOTS; i++) {
        if (d->used[i] && d->seq[i] == seq && (now_us - d->t_us[i]) < d->window_us) return false;
    }
    d->seq[d->next] = seq;
    d->t_us[d->next] = now_us;
    d->used[d->next] = true;
    d->next = (uint8_t)((d->next + 1) % BP_DEDUPE_SLOTS);
    return true;
}

#endif /* BEACON_PROTOCOL_H */
