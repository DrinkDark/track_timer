"""Capture agent: reads the USB camera on the host and streams JPEG frames to the backend.

Usage: python agent.py [--server ws://localhost:8000/ws/ingest] [--camera 0] [--width 1280 --height 800 --fps 120]
Message format (binary): "<Qd" = frame counter (u64), host time (f64, seconds), then the JPEG bytes.
The frame counter increments on every frame read, so dropped frames show up as gaps in the backend.
"""
import argparse
import asyncio
import os
import struct
import sys
import time

import cv2
import websockets

HEADER = struct.Struct("<Qd")


def open_camera(a):
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    cam = int(a.camera) if str(a.camera).isdigit() else a.camera
    cap = cv2.VideoCapture(cam, backend)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, a.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, a.height)
    cap.set(cv2.CAP_PROP_FPS, a.fps)
    if a.exposure is not None:           # manual exposure (value is driver specific)
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
        cap.set(cv2.CAP_PROP_EXPOSURE, a.exposure)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera {a.camera}")
    print(f"camera: {int(cap.get(3))}x{int(cap.get(4))} @ {cap.get(cv2.CAP_PROP_FPS):.0f} fps requested")
    return cap


async def run(a):
    loop = asyncio.get_running_loop()
    while True:
        try:
            cap = open_camera(a)
            async with websockets.connect(a.server, max_size=None, compression=None) as ws:
                print("connected to", a.server)
                n, t_report, c = 0, time.time(), 0
                while True:
                    ok, frame = await loop.run_in_executor(None, cap.read)
                    if not ok:
                        raise RuntimeError("camera read failed")
                    t = time.time()
                    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, a.quality])
                    await ws.send(HEADER.pack(n, t) + jpg.tobytes())
                    n += 1; c += 1
                    if t - t_report >= 5:
                        print(f"{c / (t - t_report):.1f} fps sent"); t_report, c = t, 0
        except Exception as e:  # reconnect on any failure
            print("agent error:", e, "- retrying in 2 s")
            await asyncio.sleep(2)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--server", default=os.environ.get("SERVER", "ws://localhost:8000/ws/ingest"))
    p.add_argument("--camera", default=os.environ.get("CAMERA", "0"))
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=800)
    p.add_argument("--fps", type=int, default=120)
    p.add_argument("--quality", type=int, default=85)
    p.add_argument("--exposure", type=float, default=None)
    asyncio.run(run(p.parse_args()))
