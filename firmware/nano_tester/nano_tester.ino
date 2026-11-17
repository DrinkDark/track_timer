/*
 * Nano beacon tester: a stand-in for the beacon (spec section 4) to try the camera and the flash/blink detection
 * before the real hardware exists. Three LEDs, two buttons, no radio and no cable.
 *
 *   start LED      D3   on for HOLD_MS when the start button is pressed (the "flash" = t0 in the video)
 *   reference LED  D9   blinks at REF_HZ all the time; driven by Timer1 hardware (pin D9 is OC1A), so the
 *                       edges have no software jitter
 *   status LED     D5   the link indicator: on = "radio", off = "cable". Button 2 switches it and it stays
 *                       as set. (Set LINK_ONLY_DURING_FLASH to 1 to light it only during the flash, like the
 *                       real beacon.)
 *   start button   D2   to GND, internal pull-up
 *   link button    D4   to GND, internal pull-up
 *
 * Each LED: pin -> 330 ohm -> LED -> GND (about 10 mA; the Nano pins give up to 20 mA).
 * Serial monitor, 115200 baud:  s = start flash   l = switch status LED   t = self-test on/off   h = help
 * Self-test: after each start flash a second flash follows 8-15 s later and the exact interval between the two
 * LED edges is printed, to compare with what the software measures from the video (spec test 7).
 *
 * Caution: the Nano's ceramic resonator is only accurate to about 0.5 %, so the real blink frequency can be
 * off from REF_HZ by that much. Good enough to test the logic; not to judge the frame-rate accuracy.
 */
#include <Arduino.h>

const uint8_t PIN_START_LED = 3;
const uint8_t PIN_REF_LED = 9;           // OC1A: do not change (Timer1 toggles it in hardware)
const uint8_t PIN_STATUS_LED = 5;
const uint8_t PIN_BTN_START = 2;
const uint8_t PIN_BTN_LINK = 4;

const float REF_HZ = 23.0;               // spec: 23 Hz, not a divisor of 120 fps
const uint16_t HOLD_MS = 150;            // about 18 frames at 120 fps
const bool LINK_ONLY_DURING_FLASH = false;
const uint16_t DEBOUNCE_MS = 30;

bool linkRadio = false;                  // status LED state: true = on = radio
bool flashing = false;
bool selfTest = false;
bool secondPending = false;
uint32_t tOn = 0;                        // micros() at the last rising edge of the start LED
uint32_t tFirst = 0, tSecondAt = 0, plannedUs = 0;

struct Button {
  uint8_t pin;
  bool last;                             // true = released (pull-up)
  uint32_t changedMs;
};
Button btnStart = {PIN_BTN_START, true, 0};
Button btnLink = {PIN_BTN_LINK, true, 0};

// True once per press, after debouncing.
bool pressed(Button &b) {
  bool level = digitalRead(b.pin);
  uint32_t now = millis();
  if (level != b.last && now - b.changedMs >= DEBOUNCE_MS) {
    b.last = level;
    b.changedMs = now;
    return !level;
  }
  return false;
}

void showLink() {
  digitalWrite(PIN_STATUS_LED, (linkRadio && (!LINK_ONLY_DURING_FLASH || flashing)) ? HIGH : LOW);
}

void flashOn() {
  digitalWrite(PIN_START_LED, HIGH);     // first thing: the edge the camera will see
  tOn = micros();
  flashing = true;
  showLink();
}

void startFlash() {
  if (flashing) return;                  // ignore a press while the LED is still on
  flashOn();
  Serial.print(F("FLASH t_us=")); Serial.print(tOn);
  Serial.print(F(" link=")); Serial.println(linkRadio ? F("radio") : F("cable"));
  if (selfTest) {
    tFirst = tOn;
    plannedUs = 8000000UL + (uint32_t)random(0, 7000001L);   // 8 to 15 s
    tSecondAt = tFirst + plannedUs;
    secondPending = true;
    Serial.print(F("SELFTEST second flash planned in us=")); Serial.println(plannedUs);
  }
}

void switchLink() {
  linkRadio = !linkRadio;
  showLink();
  Serial.print(F("STATUS LED ")); Serial.println(linkRadio ? F("on (radio)") : F("off (cable)"));
}

void printHelp() {
  Serial.println(F("s start flash | l switch status LED | t self-test on/off | h help"));
  Serial.print(F("ref LED ")); Serial.print(F_CPU / 8.0 / (2.0 * (OCR1A + 1)), 5);
  Serial.print(F(" Hz (nominal, resonator-dependent), hold ")); Serial.print(HOLD_MS);
  Serial.print(F(" ms, self-test ")); Serial.println(selfTest ? F("on") : F("off"));
}

void setup() {
  pinMode(PIN_START_LED, OUTPUT);
  pinMode(PIN_STATUS_LED, OUTPUT);
  pinMode(PIN_REF_LED, OUTPUT);
  pinMode(PIN_BTN_START, INPUT_PULLUP);
  pinMode(PIN_BTN_LINK, INPUT_PULLUP);

  // Timer1 in CTC mode, prescaler 8 (0.5 us per tick), OC1A (D9) toggles on every compare match:
  // one toggle per half period, so the LED blinks at REF_HZ with no interrupt and no jitter.
  TCCR1A = _BV(COM1A0);
  TCCR1B = _BV(WGM12) | _BV(CS11);
  OCR1A = (uint16_t)(F_CPU / 8.0 / (2.0 * REF_HZ) + 0.5) - 1;   // 43477 for 23 Hz
  TCNT1 = 0;

  Serial.begin(115200);
  randomSeed(analogRead(A0) ^ micros());
  showLink();
  Serial.println(F("Nano beacon tester"));
  printHelp();
}

void loop() {
  if (pressed(btnStart)) startFlash();
  if (pressed(btnLink)) switchLink();

  uint32_t now = micros();
  if (flashing && now - tOn >= (uint32_t)HOLD_MS * 1000UL) {
    digitalWrite(PIN_START_LED, LOW);
    flashing = false;
    showLink();
  }
  if (secondPending && !flashing && (int32_t)(now - tSecondAt) >= 0) {
    secondPending = false;
    flashOn();
    Serial.print(F("SELFTEST interval_us=")); Serial.print(tOn - tFirst);
    Serial.print(F(" planned_us=")); Serial.print(plannedUs);
    Serial.print(F(" interval_s=")); Serial.println((tOn - tFirst) / 1e6, 6);
  }

  while (Serial.available()) {
    char c = Serial.read();
    if (c == 's') startFlash();
    else if (c == 'l') switchLink();
    else if (c == 't') { selfTest = !selfTest; if (!selfTest) secondPending = false; printHelp(); }
    else if (c == 'h' || c == '?') printHelp();
  }
}
