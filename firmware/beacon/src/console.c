/* Serial console on the USB port: settings, status and test commands (spec section 4, points 4 and 5). */
#include "console.h"

#include <stdio.h>
#include <string.h>

#include "beacon.h"
#include "driver/usb_serial_jtag.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "links.h"
#include "ref_led.h"
#include "settings.h"

static void print_status(void)
{
    int64_t now = esp_timer_get_time();
    printf("$STATUS uptime_us=%lld armed=%d flash_on=%d selftest=%d selftest_pending=%d hold_ms=%u ref_hz=%.3f "
           "ref_actual_hz=%.5f channel=%u starts=%lu dups=%lu\n",
           (long long)now, beacon_is_armed(), beacon_flash_active(), g_cfg.selftest, beacon_selftest_pending(),
           g_cfg.hold_ms, g_cfg.ref_hz, ref_led_actual_hz(), g_cfg.channel, (unsigned long)g_stats.starts,
           (unsigned long)g_stats.dups);
    const char *n[2] = {"cable", "radio"};
    for (int i = 0; i < 2; i++) {
        long long ago = g_stats.last_rx_us[i] ? (now - g_stats.last_rx_us[i]) / 1000 : -1;
        printf("$LINK %s rx=%lu bad=%lu last_rx_ms_ago=%lld\n", n[i], (unsigned long)g_stats.rx[i],
               (unsigned long)g_stats.bad[i], ago);
    }
}

static void help(void)
{
    puts("commands:\n"
         "  status              state, settings and packet counters\n"
         "  arm | disarm        reference LED blinking on/off (the start LED always reacts to a start)\n"
         "  fire                local test flash, same path as a start packet\n"
         "  selftest on|off     second flash 8-15 s after the first; prints $SELFTEST with the exact interval\n"
         "  hold <ms>           start LED on-time, 10-1000 (default 150)\n"
         "  ref <hz>            reference LED frequency, 1-100 (default 23)\n"
         "  ch <1-13>           ESP-NOW channel (must match the start unit)\n"
         "  save | defaults     store settings in flash | restore factory values (then save)\n"
         "  reboot");
}

static void handle(char *line)
{
    char a[16] = "", b[16] = "";
    int n = sscanf(line, "%15s %15s", a, b);
    if (n < 1) return;
    if (!strcmp(a, "help") || !strcmp(a, "?")) help();
    else if (!strcmp(a, "status")) print_status();
    else if (!strcmp(a, "arm")) { beacon_arm(true); puts("armed"); }
    else if (!strcmp(a, "disarm")) { beacon_arm(false); puts("disarmed"); }
    else if (!strcmp(a, "fire")) beacon_fire_local();
    else if (!strcmp(a, "selftest") && n == 2) { g_cfg.selftest = !strcmp(b, "on"); printf("selftest %s\n", g_cfg.selftest ? "on" : "off"); }
    else if (!strcmp(a, "hold") && n == 2) puts(settings_set_hold_ms(atoi(b)) ? "ok" : "refused: 10-1000");
    else if (!strcmp(a, "ref") && n == 2) {
        if (settings_set_ref_hz(strtof(b, NULL))) { if (beacon_is_armed()) beacon_arm(true); puts("ok"); }
        else puts("refused: 1-100");
    }
    else if (!strcmp(a, "ch") && n == 2) {
        if (settings_set_channel(atoi(b))) { links_set_channel(g_cfg.channel); puts("ok"); } else puts("refused: 1-13");
    }
    else if (!strcmp(a, "save")) puts(settings_save() ? "saved" : "save failed");
    else if (!strcmp(a, "defaults")) { settings_defaults(); puts("defaults loaded (not saved)"); }
    else if (!strcmp(a, "reboot")) esp_restart();
    else puts("unknown command, type help");
}

static void console_task(void *arg)
{
    (void)arg;
    char line[64];
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
