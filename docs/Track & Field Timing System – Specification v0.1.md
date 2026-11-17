# Track & Field Timing System – Specification v0.1

Nov 16, 2026 · @arey

## 1. Scope and goals

The system times 100 m races on 2 to 4 lanes. The field target is ±0.005 s (club level); the prototype accepts about ±0.01 s.

- **Start:** an electric pistol pulse triggers a recorded pistol sound, played through an external amplifier, and a start signal sent over cable and radio.
- **Finish:** a beacon flashes an LED in the camera view at t0. A laptop records the finish line.
- **Analysis:** the web UI builds a photo-finish image, proposes a finish time per lane, and lets the operator correct it.
- **Output:** end-of-session results (CSV, PDF) for up to 100 athletes, from an imported start list.

| Phase | Camera | One frame |
| --- | --- | --- |
| M1 prototype | OV9281 USB, global shutter, 120 fps | 8.3 ms |
| M2/M3 field | Global-shutter camera, 200+ fps | 5 ms or less |

**Non-goals in v0.1:** official certification, live results display, multi-camera, bib-number OCR, false-start detection, timing from the gun sound.

## 2. System architecture

&#91;embedded content: system architecture · 8 parts, 2 links\]

The start unit sends the start signal over cable and radio to the beacon, which flashes in the camera view; the laptop times everything from the video.

## 3. Start unit

The start unit turns one pistol pulse into a sound and a start signal within a few milliseconds.

| Block | Part | Note |
| --- | --- | --- |
| Controller | ESP32-S3 (WROOM-1U, PSRAM, u.FL antenna) | GPIO interrupt plus hardware timer |
| Pistol input | 6N137 optocoupler, TVS diode, jumper-selectable series resistor | Values fixed after the pistol is measured; also accepts a dry contact |
| Audio | PCM5102A I2S DAC, 3.5 mm line out | Feeds the external amplifier; optional 1:1 isolation transformer against hum |
| Cable link | RS485 transceiver, 3-pin XLR, shielded twisted pair | 100 m, 250 kbaud |
| Radio link | ESP-NOW, external 2.4 GHz antenna | nRF24 PA/LNA module as fallback |
| Power | USB-C power bank, 5 V | Separate from the amplifier supply |
| Start button | Push button and arm switch on the unit, plus a jack for a remote dry-contact button | Turns the start unit into the pistol (internal start mode) |

**Firmware behaviour**

1. At boot the unit loads the pistol sound (mono WAV, 16 bit, 44.1 kHz, about 2 s, roughly 176 KB) into RAM and prepares the I2S DMA buffer, so playback needs no file access at trigger time.
2. The unit is armed after boot and after each lockout. Triggers are ignored during the lockout (default 5 s), so contact bounce or a second pulse cannot restart a race.
3. On a pulse edge, the interrupt handler reads the hardware timer, starts I2S playback and queues the start packet. Target: first audio sample within 2 ms of the pulse (to be measured, section 9).
4. A high-priority task sends the packet over RS485 and over ESP-NOW, with the same sequence number (section 5).
5. A start source setting selects the pistol input, the internal start button, or both. In internal mode the start unit is the pistol: the button fires the same sequence (sound plus start signal) as a pistol pulse, and the arm switch must be on, so it cannot fire by accident. A remote dry-contact button can be plugged into the pistol input to start from a distance. A mute option sends the signals without sound, for calibration or for a pistol that makes its own sound.
6. Settings (sound file, volume, lockout, each link on or off) are changed over USB serial.

**Zero time.** t0 is the first audio sample leaving the DAC, which is the trigger time plus the measured audio latency L\_audio. In internal mode the trigger is the button edge, captured the same way.

## 4. Beacon

The beacon stands in the camera view next to the finish line and turns the start signal into light.

| Block | Part |
| --- | --- |
| Controller | ESP32-S3, same module as the start unit |
| Start LED | 3 W white LED on a star board, logic-level MOSFET, current limit |
| Reference LED | Small LED, in a different image area from the start LED |
| Link indicator | Small LED: on = radio triggered the flash, off = cable |
| Links | RS485 transceiver (XLR), ESP-NOW with external antenna |
| Power, case | USB-C power bank, IP65 box, plate to aim the LEDs at the camera |

**Firmware behaviour**

