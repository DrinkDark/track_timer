"""Video import: upload, playback into the pipeline, flash detection and recording from a file."""
import sys
import time
from pathlib import Path

import cv2
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from timing_app.api import create_app  # noqa: E402
from timing_app.simulator import SimScene  # noqa: E402


def make_scene(fps=120.0):
    return SimScene(width=360, height=480, fps=fps)


def make_video(path: Path, seconds: float = 14.0, fps: float = 120.0):
    """A synthetic race written to a file, the way a recorded video would look."""
    scene = make_scene(fps)
    scene.set_race(1.0, {1: 11.5, 2: 11.9, 3: 12.3, 4: 12.8}, "radio")
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (scene.W, scene.H))
    for k in range(int(seconds * fps)):
        import numpy as np
        img = cv2.imdecode(np.frombuffer(scene.render_jpeg(k), np.uint8), cv2.IMREAD_COLOR)
        w.write(img)
    w.release()


@pytest.fixture()
def client(tmp_path):
    app = create_app(data_dir=tmp_path, source="agent")
    with TestClient(app) as c:
        yield c, tmp_path


def wait(cond, timeout=120):
    t = time.time() + timeout
    while time.time() < t:
        if cond():
            return True
        time.sleep(0.1)
    return False


def upload(c, path: Path, name=None):
    return c.post("/api/videos", params={"name": name or path.name}, content=path.read_bytes())


def test_rejects_bad_files(client, tmp_path):
    c, _ = client
    assert c.post("/api/videos", params={"name": "x.txt"}, content=b"abc").status_code == 400
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video at all")
    assert upload(c, bad).status_code == 400
    assert c.get("/api/videos").json()["files"] == []
    assert c.post("/api/video/play", json={"name": "nope.mp4"}).status_code == 404
    assert c.post("/api/video/play", json={"name": "../../etc/passwd"}).status_code == 404


def test_video_race_is_recorded_and_analysed(client, tmp_path):
    c, data = client
    src = tmp_path / "race.avi"
    make_video(src, seconds=16)
    assert upload(c, src).status_code == 200
    files = c.get("/api/videos").json()["files"]
    assert files[0]["fps"] == pytest.approx(120, abs=0.5) and files[0]["w"] == 360

    sid = c.post("/api/sessions", json={"name": "V"}).json()["id"]
    rid = c.post(f"/api/sessions/{sid}/races", json={"event": "100 m", "heat": "1", "lanes": 4}).json()["id"]
    c.put("/api/settings", json={**make_scene().default_settings(), "record_s": 14.0})
    assert c.post(f"/api/races/{rid}/arm").status_code == 200
    r = c.post("/api/video/play", json={"name": "race.avi", "paced": False})
    assert r.status_code == 200
    assert c.get("/api/settings").json()["nominal_fps"] == pytest.approx(120, abs=0.5)
    assert c.get("/api/state").json()["source"] in ("video", "agent")

    assert wait(lambda: c.get(f"/api/races/{rid}").json()["status"] == "review"), c.get("/api/state").json()
    race = c.get(f"/api/races/{rid}").json()
    times = sorted(e["time_ms"] for e in race["entries"] if e.get("time_ms"))
    assert len(times) == 4
    assert times[0] == pytest.approx(11500, abs=60)
    assert wait(lambda: c.get("/api/state").json()["video"]["state"] == "ended")
    assert c.post("/api/video/stop").json()["state"] in ("ended", "stopped")


def test_video_ending_inside_recording_still_finishes(client, tmp_path):
    c, _ = client
    src = tmp_path / "short.avi"
    make_video(src, seconds=8)             # shorter than the 18 s recording window
    upload(c, src)
    sid = c.post("/api/sessions", json={"name": "V"}).json()["id"]
    rid = c.post(f"/api/sessions/{sid}/races", json={"event": "100 m", "heat": "1", "lanes": 4}).json()["id"]
    c.post(f"/api/races/{rid}/arm")
    c.post("/api/video/play", json={"name": "short.avi", "paced": False})
    assert wait(lambda: c.get(f"/api/races/{rid}").json()["status"] == "review"), c.get("/api/state").json()


def test_play_unarmed_then_delete(client, tmp_path):
    c, data = client
    src = tmp_path / "a.avi"
    make_video(src, seconds=2)
    upload(c, src)
    c.post("/api/video/play", json={"name": "a.avi", "paced": True, "loop": True})
    time.sleep(0.5)
    st = c.get("/api/state").json()
    assert st["source"] == "video" and st["frames_in"] > 0
    assert c.delete("/api/videos/a.avi").status_code == 409
    c.post("/api/video/pause", json={"paused": True})
    assert c.get("/api/state").json()["video"]["state"] == "paused"
    c.post("/api/video/stop")
    assert c.delete("/api/videos/a.avi").status_code == 200
