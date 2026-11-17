"""End to end: simulated beacon + runners -> flash detection -> recording -> analysis -> API results."""
import math
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from timing_app.api import create_app  # noqa: E402

TIMES = {1: 11.52, 2: 11.83, 3: 12.31, 4: 12.77}


def run_race(client, app, tmp_name="Test", times=TIMES, link="radio", record_wait=240):
    sid = client.post("/api/sessions", json={"name": tmp_name}).json()["id"]
    rid = client.post(f"/api/sessions/{sid}/races", json={"event": "100 m", "heat": "1", "lanes": 4}).json()["id"]
    assert client.post(f"/api/races/{rid}/arm").status_code == 200
    r = client.post("/api/sim/start", json={"delay_s": 1.0, "times": {str(k): v for k, v in times.items()},
                                            "link": link})
    assert r.status_code == 200
    t_end = time.time() + record_wait
    while time.time() < t_end:
        if client.get(f"/api/races/{rid}").json()["status"] == "review":
            break
        time.sleep(0.25)
    else:
        pytest.fail("race never reached review: " + str(client.get("/api/state").json()))
    return sid, rid


@pytest.fixture()
def sim_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SIM_FPS", "119.7")           # the real camera is not exactly 120 fps
    app = create_app(data_dir=tmp_path, source="sim", sim_paced=False)
    with TestClient(app) as client:
        client.put("/api/settings", json={"nominal_fps": 120.0, "record_s": 18.0})
        client.put("/api/calibration", json={"audio_ms": 0.5, "led_ms": 0.0, "radio_ms": 1.4,
                                             "cable_ms": 0.2, "date": "2026-11-16"})
        yield client, app


def test_full_race_times_match_truth(sim_app):
    client, app = sim_app
    sid, rid = run_race(client, app)
    race = client.get(f"/api/races/{rid}").json()
    frame_ms = 1000.0 / 119.7

    assert race["link"] == "radio"                                   # link indicator read from the video
    assert abs(race["fps"] - 119.7) < 0.15                           # real fps measured from the blinker
    assert race["delta_ms"] == pytest.approx(0.9)
    assert not [w for w in race["warnings"] if "irregular" in w]

    by_lane = {e["lane"]: e for e in race["entries"]}
    for lane, truth in TIMES.items():
        e = by_lane[lane]
        assert e["time_ms"] is not None, f"lane {lane} not detected"
        err = e["time_ms"] - truth * 1000.0
        # Both events use "first frame showing the event": the error is below one frame.
        assert abs(err) <= frame_ms + 1.0, f"lane {lane}: error {err:.1f} ms"
        assert e["confidence"] >= 0.6
    assert [by_lane[l]["place"] for l in (1, 2, 3, 4)] == [1, 2, 3, 4]


def test_torso_rule_not_head_or_arms(sim_app):
    """Head, arms and legs lead the torso by about 4 frames; the detector must wait for the torso."""
    client, app = sim_app
    sid, rid = run_race(client, app, tmp_name="torso", times={1: 11.0, 2: 11.9, 3: 12.8, 4: 13.7})
    race = client.get(f"/api/races/{rid}").json()
    scene = app.state.scene
    for e in race["entries"]:
        truth_frame = scene.truth_cross_frame(e["lane"], {1: 11.0, 2: 11.9, 3: 12.8, 4: 13.7}[e["lane"]])
        assert e["final_frame"] is not None
        assert abs(e["final_frame"] - truth_frame) <= 1, (e["lane"], e["final_frame"], truth_frame)


def test_edit_marker_and_audit(sim_app):
    client, app = sim_app
    sid, rid = run_race(client, app, tmp_name="edit")
    race = client.get(f"/api/races/{rid}").json()
    e1 = next(e for e in race["entries"] if e["lane"] == 1)
    new_frame = e1["final_frame"] + 40                      # move the marker about 334 ms later
    r = client.put(f"/api/races/{rid}/entries/{e1['entry_id']}/marker", json={"frame": new_frame})
    assert r.status_code == 200
    after = next(e for e in r.json()["entries"] if e["lane"] == 1)
    assert after["edited"] == 1
    assert after["time_ms"] == pytest.approx(e1["time_ms"] + 40 / race["fps"] * 1000.0, abs=0.01)
    assert after["auto_frame"] == e1["final_frame"]          # the automatic value is kept
    # Moving lane 1 later than lane 2 changes the places.
    places = {e["lane"]: e["place"] for e in r.json()["entries"]}
    assert places[1] == 2 and places[2] == 1                 # 11.52 + 0.33 = 11.85 s > 11.83 s
    assert any(a["field"] == "final_frame" for a in client.get("/api/audit").json())


def test_lock_and_exports(sim_app):
    client, app = sim_app
    sid, rid = run_race(client, app, tmp_name="export")
    assert client.post(f"/api/races/{rid}/lock", json={"locked": True}).json()["status"] == "locked"
    e1 = client.get(f"/api/races/{rid}").json()["entries"][0]
    assert client.put(f"/api/races/{rid}/entries/{e1['entry_id']}/marker", json={"frame": 5}).status_code == 409
    csv_text = client.get(f"/api/sessions/{sid}/export.csv").text
    assert csv_text.splitlines()[0].startswith("event,heat")
    assert "11.5" in csv_text                                   # 11.52 s, rounded up to the hundredth
    pdf = client.get(f"/api/sessions/{sid}/export.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert client.get(f"/api/races/{rid}/strip.jpg").status_code == 200
    n_flash = client.get(f"/api/races/{rid}").json()["n_flash"]
    assert client.get(f"/api/races/{rid}/frame/{n_flash}.jpg").status_code == 200


def test_cable_link_uses_cable_delta(sim_app):
    client, app = sim_app
    sim = app.state.sim_state["sim"]
    app.state.scene.delta_ms = 0.2 - 0.5                      # cable: L_link 0.2, audio 0.5
    sid, rid = run_race(client, app, tmp_name="cable", link="cable")
    race = client.get(f"/api/races/{rid}").json()
    assert race["link"] == "cable"
    assert race["delta_ms"] == pytest.approx(-0.3)
    e = next(e for e in race["entries"] if e["lane"] == 1)
    assert abs(e["time_ms"] - TIMES[1] * 1000.0) <= 1000.0 / 119.7 + 1.0