1. Both links are listened to at all times. The first valid packet with a new sequence number triggers the flash. Later copies of the same sequence number are dropped.
2. On trigger, the handler switches the start LED on within microseconds, sets the link indicator for the link that arrived first, and holds the LED on for 150 ms (about 18 frames at 120 fps). The leading edge of the LED is the start marker in the video.
3. While armed, the reference LED toggles from a hardware timer at 23 Hz (default, configurable). 23 Hz is not a divisor of 120 fps, so the edges fall on varying frame phases and the step pattern is easy to check; a 20 Hz blinker (exactly 3 frames) caused false dropped-frame alarms. The software uses these edges to measure the real frame rate and to detect dropped frames (section 6).
4. A self-test mode fires a second flash of the start LED a random 8 to 15 s after the first, and logs the exact interval over USB serial. This tests the whole chain against a known interval (section 9).
5. A status LED shows armed, link seen and self-test. USB serial logs every packet with its arrival time.

## 5. Link protocol and latency calibration

Both links carry the same 9-byte packet, so the beacon can act on whichever arrives first.

| Field | Size | Meaning |
| --- | --- | --- |
| Magic | 1 B | 0xA5 |
| Type | 1 B | 1 = start, 2 = ping, 3 = pong |
| Sequence | 2 B | Increases with each trigger or ping |
| Start-unit time | 4 B | Microsecond timer value at the trigger |
| CRC | 1 B | CRC-8 over the first 8 bytes |

**Latency model.** The flash appears at the trigger time plus the link latency L\_link plus the LED switching time L\_LED, while t0 is the trigger time plus the audio latency L\_audio. The flash therefore lags t0 by one constant per link:

```latex
\delta_{link} = L_{link} + L_{LED} - L_{audio}
```

The link indicator LED tells the software which constant (cable or radio) to apply.

**Calibration procedure**

1. Audio: with a logic analyzer, measure the delay from the trigger test pin to the first non-zero sample at the DAC output. Use 1000 triggers and keep the median and the spread.
2. Cable: measure from the start unit's trigger test pin to the beacon's LED gate pin with only the cable connected. Use 1000 triggers.
3. Radio: repeat with only the radio, at 10, 50, 100 and 200 m.
4. Accept a link for timing only if its spread (max minus min) is at most 0.2 ms for the cable and 2 ms for the radio.
5. Store L\_audio, L\_cable, L\_radio, L\_LED and the date in calibration.json on the laptop. The web UI shows these values and warns when the file is missing.

**Speaker position.** t0 is defined at the speaker output, not at the athletes' ears. Place the speaker at about equal distance from every lane.

## 6. Camera and capture

The prototype camera is an Arducam B0332 (OV9281, global shutter, mono, USB 2.0, UVC), which lists MJPG at 120 fps for 1280 × 800. Verify the frame rate on your laptop before relying on it.

**Mounting**

- Tripod beside the track, on the extension of the finish line, aimed across the track and 1.5 to 2 m above the ground. The height separates the lanes in the image and reduces how much a near lane hides a far one.
- Sensor rotated 90° so its long side (1280 px) is vertical. The software rotates the frame back.
- The finish-line plane falls on one image column. The UI draws it as an overlay and lets the operator shift it by a few pixels.
- The beacon stands at the edge of the image, outside the athletes' path, so neither LED is ever hidden.

**Lens.** The supplied 70° M12 lens is too wide, so swap it. The focal length follows from the distance to the farthest lane and the height that must be covered:

```latex
f \approx \frac{D_{far} \times h_{sensor}}{H_{scene}}
```

Example: D\_far = 13 m, H\_scene = 2.5 m, h\_sensor = 3.84 mm (1280 px × 3 µm; check the datasheet) gives f of about 20 mm.

**Exposure and light**

- Manual exposure, gain and white balance, set through the UVC controls. Start at 2 to 4 ms. Auto exposure stays off so the flash detector sees a stable baseline.
- Indoor lighting on 50 Hz mains flickers at 100 Hz and shows as brightness banding. Record a test clip and adjust exposure or lighting.

**Capture pipeline**

