#include <driver/gpio.h>

#include "audio.h"
#include "camtrig.h"
#include "console.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "links.h"
#include "nvs_flash.h"
#include "pins.h"
#include "settings.h"
#include "sound.h"
#include "trigger.h"

/* Status LED: solid while locked out after a start; otherwise a pulse every 2 s when armed (one more pulse
 * when muted). Internal-only mode with the arm switch off shows as dark. */
static void status_task(void *arg)
{
    (void)arg;
    for (;;) {
        int phase = (int)((esp_timer_get_time() / 1000) % 2000);
        bool usable = g_cfg.source == SRC_PISTOL || trigger_arm_switch_on();
        int pulses = !usable ? 0 : 1 + (g_cfg.mute ? 1 : 0);
        bool on = trigger_in_lockout() || (phase / 250 < pulses && phase % 250 < 100);
        gpio_set_level(PIN_LED_STATUS, on);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void app_main(void)
{
    esp_err_t r = nvs_flash_init();
    if (r == ESP_ERR_NVS_NO_FREE_PAGES || r == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    settings_load();
    sound_load();       /* into RAM before anything can trigger */
    audio_init();
    links_init();
    trigger_init();
    camtrig_init();
    xTaskCreate(status_task, "status", 2048, NULL, 2, NULL);
    console_start();
    ESP_LOGI("start_unit", "armed: source %d, lockout %u ms, channel %u", g_cfg.source, g_cfg.lockout_ms, g_cfg.channel);
}
