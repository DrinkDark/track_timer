"""Synthetic scene for developing and testing without hardware.

The scene is a side view of the track, already upright: the finish line is the vertical image
column finish_x, lane 1 is nearest to the camera (lowest in the image). Each runner is a set of
rectangles (torso, head, legs, arms). The head, arms and legs lead the torso, so detecting the
first frame in which *the torso* covers the line is a real test of the detector.

The beacon is drawn as three LED regions: start LED, reference blinker, link indicator.
Every event uses the convention "first frame showing the event": frame k shows the world at
time k / fps, so measured and true times agree to within one frame.
"""
from __future__ import annotations

import math
import threading
import time

import cv2
import numpy as np


class SimScene:
    def __init__(self, width: int = 480, height: int = 640, fps: float = 120.0, seed: int = 7,
                 n_lanes: int = 4, ref_hz: float = 23.0, px_per_m: float = 40.0,
                 jpeg_quality: int = 85):
        self.W, self.H = width, height
        self.fps = float(fps)
        self.dt = 1.0 / self.fps
        self.seed = seed
        self.n_lanes = n_lanes
        self.ref_hz = ref_hz
        self.px_per_m = px_per_m
        self.jpeg_quality = jpeg_quality
        self.finish_x = width // 2
        self.led_hold_s = 0.15
        self.tau0: float | None = None       # time (s) at which the start LED turns on
        self.link = "radio"                  # link whose signal reached the beacon first
        self.delta_ms = 0.9                  # flash lags t0 by this much (the true delta_link)
        self.cross_times: dict[int, float] = {}
        self.start_roi = (20, 20, 30, 30)
        self.ref_roi = (20, 70, 30, 30)
        self.link_roi = (20, 120, 30, 30)
        self._bg = self._make_background()
        self._lock = threading.Lock()

    # ---- geometry -------------------------------------------------------------------------
    def spacing(self) -> float:
        return 0.07 * self.H

    def ground_y(self, lane: int) -> int:
        return int(round(0.92 * self.H - (lane - 1) * self.spacing()))

    def athlete_h(self, lane: int) -> float:
        return 0.22 * self.H * (1 - 0.1 * (lane - 1))

    def lane_band(self, lane: int) -> tuple[int, int]:
        gy = self.ground_y(lane)
        return gy - int(0.3 * self.spacing()), gy + 3

    def default_settings(self) -> dict:
        return {
            "rotate": 0,
            "nominal_fps": self.fps,
            "finish_x": self.finish_x,
            "finish_band_px": 1,
            "start_led_roi": list(self.start_roi),
            "ref_led_roi": list(self.ref_roi),
            "link_led_roi": list(self.link_roi),
            "ref_led_hz": self.ref_hz,
            "lanes": [{"lane": k, "y0": self.lane_band(k)[0], "y1": self.lane_band(k)[1]}
                      for k in range(1, self.n_lanes + 1)],
            "detect": {"min_height": 60, "min_part": 6},
            "record_s": 20.0,
        }

    # ---- schedule -------------------------------------------------------------------------
    def set_race(self, tau0_s: float, race_times_s: dict[int, float], link: str = "radio") -> None:
        """Start LED turns on at tau0; t0 (the sound) is delta_ms earlier; the torso of the runner
        in lane L reaches the finish column race_times_s[L] seconds after t0."""
        with self._lock:
            self.tau0 = tau0_s
            self.link = link
            t0 = tau0_s - self.delta_ms / 1000.0
            self.cross_times = {lane: t0 + t for lane, t in race_times_s.items()}

    def truth_frame_of_flash(self) -> int:
        return int(math.ceil(self.tau0 / self.dt - 1e-9))

    def truth_cross_frame(self, lane: int, race_time_s: float) -> int:
        t0 = self.tau0 - self.delta_ms / 1000.0
        return int(math.ceil((t0 + race_time_s) / self.dt - 1e-9))

    # ---- rendering ------------------------------------------------------------------------
    def _make_background(self) -> np.ndarray:
        y = np.linspace(105, 150, self.H, dtype=np.float32)[:, None]
        bg = np.repeat(y, self.W, axis=1)
        for x in range(0, self.W, 53):                       # fence posts
            bg[:, x:x + 3] -= 18
        bg[: int(0.30 * self.H), :] += 12
        return bg

    @staticmethod
    def _rect(img, xa, xb, ya, yb, val):
        # A pixel is covered if its centre lies inside [xa, xb) x [ya, yb).
        x0 = max(0, int(math.ceil(xa - 0.5)))
        x1 = min(img.shape[1], int(math.ceil(xb - 0.5)))
        y0 = max(0, int(math.ceil(ya - 0.5)))
        y1 = min(img.shape[0], int(math.ceil(yb - 0.5)))
        if x1 > x0 and y1 > y0:
            img[y0:y1, x0:x1] = val

    def _draw_runner(self, img, lane: int, t: float) -> None:
        tc = self.cross_times.get(lane)
        if tc is None:
            return
        scale = self.athlete_h(lane) / self.athlete_h(1)
        w = 0.5 * self.px_per_m * scale
        v = 8.5 * self.px_per_m * scale * (1 + 0.03 * (lane - 2))
        xf = self.finish_x + 0.5 + v * (t - tc)           # front edge of the torso
        if xf < -3 * w or xf > self.W + 3 * w:
            return
        gy = float(self.ground_y(lane))
        ha = self.athlete_h(lane)
        val = 28 + 7 * lane
        phi = 2 * math.pi * 4.0 * t + lane
        s, c = math.sin(phi), math.cos(phi)
        self._rect(img, xf - w, xf, gy - 0.75 * ha, gy - 0.35 * ha, val)                      # torso
        self._rect(img, xf - 0.7 * w, xf + 0.25 * w, gy - ha, gy - 0.75 * ha, val)            # head
        for sign in (1, -1):                                                                  # legs
            lead = 0.65 * w * max(0.0, sign * s)
            self._rect(img, xf - w, xf + lead, gy - 0.35 * ha, gy, val)
        arm_lead = 0.6 * w * max(0.0, c)
        self._rect(img, xf - 0.5 * w, xf + 0.1 * w + arm_lead, gy - 0.68 * ha, gy - 0.58 * ha, val)

    def _draw_led(self, img, roi, on: bool) -> None:
        x, y, w, h = roi
        img[y:y + h, x:x + w] = 255 if on else 55

    def render(self, idx: int) -> np.ndarray:
        t = idx * self.dt
        rng = np.random.default_rng(self.seed * 1_000_003 + idx)
        img = self._bg + rng.standard_normal(self._bg.shape, dtype=np.float32) * 2.5
        with self._lock:
            tau0 = self.tau0
            for lane in range(self.n_lanes, 0, -1):          # far lanes first, near lanes occlude
                self._draw_runner(img, lane, t)
            link = self.link
        img = np.clip(img, 0, 255).astype(np.uint8)
        start_on = tau0 is not None and tau0 <= t < tau0 + self.led_hold_s
        self._draw_led(img, self.start_roi, start_on)
        ref_on = (math.floor(2.0 * self.ref_hz * t) % 2) == 0
        self._draw_led(img, self.ref_roi, ref_on)
        self._draw_led(img, self.link_roi, start_on and link == "radio")
        return img

    def render_jpeg(self, idx: int) -> bytes:
        ok, buf = cv2.imencode(".jpg", self.render(idx), [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        return buf.tobytes()


class SimulatedSource(threading.Thread):
    """Feeds a pipeline with frames from a SimScene, optionally paced in real time."""

    def __init__(self, scene: SimScene, pipeline, paced: bool = True):
        super().__init__(daemon=True, name="sim-source")
        self.scene = scene
        self.pipeline = pipeline
        self.paced = paced
        self.idx = 0
        self._stop = threading.Event()

    def trigger(self, delay_s: float, race_times_s: dict[int, float], link: str = "radio") -> float:
        """Schedules a race: the start LED turns on delay_s from now. Returns tau0 (s)."""
        tau0 = (self.idx + delay_s * self.scene.fps) * self.scene.dt
        self.scene.set_race(tau0, race_times_s, link)
        return tau0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        t_start = time.monotonic()
        while not self._stop.is_set():
            if self.paced:
                due = t_start + self.idx * self.scene.dt
                wait = due - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
            jpeg = self.scene.render_jpeg(self.idx)
            self.pipeline.push_frame(self.idx, time.monotonic(), jpeg, block=True)
            self.idx += 1
