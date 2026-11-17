"""Timing model (spec section 8): frame numbers to race time, real fps from the reference LED."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class FpsResult:
    fps: float
    measured: bool
    n_edges: int = 0
    max_resid: float = 0.0
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "fps": self.fps,
            "measured": self.measured,
            "n_edges": self.n_edges,
            "max_resid": self.max_resid,
            "warnings": list(self.warnings),
        }


def _rolling_median(x: np.ndarray, w: int) -> np.ndarray:
    if len(x) < w:
        return x
    pad = w // 2
    xp = np.pad(x, pad, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(xp, w)
    return np.median(win, axis=1)[: len(x)]


def _largest_step(edges: np.ndarray) -> tuple[float, float]:
    n = edges.size
    best, best_at = 0.0, 0.0
    k = np.arange(n, dtype=np.float64)
    for frac in np.linspace(0.12, 0.88, 17):
        m = int(frac * n)
        if m < 8 or n - m < 8:
            continue
        a1, b1 = np.polyfit(k[:m], edges[:m], 1)
        a2, b2 = np.polyfit(k[m:], edges[m:], 1)
        x = m - 0.5
        jump = abs((a1 * x + b1) - (a2 * x + b2))
        if jump > best:
            best, best_at = jump, edges[m]
    return best, best_at


def estimate_fps(ref: np.ndarray, ref_hz: float, nominal_fps: float) -> FpsResult:
    """Real frame rate from the reference LED, a square wave of frequency ref_hz.

    An edge occurs every 1/(2*ref_hz) seconds. A least-squares line through the edge frame
    numbers gives frames per edge, so quantization of single edges averages out. A step in
    the smoothed residuals marks a dropped (or duplicated) frame.
    """
    ref = np.asarray(ref, dtype=np.float64)
    res = FpsResult(fps=nominal_fps, measured=False)
    if ref.size < 40:
        res.warnings.append("Recording too short to measure the frame rate; nominal fps used")
        return res
    lo, hi = np.percentile(ref, [5, 95])
    if hi - lo < 25:
        res.warnings.append("Reference LED not visible; nominal fps used")
        return res
    state = ref > (lo + hi) / 2.0
    edges = np.flatnonzero(state[1:] != state[:-1]) + 1
    if edges.size < 20:
        res.warnings.append("Too few reference LED edges; nominal fps used")
        return res
    k = np.arange(edges.size, dtype=np.float64)
    slope, icpt = np.polyfit(k, edges.astype(np.float64), 1)
    resid = edges - (slope * k + icpt)
    half_period_s = 1.0 / (2.0 * ref_hz)
    res.fps = float(slope / half_period_s)
    res.measured = True
    res.n_edges = int(edges.size)
    res.max_resid = float(np.max(np.abs(resid)))
    # A dropped frame shifts every later edge by one frame. Fit separate lines left and right of
    # several split points and compare them at the split: quantization alone keeps the difference
    # small when the blinker frequency is not a simple fraction of the frame rate (use 23 Hz, not 20 Hz),
    # while a missing frame makes it about one frame.
    jump, at = _largest_step(edges.astype(np.float64))
    if jump > 0.68:
        res.warnings.append(
            f"Frame timing irregular near frame offset {int(at)}: a frame may have been dropped")
    spacing = np.diff(edges)
    if spacing.size and spacing.min() < 0.5 * np.median(spacing):
        res.warnings.append("Reference LED shows spurious edges (flicker or noise)")
    return res


def race_time_ms(n_finish: float, n_flash: int, fps: float, delta_ms: float) -> float:
    """t = (n_finish - n_flash) / f + delta_link."""
    return (n_finish - n_flash) / fps * 1000.0 + delta_ms


ROUND_TOL_HUNDREDTHS = 0.005


def round_up_hundredths(t_ms: float) -> int:
    """Time rounded up to the next hundredth of a second, as an integer number of hundredths."""
    # 0.05 ms of tolerance (a frame is 8 ms) so float noise on an exact value, e.g. 11200.02 ms
    # from a 119.9998 fps estimate, is not pushed up a whole hundredth.
    return int(math.ceil(t_ms / 10.0 - ROUND_TOL_HUNDREDTHS))


def format_time(t_ms: float | None) -> str:
    if t_ms is None:
        return ""
    h = round_up_hundredths(t_ms)
    s, hh = divmod(h, 100)
    m, ss = divmod(s, 60)
    return f"{m}:{ss:02d}.{hh:02d}" if m else f"{ss}.{hh:02d}"
