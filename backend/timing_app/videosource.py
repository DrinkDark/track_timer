"""Plays a recorded video file into the pipeline, as if it were the live camera."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def probe(path: Path) -> dict | None:
    """Basic facts about a video file, or None when OpenCV cannot read it."""
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        ok, frame = cap.read()
        if not ok:
            return None
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
        return {"fps": round(fps, 3), "frames": max(n, 0), "w": int(frame.shape[1]), "h": int(frame.shape[0])}
    finally:
        cap.release()


class VideoPlayer:
    """One playback at a time. Frames go to pipeline.push_frame with their real frame number."""

    def __init__(self, pipeline):
        self.pipeline = pipeline
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self.name = ""
        self.fps = 0.0
        self.total = 0
        self.pos = 0
        self.speed = 1.0
        self.loop = False
        self.paced = True
        self.state = "stopped"        # stopped | playing | paused | ended | error
        self.error = ""

    @property
    def active(self) -> bool:
        return self.state in ("playing", "paused")

    def status(self) -> dict:
        return {"name": self.name, "state": self.state, "pos": self.pos, "total": self.total,
                "fps": self.fps, "speed": self.speed, "loop": self.loop, "paced": self.paced,
                "error": self.error}

    def play(self, path: Path, speed: float = 1.0, loop: bool = False, paced: bool = True) -> None:
        self.stop()
        info = probe(path)
        if info is None:
            raise ValueError("This file cannot be read as a video")
        self.name = path.name
        self.fps = info["fps"] if info["fps"] > 1 else 30.0
        self.total = info["frames"]
        self.pos = 0
        self.speed = max(0.05, min(float(speed), 16.0))
        self.loop, self.paced = bool(loop), bool(paced)
        self.error = ""
        self.state = "playing"
        self._stop.clear()
        self._pause.clear()
        self._thread = threading.Thread(target=self._run, args=(path,), daemon=True, name="video-source")
        self._thread.start()

    def pause(self, paused: bool) -> None:
        if self.state in ("playing", "paused"):
            (self._pause.set if paused else self._pause.clear)()
            self.state = "paused" if paused else "playing"

    def stop(self) -> None:
        t = self._thread
        if t and t.is_alive():
            self._stop.set()
            t.join(timeout=5)
        self._thread = None
        if self.state in ("playing", "paused"):
            self.state = "stopped"

    def _run(self, path: Path) -> None:
        cap = cv2.VideoCapture(str(path))
        idx = 0
        t0 = time.monotonic()
        paused_for = 0.0
        try:
            while not self._stop.is_set():
                if self._pause.is_set():
                    t_p = time.monotonic()
                    while self._pause.is_set() and not self._stop.is_set():
                        time.sleep(0.05)
                    paused_for += time.monotonic() - t_p
                    continue
                ok, frame = cap.read()
                if not ok:
                    if self.loop and idx > 0:
                        self.pipeline.end_stream()
                        self.pipeline.wait_idle(120)      # let a recording in progress finish
                        self.pipeline.reset_stream()
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        idx, t0, paused_for = 0, time.monotonic(), 0.0
                        continue
                    break
                if self.paced:
                    wait = t0 + paused_for + idx / (self.fps * self.speed) - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
                if ok:
                    self.pipeline.push_frame(idx, time.monotonic(), jpg.tobytes(), block=True)
                idx += 1
                self.pos = idx
            if not self._stop.is_set():
                self.pipeline.end_stream()
                self.state = "ended"
        except Exception as exc:  # never leave the state on "playing"
            self.error = repr(exc)
            self.state = "error"
        finally:
            cap.release()
