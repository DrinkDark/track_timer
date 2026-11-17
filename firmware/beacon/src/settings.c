#include "settings.h"

#include "esp_log.h"
#include "nvs.h"

#define SETTINGS_VERSION 1u
static const char *TAG = "settings";

settings_t g_cfg;

void settings_defaults(void)
{
    g_cfg = (settings_t){.version = SETTINGS_VERSION, .hold_ms = 150, .channel = 1, .selftest = 0,
                         .arm_on_boot = 1, .ref_hz = 23.0f};
}

void settings_load(void)
{
    settings_defaults();
    nvs_handle_t h;
    if (nvs_open("beacon", NVS_READONLY, &h) != ESP_OK) return;
    settings_t tmp;
    size_t len = sizeof tmp;
    if (nvs_get_blob(h, "cfg", &tmp, &len) == ESP_OK && len == sizeof tmp && tmp.version == SETTINGS_VERSION &&
        tmp.hold_ms >= 10 && tmp.hold_ms <= 1000 && tmp.channel >= 1 && tmp.channel <= 13 &&
        tmp.ref_hz >= 1.0f && tmp.ref_hz <= 100.0f) {
        g_cfg = tmp;
    } else {
        ESP_LOGW(TAG, "no valid stored settings, using defaults");
    }
    nvs_close(h);
}

bool settings_save(void)
{
    nvs_handle_t h;
    if (nvs_open("beacon", NVS_READWRITE, &h) != ESP_OK) return false;
    bool ok = nvs_set_blob(h, "cfg", &g_cfg, sizeof g_cfg) == ESP_OK && nvs_commit(h) == ESP_OK;
    nvs_close(h);
    return ok;
}

bool settings_set_hold_ms(int v)
{
    if (v < 10 || v > 1000) return false;
    g_cfg.hold_ms = (uint16_t)v;
    return true;
}

bool settings_set_channel(int v)
{
    if (v < 1 || v > 13) return false;
    g_cfg.channel = (uint8_t)v;
    return true;
}

bool settings_set_ref_hz(float v)
{
    if (!(v >= 1.0f && v <= 100.0f)) return false;
    g_cfg.ref_hz = v;
    return true;
}
