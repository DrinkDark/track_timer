# Firmware: beacon and start unit

ESP32-S3 (WROOM-1U) firmware for the two field units of the spec (sections 3 to 5). ESP-IDF 5.3 through PlatformIO.

| Folder | What |
| --- | --- |
| `common/beacon_protocol.h` | the 9-byte link packet, CRC-8, stream re-sync and duplicate filter (plain C, shared) |
| `beacon/` | beacon: start LED, reference LED, link indicator, self-test (spec section 4) |
| `start_unit/` | start unit: pistol input, sound out, start packet on cable and radio (spec section 3) |
| `tools/beacon_link.py` | PC side of the protocol; sends start/ping through a USB-RS485 adapter for bench tests |
| `tools/upload_sound.py` | loads the pistol sound into the start unit |
| `test/test_protocol.c` | host test of the protocol header (`cc -I common test/test_protocol.c && ./a.out`) |

## Build and flash
    pip install platformio
    cd beacon      && pio run -t upload && pio device monitor     # or: cd start_unit
Both open a serial console on the native USB port; type `help`. Pin numbers are in `src/pins.h` of each unit
(they are a proposal: avoid strapping, USB and flash pins; change them to match the board).

Both firmwares compile (checked with PlatformIO, espressif32 6.10.0). **Nothing has run on hardware yet.**

## Link protocol (spec section 5)
`A5 | type | seq(2, LE) | start-unit time in us (4, LE) | CRC-8`. Type 1 start, 2 ping, 3 pong.
CRC-8 uses polynomial 0x07, initial value 0, no reflection (the spec did not fix it); check value for
`"123456789"` is `0xF4`. Cable: RS485, 250 kbaud 8N1. Radio: ESP-NOW **broadcast** on a fixed channel (default 1,
setting `ch` on both units); no pairing, no encryption, so any device that sends a valid packet starts the flash.

## Beacon behaviour
* Both links are listened to all the time. The first start packet with a new sequence number switches the start LED on
  in the receiving task, sets the link indicator (on = radio, off = cable) and holds both for `hold` ms (150).
  Copies are dropped. A sequence number counts as a copy only within 2 s, so a start unit that rebooted (counter back
  at 0) is never ignored.
* A new start while the LED is still on only extends the hold (`$RETRIGGER`).
* The reference LED blinks from a hardware timer (23 Hz; half period 21739 us, 6 ppm from exact; `status` shows
  the actual value). `arm`/`disarm` start and stop it; it is armed after boot. The start LED reacts even when disarmed.
* Self-test (`selftest on`): 8 to 15 s after the first flash (microsecond resolution) a second flash is made from the timer
  interrupt and `$SELFTEST ... interval_us=` prints the exact interval between the two LED edges. `fire` makes a local first flash
  for a bench test without a start unit.
* Status LED, per 2 s cycle: off = disarmed; pulses = 1 (armed) + 1 (a valid packet in the last 15 s) + 1 (self-test on);
  solid while the start LED is on.
* Serial log, one line per event with microsecond timestamps since boot: `$PKT`, `$FLASH_ON`, `$FLASH_OFF`, `$RETRIGGER`,
  `$SELFTEST`, `$STATUS`, `$LINK`. The LED gate pin (`PIN_LED_START`) is the pin to probe for the latency calibration.
* A ping is answered with a pong on the link it came from, for range tests.

## Start unit behaviour
* Boot: loads the pistol sound from the `sound` flash partition into RAM (a built-in synthetic bang is used until one is uploaded),
  starts the I2S clocks (they run all the time so the DAC stays locked) and arms.
* Trigger: a GPIO interrupt on the pistol pin (falling edge by default, `edge rise` to change) or on the button
  (needs the arm switch on) timestamps the edge, raises the test pin `PIN_TEST_TRIG`, requests the sound and queues the start packet at
  the front of the transmit queue: cable first, then radio, same sequence number. Triggers are ignored for `lockout` ms (5000).
* `source pistol|button|both`, `mute on|off` (signals without sound), `volume`, `cable on|off`, `radio on|off`, all saved with `save`.
* The remote dry-contact jack is wired in parallel with the button (the spec also mentions the pistol input; either works electrically).
* `ping [n]` sends n pings on both links and prints loss and round trip (`$PING`) for range checks (spec test 4).
* `heartbeat <s>` sends a ping every s seconds so the beacon status LED can show "link seen". It is **off** by default: a heartbeat
  that overlaps a start would delay it by up to 0.36 ms on the cable.
* `$TRIG` logs `audio_handoff_us`, the time from the interrupt until the first chunk with sound was given to the I2S DMA.
  `$TX` logs `isr_to_tx_us`, the time the start packet waited for the transmit task.
* Upload a sound: `python tools/upload_sound.py <port> pistol.wav` (mono, 16 bit, 44.1 kHz, up to about 2.9 s).

## Not verified, to measure on the bench (spec section 9)
* **Audio latency and spread (test 2).** The I2S DMA holds 3 chunks of 16 frames (0.36 ms each), so the sound can start about
  0.4 to 1.1 ms after the trigger, spread under 1 ms in theory. `DMA_BUFS` and `CHUNK_FRAMES` in `audio.c` are the knobs.
  The PCM5102A output is stereo: the mono sound is copied to both channels.
* **Cable and radio latency (tests 3 and 4).** The start packet goes out from a high-priority task, not from the interrupt.
  If the cable spread is above 0.2 ms, write the 9 bytes to the UART FIFO inside the interrupt.
* ESP-NOW broadcast reception on the beacon and the 250 kbaud RS485 link over 100 m of cable.
* The beacon's start LED switch-on is a `gpio_ll_set_level` call in the receiving task (a microsecond or two after reception).
* The input stage of the pistol pulse (spec test 1) decides the edge, the filtering and whether a glitch filter is needed.
