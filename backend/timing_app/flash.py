"""Beacon flash detection (spec section 6)."""
from __future__ import annotations

from collections import deque

import numpy as np


class FlashDetector:
    """Finds the first frame in which the start LED is on.

    The mean brightness of the start-LED region is compared with a running baseline. The flash
    is confirmed when `confirm` consecutive frames exceed baseline + threshold; the returned
    frame number is the first of those frames. Candidate frames never enter the baseline.
    """

    def __init__(self, base_n: int = 120, min_ready: int = 30, min_delta: float = 40.0,
                 k_sigma: float = 6.0, confirm: int = 2):
        self.base_n = base_n
        self.min_ready = min_ready
        self.min_delta = min_delta
        self.k_sigma = k_sigma
        self.confirm = confirm
        self.reset()

    def reset(self) -> None:
        self.base: deque = deque(maxlen=self.base_n)
        self.cand_first: int | None = None
        self.cand_count = 0

    @property
    def ready(self) -> bool:
        return len(self.base) >= self.min_ready

    def update(self, idx: int, value: float) -> int | None:
        if self.ready:
            arr = np.fromiter(self.base, dtype=np.float64)
            med = float(np.median(arr))
            thr = max(self.min_delta, self.k_sigma * float(arr.std()))
            if value - med > thr:
                if self.cand_first is None:
                    self.cand_first = idx
                self.cand_count += 1
                if self.cand_count >= self.confirm:
                    n = self.cand_first
                    self.cand_first = None
                    self.cand_count = 0
                    return n
                return None
        self.cand_first = None
        self.cand_count = 0
        self.base.append(value)
        return None


def link_from_indicator(link_vals: np.ndarray, pos: int, min_delta: float = 40.0,
                        k_sigma: float = 6.0) -> str | None:
    """Reads the link indicator LED around the flash: on = radio, off = cable.

    Returns None if the indicator region has no usable baseline (not visible).
    """
    link_vals = np.asarray(link_vals, dtype=np.float64)
    if pos < 10 or pos + 5 > link_vals.size:
        return None
    base = link_vals[max(0, pos - 60): pos - 1]
    if base.size < 5:
        return None
    thr = max(min_delta, k_sigma * float(base.std()))
    after = link_vals[pos + 1: pos + 5]
    return "radio" if float(after.max()) - float(np.median(base)) > thr else "cable"
