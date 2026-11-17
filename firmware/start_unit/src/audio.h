#pragma once
#include <stdbool.h>
#include <stdint.h>

void audio_init(void);
/* Requests playback from the start of the sound. Safe from an interrupt; the writer task picks it up
 * at the next DMA chunk (at most 2-3 chunks of about 0.36 ms each). */
void audio_play_from_isr(void);
/* Time at which the first chunk with sound was handed to the I2S DMA, and the request time it belongs to. */
void audio_last_handoff(int64_t *req_us, int64_t *handoff_us);
bool audio_playing(void);
