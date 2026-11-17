#pragma once
/* ESP32-S3 WROOM-1U pin map (change here to match the board).
 * Avoided: strapping pins 0, 3, 45, 46; USB 19, 20; flash/PSRAM 26-37; UART0 console 43, 44. */
#define PIN_PISTOL      1   /* 6N137 output (open collector, pulled up): the pistol pulse. Falling edge by default. */
#define PIN_BUTTON      2   /* start button and remote dry-contact jack in parallel, active low, internal pull-up */
#define PIN_ARM_SWITCH  12  /* arm switch, active low (switch closed = armed). Internal start needs it on. */
#define PIN_TEST_TRIG   13  /* goes high in the trigger interrupt: the "trigger test pin" for the logic analyzer
                               (spec sections 5 and 9, tests 2-4); cleared 200 us later */
#define PIN_LED_STATUS  7
#define PIN_CAM_TRIG    14  /* camera trigger output (option, spec section 11): rising edge = one frame.
                               3.3 V logic: check the level the camera board expects before connecting. */

#define PIN_I2S_BCK     9   /* PCM5102A: BCK, LRCK(WS), DIN. SCK tied low (internal PLL), no MCLK needed. */
#define PIN_I2S_WS      10
#define PIN_I2S_DOUT    11

#define PIN_RS485_TX    17
#define PIN_RS485_RX    18
#define PIN_RS485_DE    8   /* DE and /RE of the transceiver tied together, driven as RTS */
#define RS485_UART      1
#define RS485_BAUD      250000

#define SAMPLE_RATE     44100
