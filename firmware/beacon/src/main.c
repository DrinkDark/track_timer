#include "beacon.h"
#include "console.h"
#include "esp_log.h"
#include "links.h"
#include "nvs_flash.h"
#include "settings.h"
#include "status_led.h"

void app_main(void)
{
    esp_err_t r = nvs_flash_init();
    if (r == ESP_ERR_NVS_NO_FREE_PAGES || r == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    settings_load();
    beacon_init();
    links_init();
    status_led_start();
    if (g_cfg.arm_on_boot) beacon_arm(true);
    console_start();
    ESP_LOGI("beacon", "ready: hold %u ms, ref %.1f Hz, channel %u", g_cfg.hold_ms, g_cfg.ref_hz, g_cfg.channel);
}
