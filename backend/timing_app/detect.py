"""Automatic finish detection (spec section 7, version 1: classical image processing).

Input is the photo-finish strip: a 2D array (rows = image height, columns = frames) holding the
finish-line column of every frame. The finish moment of a lane is the first frame in which the
torso region of the runner covers the line (not head, arms or legs).
"""
from __future__ import annotations

import numpy as np


def runs_in_column(mask_col: np.ndarray, close_gap: int) -> list[tuple[int, int]]:
    """Vertical runs of foreground pixels, merging gaps of up to close_gap background rows."""
    ys = np.flatnonzero(mask_col)
    if ys.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(ys) > close_gap + 1)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [ys.size - 1]))
    return [(int(ys[s]), int(ys[e])) for s, e in zip(starts, ends)]


def foreground_mask(strip: np.ndarray, bg_n: int, abs_threshold: float) -> np.ndarray:
    bg_frames = strip[:, :bg_n].astype(np.float32)
    bg = np.median(bg_frames, axis=1)
    noise = bg_frames.std(axis=1)
    thr = np.maximum(abs_threshold, 4.0 * noise)
    diff = np.abs(strip.astype(np.float32) - bg[:, None])
    return diff > thr[:, None]


def detect_crossings(strip: np.ndarray, lanes: list[dict], fps: float, cfg: dict) -> list[dict]:
    """Returns one dict per lane: lane, frame (column index in the strip or None), confidence, notes."""
    n_rows, n_frames = strip.shape
    bg_n = max(10, min(n_frames // 4, int(cfg["bg_seconds"] * fps)))
    fg = foreground_mask(strip, bg_n, cfg["abs_threshold"])

    tops = {ln["lane"]: np.full(n_frames, -1, dtype=np.int32) for ln in lanes}
    bots = {ln["lane"]: np.full(n_frames, -1, dtype=np.int32) for ln in lanes}
    min_part = int(cfg["min_part"])
    for f in range(n_frames):
        runs = runs_in_column(fg[:, f], int(cfg["close_gap"]))
        for top, bot in runs:
            if bot - top + 1 < min_part:
                continue
            for ln in lanes:
                if ln["y0"] <= bot <= ln["y1"]:
                    lane = ln["lane"]
                    cur_h = bots[lane][f] - tops[lane][f] + 1 if tops[lane][f] >= 0 else 0
                    if bot - top + 1 > cur_h:
                        tops[lane][f], bots[lane][f] = top, bot
                    break

    results = []
    persist = int(cfg["persist"])
    win = max(3, int(cfg["window_s"] * fps))
    for ln in lanes:
        lane = ln["lane"]
        top, bot = tops[lane], bots[lane]
        present = top >= 0
        out = {"lane": lane, "frame": None, "confidence": 0.0, "height_px": None,
               "ground_y": None, "lead_frames": None, "notes": []}
        # Walk through presence events; skip the ones too small to be a whole runner
        # (a floating head or an arm of a runner in another lane can land in this lane's band).
        f_a = None
        h_a = 0
        w_end = n_frames
        hts = np.zeros(0)
        f = bg_n
        while f <= n_frames - persist:
            if not present[f:f + persist].all():
                f += 1
                continue
            end = min(n_frames, f + win)
            cand = np.where(present[f:end], bot[f:end] - top[f:end] + 1, 0)
            if int(cand.max()) >= cfg["min_height"]:
                f_a, h_a, w_end, hts = f, int(cand.max()), end, cand
                break
            g = f
            while g < n_frames and present[g]:
                g += 1
            f = max(g, f + 1)
        if f_a is None:
            out["notes"].append("no runner seen")
            results.append(out)
            continue
        out["height_px"] = h_a
        conf = 0.95
        if h_a < 1.5 * cfg["min_height"]:
            conf -= 0.25
        sel = np.flatnonzero(hts >= 0.8 * h_a) + f_a
        y_g = float(np.median(bot[sel]))
        out["ground_y"] = y_g
        y_a = y_g - cfg["torso_high"] * h_a
        y_b = y_g - cfg["torso_low"] * h_a
        band = max(1.0, y_b - y_a)
        cover = np.zeros(n_frames)
        for f in range(f_a, min(n_frames, f_a + 3 * win)):
            if present[f]:
                ov = min(bot[f], y_b) - max(top[f], y_a)
                cover[f] = max(0.0, ov) / band
        f_t = None
        for f in range(f_a, min(n_frames - 1, f_a + 3 * win)):
            if cover[f] >= cfg["cover"] and cover[f + 1] >= cfg["cover"]:
                f_t = f
                break
        if f_t is None:
            out["notes"].append("torso never covered the line")
            out["confidence"] = 0.2
            results.append(out)
            continue
        out["frame"] = int(f_t)
        out["lead_frames"] = int(f_t - f_a)
        if w_end >= n_frames:
            conf -= 0.2
            out["notes"].append("recording ends during the crossing")
        # Another runner in the same lane within one second after the window.
        after = present[min(n_frames, f_t + win): min(n_frames, f_t + int(fps))]
        if after.size and after.sum() >= persist:
            conf -= 0.2
            out["notes"].append("second runner in this lane within 1 s")
        out["confidence"] = round(max(0.0, conf), 2)
        results.append(out)

    # Neighbouring lanes crossing within 3 frames are easily mixed up: lower the confidence.
    found = [r for r in results if r["frame"] is not None]
    for a in found:
        for b in found:
            if a is not b and abs(a["frame"] - b["frame"]) <= 3:
                a["confidence"] = round(max(0.0, a["confidence"] - 0.15), 2)
                a["notes"].append(f"close to lane {b['lane']}")
                break
    return results