1. A capture agent on the laptop host requests MJPG 1280 × 800 at 120 fps (V4L2 on Linux, DirectShow or Media Foundation on Windows, AVFoundation on macOS). It keeps the compressed frames untouched in a 10 s RAM ring buffer (configurable), with the host arrival time of each frame for diagnostics only. Host time is never used for race times.
2. A flash detector decodes only a small region around the start LED and compares its mean brightness with a running baseline. The first of two consecutive frames above the threshold is n\_flash. The link indicator is read on the same frame.
3. After the flash, recording continues until t0 + 40 s (configurable). The window from t0 − 2 s to the end is written as one MJPEG file (MKV or AVI, no re-encoding) with a JSON sidecar: n\_flash, link used, measured fps, calibration snapshot, camera settings.
4. Frame-timing check: the reference LED edge frame numbers must follow a straight line (least squares); its slope gives the real frame rate, and a step larger than 0.68 of a period above the expected value marks a dropped frame. Any such gap marks the race with a frame-timing warning and shows where it happened. The real frame rate is computed from the reference LED over the whole window and used in the time formula (section 8).

## 7. Software

A Python backend on the laptop captures, detects and serves a web UI. Backend and browser run on the same laptop (localhost). Other devices on the local WiFi can open the same UI if wanted.

**Proposed stack:** Python 3.11+, FastAPI, OpenCV and NumPy, SQLite, ffmpeg for muxing; a vanilla JavaScript web UI with canvas for the photo-finish strip. The backend and UI run in a Docker container (Linux, Windows, macOS); a small capture agent runs on the host, reads the USB camera and streams JPEG frames to the backend over a WebSocket (/ws/ingest). USB cameras cannot be passed into containers on Windows and macOS; on Linux the agent can also run in a container with /dev/video0. A built-in simulator (TIMING\_SOURCE=sim) replaces the camera for tests.

| Module | Role |
| --- | --- |
| capture | Camera thread, ring buffer, flash detector, recorder (section 6) |
| timing | Converts frame numbers to seconds with the measured fps and calibration |
| strip | Builds the photo-finish image from the finish-line column of every frame |
| detect | Proposes a finish frame per lane with a confidence score |
| storage | SQLite database plus one folder per race (video, sidecar, strip) |
| export | CSV and PDF results |

**Photo-finish strip.** For each frame, take the finish-line column (optionally 3 px wide, averaged). Place the columns side by side: the x axis is time (one column per frame), the y axis is the height of the scene. The UI shows it with a time ruler in milliseconds, zoom and a cursor that opens the matching full frame.

**Auto-detection (version 1, classical image processing)**

1. Build a per-pixel background of the empty line column from the frames before the first runner arrives.
2. Mark foreground pixels per frame (difference to background above an adaptive threshold, then morphological clean-up) and group them into vertical segments.
3. The finish moment is the first frame in which the torso region of a segment reaches the line, not the head, arms, hands or feet. The torso region is the middle part of the segment's height (default 35% to 75% above the feet, adjustable). The crossing is refined between frames from the covered fraction.
4. Lane assignment: the operator draws a vertical band per lane once per setup. A segment belongs to the lane band that contains its lowest point (the feet).
5. Confidence is lowered when a lane has several candidates within 1 s, none at all, a segment that is too small, or two lanes overlapping in the image. Low-confidence results are flagged for review. Places follow from the times, and gaps under one frame are flagged as ties.

**Single-screen layout**

&#91;embedded content: single-screen layout · 4 panels\]

The race list selects what the other panels show. Live capture keeps running while an earlier race is reviewed, so the next race is never missed.

1. **Race list:** previous and current races with their recorded times, and the next race with only its list of athletes, in a compact form (from the imported start list). Selecting a race loads it in the other panels. Session setup and start-list CSV import (bib, name, club, category, heat, lane) are in a menu.
2. **Live capture:** camera preview with overlays for the finish column, the start-LED region and the reference-LED region, plus the arm and stop buttons. Status: measured fps, reference blinker OK, calibration loaded, last radio and cable packet.
3. **Race review:** the strip with a draggable marker per lane, the frame viewer and the athlete list. Keys: left and right arrows step one frame, shift steps ten.
4. **Results:** table for the selected race (place, lane, bib, name, time, status), editable; lock a race when it is final; export.
5. **Calibration:** opens as a dialog over the screen and shows calibration.json, the last self-test result and its error.

**Correction tools.** Move a marker; add a missing marker; delete a false one; swap places; assign or change an athlete; set DNS, DNF or DQ; undo and redo. Every change is written to an audit log (time, user, field, old value, new value).

**Data model**

| Table | Main fields |
| --- | --- |
| sessions | id, name, date |
| athletes | session, bib, name, club, category |
| races | session, name, heat, distance, n\_flash, measured fps, link used, calibration snapshot, status |
| entries | race, lane, athlete |
| results | entry, auto frame, final frame, time ms, place, status, confidence, edited |
| audit | timestamp, user, entity, field, old, new |

