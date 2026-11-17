#include "sound.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "pins.h"

#define SND_MAGIC 0x444E5350u   /* "PSND" */
#define HDR_SIZE 16
#define MAX_BYTES (0x40000 - 4096)
static const char *TAG = "sound";

typedef struct {
    uint32_t magic, rate, samples, crc32;
} snd_hdr_t;

static sound_t s_snd;
static int16_t *s_ram;
static const esp_partition_t *part(void)
{
    return esp_partition_find_first(ESP_PARTITION_TYPE_DATA, (esp_partition_subtype_t)0x40, "sound");
}

uint32_t sound_crc32(const uint8_t *d, size_t n)
{
    uint32_t c = 0xFFFFFFFFu;
    while (n--) {
        c ^= *d++;
        for (int i = 0; i < 8; i++) c = (c & 1) ? (c >> 1) ^ 0xEDB88320u : c >> 1;
    }
    return ~c;
}

/* A pistol-like bang for use until a real recording is uploaded: noise burst, low thump and a decaying tail. */
static void synth_default(int16_t *out, uint32_t n)
{
    uint32_t lfsr = 0xACE1u;
    float lp = 0;
    for (uint32_t i = 0; i < n; i++) {
        float t = (float)i / SAMPLE_RATE;
        lfsr = (lfsr >> 1) ^ (-(lfsr & 1u) & 0xB400u);
        float noise = ((float)(lfsr & 0xFFFF) / 32768.0f) - 1.0f;
        lp += 0.35f * (noise - lp);                                    /* softened noise */
        float v = 0.9f * lp * expf(-t / 0.045f) + 0.5f * sinf(2 * (float)M_PI * 110.0f * t) * expf(-t / 0.07f) +
                  0.25f * noise * expf(-t / 0.35f);
        if (t < 0.0005f) v *= t / 0.0005f;                              /* 0.5 ms attack: no click from DC */
        v *= 0.8f;
        out[i] = (int16_t)(v > 1 ? 32767 : v < -1 ? -32767 : v * 32767.0f);
    }
}

const sound_t *sound_get(void) { return &s_snd; }

bool sound_load(void)
{
    free(s_ram);
    s_ram = NULL;
    const esp_partition_t *p = part();
    snd_hdr_t h;
    if (p && esp_partition_read(p, 0, &h, sizeof h) == ESP_OK && h.magic == SND_MAGIC && h.rate == SAMPLE_RATE &&
        h.samples > 0 && h.samples * 2u <= MAX_BYTES) {
        s_ram = heap_caps_malloc(h.samples * 2u, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        if (s_ram && esp_partition_read(p, HDR_SIZE, s_ram, h.samples * 2u) == ESP_OK &&
            sound_crc32((const uint8_t *)s_ram, h.samples * 2u) == h.crc32) {
            s_snd = (sound_t){.pcm = s_ram, .samples = h.samples, .is_default = false};
            ESP_LOGI(TAG, "stored sound: %lu samples (%.2f s)", (unsigned long)h.samples, (double)h.samples / SAMPLE_RATE);
            return true;
        }
        free(s_ram);
        s_ram = NULL;
        ESP_LOGW(TAG, "stored sound is damaged, using the built-in one");
    }
    uint32_t n = SAMPLE_RATE * 12 / 10;   /* 1.2 s */
    s_ram = heap_caps_malloc(n * 2u, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!s_ram) { s_snd = (sound_t){0}; return false; }
    synth_default(s_ram, n);
    s_snd = (sound_t){.pcm = s_ram, .samples = n, .is_default = true};
    ESP_LOGI(TAG, "no stored sound: built-in bang, %.1f s", (double)n / SAMPLE_RATE);
    return true;
}

bool sound_erase(void)
{
    const esp_partition_t *p = part();
    return p && esp_partition_erase_range(p, 0, 4096) == ESP_OK && sound_load();
}

bool sound_store(uint32_t total, uint32_t crc, sound_read_fn rd)
{
    const esp_partition_t *p = part();
    if (!p || total < 2 || total > MAX_BYTES || (total & 1)) return false;
    uint32_t erase = (HDR_SIZE + total + 4095) & ~4095u;
    if (esp_partition_erase_range(p, 0, erase) != ESP_OK) return false;
    uint8_t buf[1024];
    uint32_t got = 0, run = 0xFFFFFFFFu;
    while (got < total) {
        size_t want = total - got < sizeof buf ? total - got : sizeof buf;
        int n = rd(buf, want);
        if (n <= 0) { esp_partition_erase_range(p, 0, 4096); return false; }   /* timeout: leave no half-written sound */
        if (esp_partition_write(p, HDR_SIZE + got, buf, (size_t)n) != ESP_OK) return false;
        for (int i = 0; i < n; i++) {                                             /* running CRC */
            run ^= buf[i];
            for (int b = 0; b < 8; b++) run = (run & 1) ? (run >> 1) ^ 0xEDB88320u : run >> 1;
        }
        got += (uint32_t)n;
    }
    if (~run != crc) { esp_partition_erase_range(p, 0, 4096); return false; }
    snd_hdr_t h = {.magic = SND_MAGIC, .rate = SAMPLE_RATE, .samples = total / 2, .crc32 = crc};
    if (esp_partition_write(p, 0, &h, sizeof h) != ESP_OK) return false;     /* header last: the sound is valid only now */
    return sound_load();
}
