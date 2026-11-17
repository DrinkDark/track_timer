"""Undo/redo, re-analysis history and input validation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from timing_app.db import Database  # noqa: E402


def _race_with_result(tmp_path):
    db = Database(tmp_path / "t.db")
    sid = db.create_session("s")
    rid = db.create_race(sid, "100m", "1", [1, 2])
    db._x("UPDATE races SET n_flash=100, fps=100.0, delta_ms=0, status='review' WHERE id=?", (rid,))
    return db, rid, db.get_race(rid)["entries"]


def test_undo_redo_marker_status_swap(tmp_path):
    db, rid, (a, b) = _race_with_result(tmp_path)
    db.set_marker(rid, a["entry_id"], 1200)
    db.set_marker(rid, b["entry_id"], 1300)
    db.set_status(rid, b["entry_id"], "DQ")
    db.swap_lanes_results(rid, a["entry_id"], b["entry_id"])
    assert db.history(rid) == {"undo": 4, "redo": 0}
    for _ in range(4):
        db.undo(rid)
    r = db.get_race(rid)
    assert [e["final_frame"] for e in r["entries"]] == [None, None] and not any(e["edited"] for e in r["entries"])
    for _ in range(4):
        db.redo(rid)
    r = db.get_race(rid)
    assert r["entries"][0]["final_frame"] == 1300 and r["entries"][1]["status"] == "DQ"
    db.undo(rid)
    db.set_marker(rid, a["entry_id"], 1250)          # a new edit clears redo
    assert db.history(rid)["redo"] == 0


def test_reanalysis_is_undoable_and_locked_race_refuses(tmp_path):
    db, rid, (a, b) = _race_with_result(tmp_path)
    db.set_marker(rid, a["entry_id"], 1200)
    db.push_history(rid)                             # what a reanalysis does before overwriting
    db.set_marker(rid, a["entry_id"], None)
    db.undo(rid)
    assert db.get_race(rid)["entries"][0]["final_frame"] == 1200
    db.set_locked(rid, True)
    try:
        db.undo(rid)
        assert False
    except PermissionError:
        pass


def test_api_validation(tmp_path):
    from timing_app.api import create_app
    with TestClient(create_app(tmp_path, source="agent")) as c:
        csv = "event,heat,lane,bib,name,club,category\n100m,1,1,1,A,B,C\n"
        assert c.post("/api/sessions/999/startlist", content=csv).status_code == 404
        sid = c.post("/api/sessions", json={"name": "x"}).json()["id"]
        assert c.post(f"/api/sessions/{sid}/startlist", content="garbage").status_code == 400
        assert c.post(f"/api/sessions/{sid}/startlist", content=csv).json() == {"races": 1, "athletes": 1, "warnings": []}
        assert c.post("/api/sim/start", json={}).status_code == 400      # not the sim source
        r = c.get(f"/api/sessions/{sid}/races").json()[0]
        eid = r["entries"][0]["entry_id"]
        assert c.put(f"/api/races/{r['id']}/entries/{eid}/marker", json={"frame": "abc"}).status_code == 400
        assert c.post(f"/api/races/{r['id']}/undo").status_code == 400


def test_import_moves_duplicate_lanes_and_set_lane(tmp_path):
    db = Database(tmp_path / "t.db")
    sid = db.create_session("s")
    res = db.import_startlist(sid, "event,heat,lane,bib,name,club,category\n"
                                   "T,2,1,1,A,c,u\nT,2,1,2,B,c,u\nT,2,1,3,C,c,u\nT,2,3,4,D,c,u\n")
    assert len(res["warnings"]) == 2
    race = db.list_races(sid)[0]
    assert sorted(e["lane"] for e in race["entries"]) == [1, 2, 3, 4]
    rid = race["id"]
    ents = {e["name"]: e for e in db.get_race(rid)["entries"]}
    assert ents["D"]["lane"] == 3 and ents["A"]["lane"] == 1
    # set_lane: refuses a used lane, takes the detection of the new lane, is undoable
    db._x("UPDATE races SET n_flash=100, fps=100.0, delta_ms=0, status='review', analysis=? WHERE id=?",
          ('{"detections":[{"lane":6,"frame":1300,"confidence":0.9,"notes":[]}]}', rid))
    try:
        db.set_lane(rid, ents["A"]["entry_id"], 3)
        assert False
    except ValueError:
        pass
    r = db.set_lane(rid, ents["A"]["entry_id"], 6)
    a = next(e for e in r["entries"] if e["name"] == "A")
    assert a["lane"] == 6 and a["final_frame"] == 1300 and a["time_ms"] == 12000.0 and a["place"] == 1
    r = db.undo(rid)
    a = next(e for e in r["entries"] if e["name"] == "A")
    assert a["lane"] == 1 and a["final_frame"] is None
