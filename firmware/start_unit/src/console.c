/* Serial console on the USB port: settings, status and test commands (spec section 3, firmware point 6). */
#include "console.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "audio.h"
#include "driver/usb_serial_jtag.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "links.h"
#include "settings.h"
#include "sound.h"
#include "trigger.h"

static void print_status(void)
{
    static const char *src[] = {"pistol", "button", "both"};
    const sound_t *s = sound_get();
    printf("$STATUS uptime_us=%lld armed=%d lockout=%d arm_switch=%d source=%s mute=%d volume=%u lockout_ms=%u "
           "cable=%d radio=%d channel=%u edge=%s heartbeat_s=%u triggers=%lu ignored=%lu sound=%s sound_ms=%.0f\n",
           (long long)esp_timer_get_time(), trigger_armed(), trigger_in_lockout(), trigger_arm_switch_on(),
           src[g_cfg.source], g_cfg.mute, g_cfg.volume, g_cfg.lockout_ms, g_cfg.cable_on, g_cfg.radio_on,
           g_cfg.channel, g_cfg.edge_rising ? "rising" : "falling", g_cfg.heartbeat_s,
           (unsigned long)trigger_count(), (unsigned long)trigger_ignored(), s->is_default ? "built-in" : "stored",
           s->samples * 1000.0 / 44100.0);
    printf("$LINKS cable_sent=%lu radio_sent=%lu cable_pongs=%lu radio_pongs=%lu\n", (unsigned long)g_link_stats.sent[0],
           (unsigned long)g_link_stats.sent[1], (unsigned long)g_link_stats.pongs[0],
           (unsigned long)g_link_stats.pongs[1]);
}

static void help(void)
{
    puts("commands:\n"
         "  status                       state, settings and counters\n"
         "  source pistol|button|both    what starts a race (button needs the arm switch on)\n"
         "  mute on|off                  send the signals without sound\n"
         "  volume <0-100>               sound level\n"
         "  lockout <ms>                 ignore triggers after a start, 500-60000 (default 5000)\n"
         "  cable on|off | radio on|off  enable each link\n"
         "  edge fall|rise               pistol pulse edge (6N137 output: falling)\n"
         "  ch <1-13>                    ESP-NOW channel (must match the beacon)\n"
         "  heartbeat <s>                ping every s seconds so the beacon shows 'link seen'; 0 = off\n"
         "  fire                         software trigger: sound + start packets, like a pistol pulse\n"
         "  play                         play the sound locally only (volume check)\n"
         "  ping [n]                     n pings per link, prints loss and round trip (range test)\n"
         "  sound upload <bytes> <crc32> raw 16-bit mono 44.1 kHz PCM follows (use tools/upload_sound.py)\n"
         "  sound default                forget the stored sound, use the built-in one\n"
         "  save | defaults | reboot");
}

static int usb_read(uint8_t *buf, size_t want)
{
    size_t got = 0;
    int idle = 0;
    while (got < want && idle < 50) {            /* 5 s without data: give up */
        int n = usb_serial_jtag_read_bytes(buf + got, want - got, pdMS_TO_TICKS(100));
        if (n > 0) { got += (size_t)n; idle = 0; } else idle++;
    }
    return got ? (int)got : -1;
}

static void handle(char *line)
{
    char a[16] = "", b[16] = "", c[16] = "";
    int n = sscanf(line, "%15s %15s %15s", a, b, c);
    if (n < 1) return;
    bool on = !strcmp(b, "on");
    if (!strcmp(a, "help") || !strcmp(a, "?")) help();
    else if (!strcmp(a, "status")) print_status();
    else if (!strcmp(a, "source") && n >= 2) {
        if (!strcmp(b, "pistol")) g_cfg.source = SRC_PISTOL; else if (!strcmp(b, "button")) g_cfg.source = SRC_BUTTON;
        else if (!strcmp(b, "both")) g_cfg.source = SRC_BOTH; else { puts("pistol|button|both"); return; }
        puts("ok");
    }
    else if (!strcmp(a, "mute") && n >= 2) { g_cfg.mute = on; puts("ok"); }
    else if (!strcmp(a, "volume") && n >= 2) { int v = atoi(b); if (v < 0 || v > 100) puts("refused: 0-100"); else { g_cfg.volume = (uint8_t)v; puts("ok"); } }
    else if (!strcmp(a, "lockout") && n >= 2) { int v = atoi(b); if (v < 500 || v > 60000) puts("refused: 500-60000"); else { g_cfg.lockout_ms = (uint16_t)v; puts("ok"); } }
    else if (!strcmp(a, "cable") && n >= 2) { g_cfg.cable_on = on; puts("ok"); }
    else if (!strcmp(a, "radio") && n >= 2) { g_cfg.radio_on = on; puts("ok"); }
    else if (!strcmp(a, "edge") && n >= 2) { g_cfg.edge_rising = !strcmp(b, "rise"); trigger_set_edge(g_cfg.edge_rising); puts("ok"); }
    else if (!strcmp(a, "ch") && n >= 2) { int v = atoi(b); if (v < 1 || v > 13) puts("refused: 1-13"); else { g_cfg.channel = (uint8_t)v; links_set_channel(v); puts("ok"); } }
    else if (!strcmp(a, "heartbeat") && n >= 2) { int v = atoi(b); if (v < 0 || v > 255) puts("refused: 0-255"); else { g_cfg.heartbeat_s = (uint8_t)v; puts("ok"); } }
    else if (!strcmp(a, "fire")) trigger_fire_software();
    else if (!strcmp(a, "play")) audio_play_from_isr();
    else if (!strcmp(a, "ping")) links_ping_test(n >= 2 ? atoi(b) : 20);
    else if (!strcmp(a, "sound") && n >= 2 && !strcmp(b, "default")) puts(sound_erase() ? "built-in sound in use" : "failed");
    else if (!strcmp(a, "sound") && n >= 3 && !strcmp(b, "upload")) {
        char len[16], crc[16];
        if (sscanf(line, "%*s %*s %15s %15s", len, crc) != 2) { puts("usage: sound upload <bytes> <crc32 hex>"); return; }
        puts("READY");
        fflush(stdout);
        bool ok = sound_store((uint32_t)strtoul(len, NULL, 10), (uint32_t)strtoul(crc, NULL, 16), usb_read);
        printf(ok ? "$SOUND stored samples=%lu\n" : "$SOUND failed\n", (unsigned long)sound_get()->samples);
    }
    else if (!strcmp(a, "save")) puts(settings_save() ? "saved" : "save failed");
    else if (!strcmp(a, "defaults")) { settings_defaults(); puts("defaults loaded (not saved)"); }
    else if (!strcmp(a, "reboot")) esp_restart();
    else puts("unknown command, type help");
}

static void console_task(void *arg)
{
    (void)arg;
    char line[80];
    help();
    for (;;) {
        printf("> ");
        fflush(stdout);
        if (!fgets(line, sizeof line, stdin)) { vTaskDelay(pdMS_TO_TICKS(50)); continue; }
        line[strcspn(line, "\r\n")] = 0;
        handle(line);
    }
}

void console_start(void)
{
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    usb_serial_jtag_driver_install(&cfg);
    usb_serial_jtag_vfs_use_driver();
    usb_serial_jtag_vfs_set_rx_line_endings(ESP_LINE_ENDINGS_CR);
    usb_serial_jtag_vfs_set_tx_line_endings(ESP_LINE_ENDINGS_CRLF);
    setvbuf(stdin, NULL, _IONBF, 0);
    xTaskCreate(console_task, "console", 4096, NULL, 1, NULL);
}
