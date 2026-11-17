"""Post-recording analysis: real fps, link, calibration delta, photo-finish strip, detection."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from .config import delta_link_ms
from .detect import detect_crossings
from .flash import link_from_indicator
from .timing import estimate_fps

STRIP_MAX_DISPLAY_H = 640


def save_arrays(rec_dir: Path, idx, cols, start, ref, link) -> None:
    np.savez_compressed(
        Path(rec_dir) / "arrays.npz",
        idx=np.asarray(idx, dtype=np.int64),
        cols=np.asarray(cols, dtype=np.uint8),
        start=np.asarray(start, dtype=np.float32),
        ref=np.asarray(ref, dtype=np.float32),
        link=np.asarray(link, dtype=np.float32),
    )


def load_arrays(rec_dir: Path) -> dict:
    with np.load(Path(rec_dir) / "arrays.npz") as z:
        return {k: z[k] for k in z.files}


def build_strip(cols: np.ndarray) -> np.ndarray:
    """cols is (frames, rows); the strip is (rows, frames)."""
    return np.ascontiguousarray(cols.T)


def analyze(rec_dir: Path, settings: dict, calibration: dict) -> dict:
    rec_dir = Path(rec_dir)
    meta = json.loads((rec_dir / "meta.json").read_text())
    arr = load_arrays(rec_dir)
    idx = arr["idx"]
    first_idx = int(idx[0])
    n_flash = int(meta["n_flash"])
    pos = n_flash - first_idx
    warnings: list[str] = []

    fps_res = estimate_fps(arr["ref"], float(settings["ref_led_hz"]), float(settings["nominal_fps"]))
    warnings += fps_res.warnings

    link, link_source = settings.get("default_link", "cable"), "default"
    if settings.get("link_led_roi"):
        got = link_from_indicator(arr["link"], pos, settings["flash_min_delta"], settings["flash_k_sigma"])
        if got is not None:
            link, link_source = got, "indicator"
    if link_source == "default":
        warnings.append(f"Link indicator not used; assuming the {link} link")
    delta = delta_link_ms(calibration, link)
    if not calibration.get("date"):
        warnings.append("Latency calibration missing; delta_link = 0")
    if meta.get("gap_frames"):
        warnings.append(f"{meta['gap_frames']} frame(s) were missing in the ingest and were duplicated")

    strip = build_strip(arr["cols"])
    dets = detect_crossings(strip, settings["lanes"], fps_res.fps, settings["detect"])
    for d in dets:
        if d["frame"] is not None:
            d["frame"] = first_idx + d["frame"]

    h_full = strip.shape[0]
    disp_h = min(h_full, STRIP_MAX_DISPLAY_H)
    disp = cv2.resize(strip, (strip.shape[1], disp_h), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(rec_dir / "strip.jpg"), disp, [cv2.IMWRITE_JPEG_QUALITY, 88])

    result = {
        "n_flash": n_flash,
        "first_idx": first_idx,
        "frame_count": int(idx.size),
        "fps": fps_res.as_dict(),
        "link": link,
        "link_source": link_source,
        "delta_ms": delta,
        "detections": dets,
        "warnings": warnings,
        "strip": {"w": int(strip.shape[1]), "h": int(h_full), "display_h": int(disp_h),
                  "y_scale": disp_h / h_full},
        "lanes": settings["lanes"],
        "finish_x": settings["finish_x"],
        "rotate": settings["rotate"],
    }
    (rec_dir / "analysis.json").write_text(json.dumps(result, indent=1))
    return result
