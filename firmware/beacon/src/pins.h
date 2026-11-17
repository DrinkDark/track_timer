#pragma once
/* ESP32-S3 WROOM-1U pin map (change here to match the board).
 * Avoided: strapping pins 0, 3, 45, 46; USB 19, 20; flash/PSRAM 26-37; UART0 console 43, 44. */
#define PIN_LED_START   4   /* gate of the logic-level MOSFET for the 3 W start LED (active high).
                               This is also the "LED gate pin" of the latency calibration (spec section 5). */
#define PIN_LED_REF     5   /* small reference LED, blinks while armed */
#define PIN_LED_LINK    6   /* link indicator: on = radio triggered the flash, off = cable */
#define PIN_LED_STATUS  7   /* status LED */

#define PIN_RS485_TX    17
#define PIN_RS485_RX    18
#define PIN_RS485_DE    8   /* DE and /RE of the transceiver tied together, driven as RTS */
#define RS485_UART      1
#define RS485_BAUD      250000
