/* Host test for common/beacon_protocol.h:  cc -Wall -Wextra -I../common test_protocol.c -o /tmp/tp && /tmp/tp */
#include <stdio.h>
#include <string.h>

#include "beacon_protocol.h"

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)

static void hex(const char *s, uint8_t *out)
{
    for (int i = 0; s[2 * i]; i++) { unsigned v; sscanf(s + 2 * i, "%2x", &v); out[i] = (uint8_t)v; }
}

int main(void)
{
    /* vectors produced by tools/beacon_link.py */
    CHECK(bp_crc8((const uint8_t *)"123456789", 9) == 0xF4);
    struct { const char *hex; uint8_t type; uint16_t seq; uint32_t t; } v[] = {
        {"a50101000000000078", 1, 1, 0},
        {"a5013412efbeadde33", 1, 0x1234, 0xDEADBEEFu},
        {"a502ffff01000000b7", 2, 65535, 1},
        {"a503070015cd5b078c", 3, 7, 123456789u},
    };
    for (unsigned i = 0; i < sizeof v / sizeof v[0]; i++) {
        uint8_t raw[BP_LEN], enc[BP_LEN];
        bp_packet_t p;
        hex(v[i].hex, raw);
        CHECK(bp_decode(raw, BP_LEN, &p));
        CHECK(p.type == v[i].type && p.seq == v[i].seq && p.time_us == v[i].t);
        bp_encode(&p, enc);
        CHECK(memcmp(raw, enc, BP_LEN) == 0);
    }

    /* every single-bit error and every wrong length is rejected */
    uint8_t good[BP_LEN];
    bp_packet_t p = {BP_START, 42, 1000};
    bp_encode(&p, good);
    for (int byte = 0; byte < BP_LEN; byte++)
        for (int bit = 0; bit < 8; bit++) {
            uint8_t bad[BP_LEN];
            memcpy(bad, good, BP_LEN);
            bad[byte] ^= (uint8_t)(1u << bit);
            bp_packet_t q;
            CHECK(!bp_decode(bad, BP_LEN, &q));
        }
    bp_packet_t q;
    CHECK(!bp_decode(good, BP_LEN - 1, &q));
    CHECK(!bp_decode(good, BP_LEN + 1, &q));

    /* stream: garbage, a magic byte inside garbage, then two packets back to back */
    bp_stream_t s;
    bp_stream_init(&s);
    uint8_t noise[] = {0x00, 0xA5, 0xA5, 0x01, 0xFF, 0x13, 0xA5, 0x77, 0x00};
    uint8_t p2[BP_LEN];
    bp_packet_t pb = {BP_PING, 9, 5};
    bp_encode(&pb, p2);
    uint8_t streambuf[sizeof noise + 2 * BP_LEN];
    memcpy(streambuf, noise, sizeof noise);
    memcpy(streambuf + sizeof noise, good, BP_LEN);
    memcpy(streambuf + sizeof noise + BP_LEN, p2, BP_LEN);
    int got = 0;
    bp_packet_t out[4];
    for (unsigned i = 0; i < sizeof streambuf; i++)
        if (bp_stream_push(&s, streambuf[i], &out[got < 3 ? got : 3])) got++;
    CHECK(got == 2);
    CHECK(out[0].type == BP_START && out[0].seq == 42 && out[1].type == BP_PING && out[1].seq == 9);

    /* a packet whose first byte is lost: the next packet must still be found */
    bp_stream_init(&s);
    got = 0;
    for (int i = 1; i < BP_LEN; i++) CHECK(!bp_stream_push(&s, good[i], &q));
    for (int i = 0; i < BP_LEN; i++) if (bp_stream_push(&s, p2[i], &q)) got++;
    CHECK(got == 1 && q.seq == 9);

    /* dedupe: copy within the window is dropped, the same number later (rebooted unit) is accepted */
    bp_dedupe_t d;
    bp_dedupe_init(&d, 2000000);
    CHECK(bp_dedupe_is_new(&d, 5, 1000000));
    CHECK(!bp_dedupe_is_new(&d, 5, 1000300));        /* radio copy 0.3 ms later */
    CHECK(bp_dedupe_is_new(&d, 6, 1001000));
    CHECK(!bp_dedupe_is_new(&d, 5, 1500000));
    CHECK(bp_dedupe_is_new(&d, 5, 3500000));         /* 2.5 s later: new race, counter restarted */
    for (int i = 0; i < 10; i++) CHECK(bp_dedupe_is_new(&d, (uint16_t)(100 + i), 4000000 + i));
    CHECK(bp_dedupe_is_new(&d, 100, 4000100));       /* older than the 4 remembered slots */

    printf(fails ? "FAILED (%d)\n" : "all protocol tests passed\n", fails);
    return fails != 0;
}
