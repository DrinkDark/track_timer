/*
 * Audio out (PCM5102A over I2S). The I2S clocks run all the time, so the DAC stays locked and playback can
 * start within a couple of DMA chunks of a trigger. A high-priority task refills tiny DMA buffers with silence,
 * or with the pistol sound after a play request. The mono sound is copied to both channels while filling.
 */
#include "audio.h"

#include <string.h>

#include "driver/i2s_std.h"
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "pins.h"
#include "settings.h"
#include "sound.h"

#define CHUNK_FRAMES 16     /* 0.36 ms per chunk; the I2S DMA holds DMA_BUFS of them (to be tuned, spec test 2) */
#define DMA_BUFS 3

static const char *TAG = "audio";
static i2s_chan_handle_t s_tx;
static volatile bool s_req, s_playing;
static volatile int64_t s_req_us, s_req_done_us, s_handoff_us;
static uint32_t s_pos;

void IRAM_ATTR audio_play_from_isr(void)
{
    s_req_us = esp_timer_get_time();
    s_req = true;
}

void audio_last_handoff(int64_t *req_us, int64_t *handoff_us)
{
    *req_us = s_req_done_us;
    *handoff_us = s_handoff_us;
}

bool audio_playing(void) { return s_playing; }

static void audio_task(void *arg)
{
    (void)arg;
    int16_t frame[CHUNK_FRAMES * 2];
    for (;;) {
        bool first = false;
        if (s_req) {
            s_req = false;
            s_pos = 0;
            s_playing = true;
            first = true;
            s_req_done_us = s_req_us;
        }
        const sound_t *snd = sound_get();
        int vol = (g_cfg.volume * 256) / 100;
        for (int i = 0; i < CHUNK_FRAMES; i++) {
            int16_t v = 0;
            if (s_playing && snd->pcm && s_pos < snd->samples) v = (int16_t)((snd->pcm[s_pos++] * vol) >> 8);
            else if (s_playing) s_playing = false;
            frame[2 * i] = v;
            frame[2 * i + 1] = v;
        }
        size_t written;
        i2s_channel_write(s_tx, frame, sizeof frame, &written, portMAX_DELAY);
        if (first) s_handoff_us = esp_timer_get_time();
    }
}

void audio_init(void)
{
    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    cc.dma_desc_num = DMA_BUFS;
    cc.dma_frame_num = CHUNK_FRAMES;
    cc.auto_clear = true;
    ESP_ERROR_CHECK(i2s_new_channel(&cc, &s_tx, NULL));
    i2s_std_config_t sc = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {.mclk = I2S_GPIO_UNUSED, .bclk = PIN_I2S_BCK, .ws = PIN_I2S_WS, .dout = PIN_I2S_DOUT,
                     .din = I2S_GPIO_UNUSED},
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(s_tx, &sc));
    ESP_ERROR_CHECK(i2s_channel_enable(s_tx));
    xTaskCreatePinnedToCore(audio_task, "audio", 4096, NULL, configMAX_PRIORITIES - 3, NULL, 1);
    ESP_LOGI(TAG, "I2S running: %d x %d frames of DMA (%.2f ms per chunk)", DMA_BUFS, CHUNK_FRAMES,
             1000.0 * CHUNK_FRAMES / SAMPLE_RATE);
}