**Exports.** CSV for the whole session (race, place, bib, name, club, time, status). PDF per race with the result table and the photo-finish image. The raw millisecond value is stored; the displayed time is rounded up to the next hundredth, as World Athletics does for photo-finish times (check your federation's rule; configurable).

## 8. Timing model and accuracy budget

At 120 fps the random timing error is about 3.4 ms (one standard deviation), which meets ±0.005 s statistically. The worst case is ±8.3 ms, which does not. At 240 fps the worst case drops to ±4.2 ms.

The race time of an athlete is:

```latex
t = \frac{n_{finish} - n_{flash}}{f} + \delta_{link}
```

n\_flash is the first frame with the start LED on, n\_finish is the finish frame (with a sub-frame refinement), f is the real frame rate measured from the reference LED, and δ\_link is the calibrated constant of the link that triggered the flash (section 5).

| Error source | Expected size | How it is handled |
| --- | --- | --- |
| Frame quantization of the flash and of the finish | Triangular, ±1 frame max (±8.3 ms at 120 fps), sigma 3.4 ms | The bias cancels because both events use the same convention (first frame showing the event). Sub-frame refinement reduces the rest |
| Frame rate not exactly nominal | 0.1% error is 12 ms over 12 s | f is measured from the reference LED in every recording |
| Radio latency jitter | Target 2 ms or less | Measured in calibration; radio not used for timing if worse |
| Cable latency jitter | Target 0.2 ms or less | Measured in calibration |
| Audio latency jitter | Target 1 ms or less | Measured in calibration |
| LED switching | Microseconds | Included in the calibration |
| Dropped frames | One frame each | Detected from the reference LED and flagged |
| Torso-crossing judgment | Not instrument-related | Operator review and correction in the UI |

Times are rounded up to the next hundredth for display; the raw milliseconds are stored.

## 9. Test and validation plan

The key test is a self-test against a known interval: the beacon produces two flashes a known time apart, and the software must measure that interval. It checks the whole chain except the athlete detection.

| # | Test | Method | Pass criterion |
| --- | --- | --- | --- |
| 1 | Pistol signal | Multimeter or scope: voltage, current, polarity, duration, bounce, whether it also makes sound | Values recorded; input stage sized from them |
| 2 | Audio latency | Logic analyzer, trigger pin to first DAC sample, 1000 triggers | Spread 1 ms or less |
| 3 | Cable latency | Trigger pin to LED gate pin, 1000 triggers | Spread 0.2 ms or less |
| 4 | Radio latency and range | Same measurement at 10, 50, 100, 200 m; 1000 packets each; also cable unplugged | Spread 2 ms or less; loss 0.1% or less |
| 5 | Beacon visibility | Record the flash indoors and outdoors at the real camera distance | Flash detected in 100 of 100 trials |
| 6 | Frame-rate stability | 10 minutes of capture on the laptop | Zero unflagged dropped frames; real fps recorded |
| 7 | Known-interval self-test | 100 runs of the beacon's two-flash test, intervals 8 to 15 s | Mean error near 0; sigma 4 ms or less at 120 fps |
| 8 | Auto-detection | 50 or more real crossings in training, compared with manual review | At least 95% within one frame; every miss flagged low confidence |
| 9 | Reference comparison | At a meeting with a certified system, if available | 30 or more athletes; mean difference 5 ms or less |
| 10 | Operator workflow | A full session of 100 athletes, from import to export | Results exported; review time per race noted |

## 10. Milestones and open items

&#91;embedded content: roadmap · 3 phases, 2 gates\]

The prototype is ready for training sessions by the end of January; each gate must pass before the next phase starts, and the field version is targeted for March 2027.

**Open items to confirm**

| Open item | Needed for |
| --- | --- |
| Pistol signal: voltage, current, polarity, duration, whether it also makes sound | Start-unit input stage (test 1) |
| Distance to the farthest lane, track layout, indoor or outdoor | Lens choice (section 6) |
| External amplifier input (level, connector) | Audio output stage |
| Laptop operating system | Capture backend (section 6) |
| Start-list CSV from your club | Import format (section 7) |
| Real frame rate of the OV9281 on your laptop at 1280 × 800 MJPG | Go or no-go for M1 (test 6) |
