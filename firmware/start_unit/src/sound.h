#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* The pistol sound lives in the "sound" partition (16-byte header + 16-bit mono PCM at 44.1 kHz) and is copied
 * into RAM at boot, so playing it needs no flash access at trigger time (spec section 3, firmware point 1). */
typedef struct {
    const int16_t *pcm;
    uint32_t samples;
    bool is_default;      /* the built-in synthetic bang, no sound stored yet */
} sound_t;

bool sound_load(void);                       /* (re)loads from flash into RAM, builds the default when empty */
const sound_t *sound_get(void);
/* Console upload: `total` bytes of raw PCM arrive through read_fn (blocking, returns bytes read or <0). */
typedef int (*sound_read_fn)(uint8_t *buf, size_t want);
bool sound_store(uint32_t total_bytes, uint32_t crc32_expected, sound_read_fn rd);
bool sound_erase(void);
uint32_t sound_crc32(const uint8_t *d, size_t n);
