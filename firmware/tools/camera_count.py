#!/usr/bin/env python3
"""Frame counting for the camera tests (spec section 9 test 6, and section 11 trigger tests).

  stats  - free-running camera: frames, effective fps, gaps. Frame-rate stability test (test 6):
      python camera_count.py stats --camera 0 --seconds 600 --width 1280 --height 800 --fps 120

  burst  - triggered camera: the start unit sends exactly N pulses and the camera must return exactly N frames.
           The camera has to be in external-trigger mode (see Arducam's documentation) and the start unit
           connected to the trigger pads. Section 11, test steps 2 and 3:
      python camera_count.py burst --camera 0 --serial /dev/tty.usbmodemXXXX --pulses 1200 --hz 120

Frame times are host times (perf_counter): good enough to count frames and see gaps, never used for race times.
Needs opencv-python (cv2) and, for burst, pyserial.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from typing import List, Optional


# ---- analysis (pure, tested without a camera) ---------------------------------------------------------------
def analyze_intervals(ts: List[float], nominal_fps: Optional[float] = None) -> dict:
    """Statistics of frame arrival times (seconds). A gap of more than 1.5 intervals counts as lost frames."""
    n = len(ts)
    out = {"frames": n, "duration_s": 0.0, "fps": 0.0, "interval_ms_median": 0.0, "interval_ms_max": 0.0,
           "interval_ms_stdev": 0.0, "gaps": 0, "lost_frames_estimate": 0}
    if n < 3:
        return out
    iv = [b - a for a, b in zip(ts, ts[1:])]
    med = statistics.median(iv)
    ref = (1.0 / nominal_fps) if nominal_fps else med
    out.update(duration_s=ts[-1] - ts[0], fps=(n - 1) / (ts[-1] - ts[0]), interval_ms_median=med * 1e3,
               interval_ms_max=max(iv) * 1e3, interval_ms_stdev=statistics.pstdev(iv) * 1e3)
    gaps = [g for g in iv if g > 1.5 * ref]
    out["gaps"] = len(gaps)
    out["lost_frames_estimate"] = int(sum(round(g / ref) - 1 for g in gaps))
    return out


def burst_verdict(sent: int, received: int, idle_frames_before: int) -> str:
    if idle_frames_before:
        return (f"NOT IN TRIGGER MODE: {idle_frames_before} frames arrived before any pulse was sent. "
                "Put the camera in external-trigger mode first.")
    if received == sent:
        return f"PASS: {received} frames for {sent} pulses"
    if received < sent:
        return f"FAIL: {sent - received} pulse(s) gave no frame ({received} of {sent})"
    return f"FAIL: {received - sent} frame(s) more than pulses ({received} for {sent})"


# ---- camera and serial wrappers ------------------------------------------------------------------------------
class FrameReader(threading.Thread):
    """Reads frames in a thread so that the main thread can wait with timeouts (a triggered camera can be silent)."""

    def __init__(self, cap):
        super().__init__(daemon=True)
        self.cap, self.ts, self.stop_flag = cap, [], False
        self.lock = threading.Lock()

    def run(self):
        while not self.stop_flag:
            ok, _ = self.cap.read()
            if ok:
                with self.lock:
                    self.ts.append(time.perf_counter())
            else:
                time.sleep(0.002)

    def count(self) -> int:
        with self.lock:
            return len(self.ts)

    def times(self) -> List[float]:
        with self.lock:
            return list(self.ts)


def open_camera(a):
    import cv2
    cap = cv2.VideoCapture(a.camera)
    if a.fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*a.fourcc))
    if a.width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, a.width)
    if a.height:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, a.height)
    if a.fps:
        cap.set(cv2.CAP_PROP_FPS, a.fps)
    if not cap.isOpened():
        raise SystemExit(f"cannot open camera {a.camera}")
    print(f"camera {a.camera}: {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} "
          f"reported {cap.get(cv2.CAP_PROP_FPS):.1f} fps")
    return cap


def run_stats(reader, seconds: float, nominal: Optional[float], sleep=time.sleep) -> dict:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        sleep(0.25)
    reader.stop_flag = True
    return analyze_intervals(reader.times(), nominal)


def run_burst(reader, ser, pulses: int, hz: float, sleep=time.sleep, clock=time.monotonic) -> dict:
    """ser needs write(bytes) and readline() (pyserial). Returns the numbers and a verdict."""
    sleep(1.0)                                   # a triggered camera must be silent now
    idle = reader.count()
    ser.write(b"\n")
    ser.write(f"camtrig burst {pulses} {hz}\n".encode())
    start_idx = reader.count()
    deadline = clock() + pulses / hz + 10
    done = False
    while clock() < deadline and not done:
        line = ser.readline().decode(errors="replace")
        done = "$CAMTRIG done" in line
    if not done:
        return {"error": "the start unit did not report the end of the burst"}
    last, quiet_since = reader.count(), clock()
    while clock() - quiet_since < 1.5:           # wait until no new frame arrives for 1.5 s
        sleep(0.1)
        now = reader.count()
        if now != last:
            last, quiet_since = now, clock()
    reader.stop_flag = True
    ts = reader.times()[idle:] if idle else reader.times()[start_idx:]
    received = len(ts)
    return {"sent": pulses, "received": received, "idle_frames_before": idle,
            "verdict": burst_verdict(pulses, received, idle), "intervals": analyze_intervals(ts, hz)}


def print_stats(r: dict) -> None:
    print(f"frames {r['frames']} in {r['duration_s']:.2f} s = {r['fps']:.3f} fps; interval median "
          f"{r['interval_ms_median']:.3f} ms, max {r['interval_ms_max']:.3f} ms, stdev {r['interval_ms_stdev']:.3f} ms")
    print(f"gaps (>1.5 intervals): {r['gaps']}, estimated lost frames: {r['lost_frames_estimate']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["stats", "burst"])
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--fps", type=float, help="requested frame rate; also the nominal rate for gap detection")
    ap.add_argument("--fourcc", default="MJPG", help="pixel format, empty for the camera default")
    ap.add_argument("--seconds", type=float, default=30, help="stats: capture time")
    ap.add_argument("--serial", help="burst: serial port of the start unit")
    ap.add_argument("--pulses", type=int, default=600, help="burst: number of pulses")
    ap.add_argument("--hz", type=float, default=120, help="burst: trigger rate")
    a = ap.parse_args()
    cap = open_camera(a)
    reader = FrameReader(cap)
    reader.start()
    if a.mode == "stats":
        print_stats(run_stats(reader, a.seconds, a.fps))
        cap.release()
        return 0
    if not a.serial:
        raise SystemExit("burst needs --serial")
    import serial
    with serial.Serial(a.serial, 115200, timeout=1) as ser:
        r = run_burst(reader, ser, a.pulses, a.hz)
    cap.release()
    if "error" in r:
        print(r["error"])
        return 2
    print(r["verdict"])
    print_stats(r["intervals"])
    return 0 if r["verdict"].startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
