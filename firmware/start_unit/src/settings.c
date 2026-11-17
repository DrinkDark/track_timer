#include "settings.h"

#include "esp_log.h"
#include "nvs.h"

#define SETTINGS_VERSION 1u
static const char *TAG = "settings";

settings_t g_cfg;

void settings_defaults(void)
{
    g_cfg = (settings_t){.version = SETTINGS_VERSION, .source = SRC_BOTH, .mute = 0, .cable_on = 1, .radio_on = 1,
                         .edge_rising = 0, .volume = 80, .channel = 1, .heartbeat_s = 0, .lockout_ms = 5000};
}

void settings_load(void)
{
    settings_defaults();
    nvs_handle_t h;
    if (nvs_open("start_unit", NVS_READONLY, &h) != ESP_OK) return;
    settings_t t;
    size_t len = sizeof t;
    if (nvs_get_blob(h, "cfg", &t, &len) == ESP_OK && len == sizeof t && t.version == SETTINGS_VERSION &&
        t.source <= SRC_BOTH && t.volume <= 100 && t.channel >= 1 && t.channel <= 13 && t.lockout_ms >= 500) {
        g_cfg = t;
    } else {
        ESP_LOGW(TAG, "no valid stored settings, using defaults");
    }
    nvs_close(h);
}

bool settings_save(void)
{
    nvs_handle_t h;
    if (nvs_open("start_unit", NVS_READWRITE, &h) != ESP_OK) return false;
    bool ok = nvs_set_blob(h, "cfg", &g_cfg, sizeof g_cfg) == ESP_OK && nvs_commit(h) == ESP_OK;
    nvs_close(h);
    return ok;
}
