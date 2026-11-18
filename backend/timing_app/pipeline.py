"""Frame pipeline: ring buffer, flash detection, recording window, post-analysis (spec section 6)."""
from __future__ import annotations

import json
import queue
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from . import analysis
from .flash import FlashDetector
from .framestore import FrameWriter


@dataclass
class Rec:
    idx: int
    jpeg: bytes
    col: np.ndarray
    start: float
    ref: float
    link: float


def rotate_img(img: np.ndarray, deg: int) -> np.ndarray:
    deg = int(deg) % 360
    if deg == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


def roi_mean(img: np.ndarray, roi) -> float:
    if not roi:
        return 0.0
    x, y, w, h = [int(v) for v in roi]
    h_img, w_img = img.shape
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w_img, x + w), min(h_img, y + h)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return float(img[y0:y1, x0:x1].mean())


class Pipeline:
    """Thread-safe entry point: push_frame() from any source; state is read with snapshot()."""

    def __init__(self, data_dir: Path, get_settings: Callable[[], dict],
                 get_calibration: Callable[[], dict],
                 on_recorded: Callable[[int, dict], None] | None = None):
        self.data_dir = Path(data_dir)
        self.get_settings = get_settings
        self.get_calibration = get_calibration
        self.on_recorded = on_recorded
        self.q: queue.Queue = queue.Queue(maxsize=1500)
        self.ring: deque = deque()
        self.detector: FlashDetector | None = None
        self.state = "idle"            # idle | armed | recording | analysing
        self.race_id: int | None = None
        self.message = ""
        self.frames_in = 0
        self.frames_dropped_queue = 0
        self.host_times: deque = deque(maxlen=240)
        self.frame_size = (0, 0)
        self.preview: bytes | None = None
        self._preview_t = 0.0
        self.last_idx = -1
        self._busy = False
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # recording state
        self._rec: dict | None = None

    # ---- lifecycle ----------------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="pipeline")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def wait_idle(self, timeout: float = 60.0) -> bool:
        """For tests: waits until the queue is drained and no analysis is running."""
        t_end = time.time() + timeout
        while time.time() < t_end:
            if self.q.empty() and self.state in ("idle", "armed") and not self._busy:
                return True
            time.sleep(0.02)
        return False

    # ---- input --------------------------------------------------------------------------------
    def push_frame(self, idx: int, t_host: float, jpeg: bytes, block: bool = False) -> None:
        try:
            if block:
                self.q.put((idx, t_host, jpeg), timeout=30)
            else:
                self.q.put_nowait((idx, t_host, jpeg))
        except queue.Full:
            self.frames_dropped_queue += 1

    def end_stream(self) -> None:
        """The source has no more frames (end of a video): closes a recording that is still open."""
        try:
            self.q.put((None, None, None), timeout=30)
        except queue.Full:
            pass

    def reset_stream(self, keep_detector: bool = False) -> None:
        """A new source starts counting frames from 0: forget the old frames."""
        with self._lock:
            if self.state in ("recording", "analysing"):
                raise RuntimeError("A recording is in progress")
            try:
                while True:
                    self.q.get_nowait()
            except queue.Empty:
                pass
            self.ring.clear()
            self.host_times.clear()
            self.last_idx = -1
            if self.state == "armed" and not keep_detector:
                s = self.get_settings()
                self.detector = FlashDetector(min_delta=s["flash_min_delta"], k_sigma=s["flash_k_sigma"],
                                              confirm=s["flash_confirm"])

    # ---- control ------------------------------------------------------------------------------
    def arm(self, race_id: int) -> None:
        with self._lock:
            if self.state in ("recording", "analysing"):
                raise RuntimeError("A recording is in progress")
            s = self.get_settings()
            self.detector = FlashDetector(min_delta=s["flash_min_delta"], k_sigma=s["flash_k_sigma"],
                                          confirm=s["flash_confirm"])
            self.race_id = race_id
            self.state = "armed"
            self.message = "Armed: waiting for the beacon flash"

    def disarm(self) -> None:
        with self._lock:
            if self.state == "armed":
                self.state = "idle"
                self.race_id = None
                self.message = "Disarmed"

    def snapshot(self) -> dict:
        with self._lock:
            ht = list(self.host_times)
            fps_in = (len(ht) - 1) / (ht[-1] - ht[0]) if len(ht) > 10 and ht[-1] > ht[0] else 0.0
            return {
                "state": self.state,
                "race_id": self.race_id,
                "message": self.message,
                "frames_in": self.frames_in,
                "frames_dropped_queue": self.frames_dropped_queue,
                "fps_in": round(fps_in, 1),
                "frame_w": self.frame_size[0],
                "frame_h": self.frame_size[1],
                "baseline_ready": bool(self.detector and self.detector.ready),
                "recording_frames": len(self._rec["cols"]) if self._rec else 0,
                "queue": self.q.qsize(),
            }

    # ---- worker -------------------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                idx, t_host, jpeg = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            self._busy = True
            try:
                if jpeg is None:                       # end of stream marker
                    self._end_of_stream()
                else:
                    self._process(idx, t_host, jpeg)
            except Exception as exc:  # keep the pipeline alive whatever a frame does
                self.message = f"Frame error: {exc!r}"
            finally:
                self._busy = False

    def _end_of_stream(self) -> None:
        with self._lock:
            state = self.state
        if state == "recording":
            self._finish_recording(self.get_settings())
        elif state == "armed":
            self.message = "The video ended without a start flash"

    def _process(self, idx: int, t_host: float, jpeg: bytes) -> None:
        s = self.get_settings()
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return
        img = rotate_img(img, s["rotate"])
        h, w = img.shape
        self.frame_size = (w, h)
        band = max(1, int(s["finish_band_px"]))
        x0 = int(min(max(0, s["finish_x"] - band // 2), w - band))
        col = img[:, x0:x0 + band].mean(axis=1).astype(np.uint8)
        rec = Rec(idx, jpeg, col,
                  roi_mean(img, s["start_led_roi"]),
                  roi_mean(img, s["ref_led_roi"]),
                  roi_mean(img, s.get("link_led_roi")))
        self.frames_in += 1
        self.last_idx = idx
        self.host_times.append(t_host)

        ring_n = int(s["ring_s"] * s["nominal_fps"])
        self.ring.append(rec)
        while len(self.ring) > ring_n:
            self.ring.popleft()

        now = time.monotonic()
        if now - self._preview_t > 0.1:
            self._preview_t = now
            scale = min(1.0, 640.0 / h)
            small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                self.preview = buf.tobytes()

        with self._lock:
            state = self.state
        if state == "armed":
            n = self.detector.update(idx, rec.start)
            if n is not None:
                self._begin_recording(n, s)
        elif state == "recording":
            self._append(rec)
            if idx >= self._rec["n_flash"] + self._rec["total_after"]:
                self._finish_recording(s)

    # ---- recording ----------------------------------------------------------------------------
    def _begin_recording(self, n_flash: int, s: dict) -> None:
        fps = float(s["nominal_fps"])
        first = n_flash - int(s["preroll_s"] * fps)
        rec_dir = self.data_dir / "races" / str(self.race_id)
        if rec_dir.exists():
            shutil.rmtree(rec_dir)
        pre = [r for r in self.ring if r.idx >= first]
        first_idx = pre[0].idx if pre else n_flash
        self._rec = {
            "dir": rec_dir, "n_flash": n_flash, "first_idx": first_idx,
            "total_after": int(s["record_s"] * fps), "writer": FrameWriter(rec_dir, first_idx),
            "idx": [], "cols": [], "start": [], "ref": [], "link": [], "last": first_idx - 1, "gaps": 0,
        }
        with self._lock:
            self.state = "recording"
            self.message = f"Flash detected at frame {n_flash}: recording"
        for r in pre:
            self._append(r)

    def _append(self, r: Rec) -> None:
        d = self._rec
        missing = r.idx - d["last"] - 1
        if missing > 0 and d["idx"]:
            for j in range(missing):          # keep strip columns uniform in time
                d["writer"].append_duplicate_of_last()
                d["idx"].append(d["last"] + 1 + j)
                d["cols"].append(d["cols"][-1])
                d["start"].append(d["start"][-1])
                d["ref"].append(d["ref"][-1])
                d["link"].append(d["link"][-1])
            d["gaps"] += missing
        d["writer"].append(r.jpeg)
        d["idx"].append(r.idx)
        d["cols"].append(r.col)
        d["start"].append(r.start)
        d["ref"].append(r.ref)
        d["link"].append(r.link)
        d["last"] = r.idx

    def _finish_recording(self, s: dict) -> None:
        d, self._rec = self._rec, None
        race_id = self.race_id
        with self._lock:
            self.state = "analysing"
            self.message = "Analysing the recording"
        d["writer"].close()
        rec_dir: Path = d["dir"]
        analysis.save_arrays(rec_dir, d["idx"], d["cols"], d["start"], d["ref"], d["link"])
        (rec_dir / "meta.json").write_text(json.dumps({
            "race_id": race_id, "n_flash": d["n_flash"], "first_idx": d["first_idx"],
            "frame_count": len(d["idx"]), "gap_frames": d["gaps"],
            "settings": s, "calibration": self.get_calibration(),
        }, indent=1))
        try:
            result = analysis.analyze(rec_dir, s, self.get_calibration())
            if self.on_recorded:
                self.on_recorded(race_id, result)
            msg = "Recording done: ready for review"
        except Exception as exc:
            msg = f"Analysis failed: {exc!r}"
        with self._lock:
            self.state = "idle"
            self.race_id = None
            self.message = msg
