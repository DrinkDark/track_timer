"""Settings and calibration, stored as JSON files in the data directory."""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

DEFAULT_SETTINGS: dict = {
    # Frame geometry. rotate = clockwise degrees applied to each decoded frame so that
    # the finish line is a vertical image column and the ground is at the bottom.
    "rotate": 0,
    "nominal_fps": 120.0,
    # Image positions are in the rotated ("upright") frame, in pixels.
    "finish_x": 400,
    "finish_band_px": 3,
    "start_led_roi": [20, 20, 30, 30],   # x, y, w, h
    "ref_led_roi": [20, 70, 30, 30],
    "link_led_roi": [20, 120, 30, 30],
    "ref_led_hz": 23.0,
    # Recording window
    "ring_s": 10.0,
    "preroll_s": 2.0,
    "record_s": 40.0,
    # Flash detector
    "flash_min_delta": 40.0,
    "flash_k_sigma": 6.0,
    "flash_confirm": 2,
    # Ground-contact band per lane (feet must fall inside [y0, y1]), upright pixels.
    "lanes": [
        {"lane": 1, "y0": 1130, "y1": 1200},
        {"lane": 2, "y0": 1050, "y1": 1120},
        {"lane": 3, "y0": 970, "y1": 1040},
        {"lane": 4, "y0": 890, "y1": 960},
    ],
    # Detection
    "detect": {
        "bg_seconds": 1.5,
        "abs_threshold": 18.0,
        "close_gap": 6,
        "min_part": 6,
        "min_height": 60,
        "persist": 2,
        "window_s": 0.4,
        "torso_low": 0.35,
        "torso_high": 0.75,
        "cover": 0.5,
    },
    "default_link": "cable",
    "auto_rearm": False,
}

DEFAULT_CALIBRATION: dict = {
    # All values in milliseconds. delta_link = L_link + L_led - L_audio (spec section 5).
    "audio_ms": 0.0,
    "led_ms": 0.0,
    "cable_ms": 0.0,
    "radio_ms": 0.0,
    "date": None,
    "note": "Not calibrated yet",
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def data_dir() -> Path:
    env = os.environ.get("TIMING_DATA")
    if env:
        p = Path(env)
    elif Path("/data").is_dir():
        p = Path("/data")
    else:
        p = Path(__file__).resolve().parents[2] / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


class JsonStore:
    """A small thread-safe JSON document with defaults."""

    def __init__(self, path: Path, defaults: dict):
        self.path = Path(path)
        self.defaults = defaults
        self._lock = threading.RLock()
        self._value = copy.deepcopy(defaults)
        if self.path.exists():
            try:
                self._value = _merge(defaults, json.loads(self.path.read_text()))
            except (OSError, ValueError):
                self._value = copy.deepcopy(defaults)

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._value)

    def update(self, patch: dict) -> dict:
        with self._lock:
            self._value = _merge(self._value, patch)
            self.path.write_text(json.dumps(self._value, indent=2))
            return copy.deepcopy(self._value)

    def replace(self, value: dict) -> dict:
        with self._lock:
            self._value = _merge(self.defaults, value)
            self.path.write_text(json.dumps(self._value, indent=2))
            return copy.deepcopy(self._value)


def delta_link_ms(calibration: dict, link: str) -> float:
    """delta_link = L_link + L_led - L_audio (milliseconds)."""
    l_link = calibration.get("radio_ms" if link == "radio" else "cable_ms", 0.0)
    return float(l_link) + float(calibration.get("led_ms", 0.0)) - float(calibration.get("audio_ms", 0.0))
