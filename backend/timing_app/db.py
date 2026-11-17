"""SQLite storage (spec section 7 data model) plus the result logic: times, places, audit log."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import time
from pathlib import Path

from .timing import format_time, race_time_ms

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, name TEXT, date TEXT);
CREATE TABLE IF NOT EXISTS athletes(id INTEGER PRIMARY KEY, session_id INTEGER, bib TEXT, name TEXT,
    club TEXT, category TEXT);
CREATE TABLE IF NOT EXISTS races(id INTEGER PRIMARY KEY, session_id INTEGER, event TEXT, heat TEXT,
    ord INTEGER, status TEXT DEFAULT 'scheduled', n_flash INTEGER, first_idx INTEGER, frame_count INTEGER,
    fps REAL, link TEXT, delta_ms REAL, recorded_at TEXT, analysis TEXT, warnings TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY, race_id INTEGER, lane INTEGER, athlete_id INTEGER);
CREATE TABLE IF NOT EXISTS results(entry_id INTEGER PRIMARY KEY, auto_frame INTEGER, final_frame INTEGER,
    auto_time_ms REAL, time_ms REAL, place INTEGER, status TEXT DEFAULT 'OK', confidence REAL DEFAULT 0,
    notes TEXT DEFAULT '', edited INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT, user TEXT, entity TEXT, entity_id INTEGER,
    field TEXT, old TEXT, new TEXT);
"""

STATUSES = ("OK", "DNS", "DNF", "DQ")


class Database:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._undo: dict[int, list] = {}
        self._redo: dict[int, list] = {}
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # ---- helpers ------------------------------------------------------------------------------
    def _q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def _x(self, sql: str, args=()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    def _audit(self, user: str, entity: str, entity_id: int, field: str, old, new) -> None:
        self._x("INSERT INTO audit(ts,user,entity,entity_id,field,old,new) VALUES(?,?,?,?,?,?,?)",
                (time.strftime("%Y-%m-%d %H:%M:%S"), user, entity, entity_id, field,
                 None if old is None else str(old), None if new is None else str(new)))

    # ---- sessions and start list --------------------------------------------------------------
    def create_session(self, name: str, date: str | None = None) -> int:
        return self._x("INSERT INTO sessions(name,date) VALUES(?,?)",
                       (name, date or time.strftime("%Y-%m-%d")))

    def list_sessions(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM sessions ORDER BY id DESC")]

    def _next_ord(self, session_id: int) -> int:
        r = self._q("SELECT COALESCE(MAX(ord),0)+1 AS n FROM races WHERE session_id=?", (session_id,))
        return int(r[0]["n"])

    def create_race(self, session_id: int, event: str, heat: str, lanes: list[int]) -> int:
        rid = self._x("INSERT INTO races(session_id,event,heat,ord) VALUES(?,?,?,?)",
                      (session_id, event, str(heat), self._next_ord(session_id)))
        for lane in lanes:
            aid = self._x("INSERT INTO athletes(session_id,bib,name,club,category) VALUES(?,?,?,?,?)",
                          (session_id, "", f"Lane {lane}", "", ""))
            eid = self._x("INSERT INTO entries(race_id,lane,athlete_id) VALUES(?,?,?)", (rid, lane, aid))
            self._x("INSERT INTO results(entry_id) VALUES(?)", (eid,))
        return rid

    def import_startlist(self, session_id: int, text: str) -> dict:
        """CSV columns (any order, case-insensitive): event, heat, lane, bib, name, club, category."""
        if not self._q("SELECT 1 FROM sessions WHERE id=?", (session_id,)):
            raise KeyError("session not found")
        sample = text[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in reader]
        if not rows or not ({"name", "bib", "lane"} & set(rows[0])):
            raise ValueError("No start list found: expected a header with event, heat, lane, bib, name, club, category")
        races: dict[tuple, int] = {}
        used: dict[tuple, set] = {}
        warnings: list[str] = []
        wanted: dict[tuple, set] = {}               # lanes the file asks for, per heat
        for r in rows:
            if r.get("lane", "").isdigit() and int(r["lane"]) > 0:
                wanted.setdefault((r.get("event") or "Race", r.get("heat") or "1"), set()).add(int(r["lane"]))
        n_ath = 0
        counters: dict[tuple, int] = {}
        for r in rows:
            event = r.get("event") or "Race"
            heat = r.get("heat") or "1"
            key = (event, heat)
            if key not in races:
                races[key] = self._x("INSERT INTO races(session_id,event,heat,ord) VALUES(?,?,?,?)",
                                     (session_id, event, heat, self._next_ord(session_id)))
            counters[key] = counters.get(key, 0) + 1
            lane = int(r["lane"]) if r.get("lane", "").isdigit() and int(r["lane"]) > 0 else counters[key]
            taken = used.setdefault(key, set())
            if lane in taken:                       # two athletes in one lane cannot both be timed
                busy = taken | wanted.get(key, set())
                new = next(n for n in range(1, len(busy) + 2) if n not in busy)
                warnings.append(f"{event} heat {heat}: lane {lane} given twice ({r.get('name', '')}) - moved to lane {new}")
                lane = new
            taken.add(lane)
            aid = self._x("INSERT INTO athletes(session_id,bib,name,club,category) VALUES(?,?,?,?,?)",
                          (session_id, r.get("bib", ""), r.get("name", ""), r.get("club", ""),
                           r.get("category", "")))
            eid = self._x("INSERT INTO entries(race_id,lane,athlete_id) VALUES(?,?,?)",
                          (races[key], lane, aid))
            self._x("INSERT INTO results(entry_id) VALUES(?)", (eid,))
            n_ath += 1
        return {"races": len(races), "athletes": n_ath, "warnings": warnings}

    # ---- reading ------------------------------------------------------------------------------
    def _entries(self, race_id: int) -> list[dict]:
        rows = self._q(
            """SELECT e.id AS entry_id, e.lane, a.id AS athlete_id, a.bib, a.name, a.club, a.category,
                      r.auto_frame, r.final_frame, r.auto_time_ms, r.time_ms, r.place, r.status,
                      r.confidence, r.notes, r.edited
               FROM entries e JOIN athletes a ON a.id=e.athlete_id JOIN results r ON r.entry_id=e.id
               WHERE e.race_id=? ORDER BY e.lane""", (race_id,))
        out = []
        for r in rows:
            d = dict(r)
            d["time"] = format_time(d["time_ms"]) if d["time_ms"] is not None else ""
            out.append(d)
        return out

    def get_race(self, race_id: int) -> dict | None:
        rows = self._q("SELECT * FROM races WHERE id=?", (race_id,))
        if not rows:
            return None
        d = dict(rows[0])
        d["warnings"] = json.loads(d["warnings"] or "[]")
        d["analysis"] = json.loads(d["analysis"]) if d["analysis"] else None
        d["entries"] = self._entries(race_id)
        return d

    def list_races(self, session_id: int) -> list[dict]:
        out = []
        for r in self._q("SELECT * FROM races WHERE session_id=? ORDER BY ord", (session_id,)):
            d = dict(r)
            d.pop("analysis", None)
            d["warnings"] = json.loads(d["warnings"] or "[]")
            entries = self._entries(d["id"])
            if d["status"] in ("review", "locked"):
                entries.sort(key=lambda e: (e["place"] is None, e["place"] or 0, e["lane"]))
            d["entries"] = entries
            out.append(d)
        return out

    def race_session(self, race_id: int) -> int | None:
        r = self._q("SELECT session_id FROM races WHERE id=?", (race_id,))
        return r[0]["session_id"] if r else None

    # ---- recording result -----------------------------------------------------------------------
    def apply_recording(self, race_id: int, result: dict, reanalysis: bool = False) -> None:
        if reanalysis:
            self.push_history(race_id)   # manual edits can be brought back with undo
        else:
            self._undo.pop(race_id, None)
            self._redo.pop(race_id, None)
        # The status becomes 'review' only after the results are in, so readers never see half a race.
        self._x("""UPDATE races SET n_flash=?, first_idx=?, frame_count=?, fps=?, link=?,
                   delta_ms=?, recorded_at=?, analysis=?, warnings=? WHERE id=?""",
                (result["n_flash"], result["first_idx"], result["frame_count"], result["fps"]["fps"],
                 result["link"], result["delta_ms"], time.strftime("%Y-%m-%d %H:%M:%S"),
                 json.dumps(result), json.dumps(result["warnings"]), race_id))
        by_lane = {d["lane"]: d for d in result["detections"]}
        for e in self._entries(race_id):
            det = by_lane.get(e["lane"])
            frame = det["frame"] if det else None
            t = self._time_for(race_id, frame)
            self._x("""UPDATE results SET auto_frame=?, final_frame=?, auto_time_ms=?, time_ms=?, status=?,
                       confidence=?, notes=?, edited=0 WHERE entry_id=?""",
                    (frame, frame, t, t, "OK" if frame is not None else "DNF",
                     det["confidence"] if det else 0.0, "; ".join(det["notes"]) if det else "",
                     e["entry_id"]))
        extra = [d["lane"] for d in result["detections"] if d["frame"] is not None
                 and d["lane"] not in {e["lane"] for e in self._entries(race_id)}]
        if extra:
            warns = result["warnings"] + [f"Crossing detected in lane(s) without an entry: {extra}"]
            self._x("UPDATE races SET warnings=? WHERE id=?", (json.dumps(warns), race_id))
        self.recompute_places(race_id)
        self._x("UPDATE races SET status='review' WHERE id=?", (race_id,))

    def _time_for(self, race_id: int, frame) -> float | None:
        if frame is None:
            return None
        r = self._q("SELECT n_flash, fps, delta_ms FROM races WHERE id=?", (race_id,))[0]
        return race_time_ms(frame, r["n_flash"], r["fps"], r["delta_ms"])

    def recompute_places(self, race_id: int) -> None:
        rows = [e for e in self._entries(race_id)]
        ok = sorted((e for e in rows if e["status"] == "OK" and e["time_ms"] is not None),
                    key=lambda e: e["time_ms"])
        for e in rows:
            self._x("UPDATE results SET place=NULL WHERE entry_id=?", (e["entry_id"],))
        for i, e in enumerate(ok, 1):
            self._x("UPDATE results SET place=? WHERE entry_id=?", (i, e["entry_id"]))

    # ---- undo / redo (per race, in memory; covers markers, statuses, swaps and re-analysis) ------
    def _snapshot(self, race_id: int) -> list[dict]:
        return [dict(r) for r in self._q(
            """SELECT r.*, e.lane AS lane FROM results r JOIN entries e ON e.id=r.entry_id WHERE e.race_id=?""",
            (race_id,))]

    def _restore(self, race_id: int, rows: list[dict]) -> None:
        for r in rows:
            self._x("UPDATE entries SET lane=? WHERE id=?", (r["lane"], r["entry_id"]))
            self._x("""UPDATE results SET auto_frame=?, final_frame=?, auto_time_ms=?, time_ms=?, place=?,
                       status=?, confidence=?, notes=?, edited=? WHERE entry_id=?""",
                    (r["auto_frame"], r["final_frame"], r["auto_time_ms"], r["time_ms"], r["place"],
                     r["status"], r["confidence"], r["notes"], r["edited"], r["entry_id"]))

    def push_history(self, race_id: int) -> None:
        self._undo.setdefault(race_id, []).append(self._snapshot(race_id))
        del self._undo[race_id][:-100]
        self._redo[race_id] = []

    def history(self, race_id: int) -> dict:
        return {"undo": len(self._undo.get(race_id, [])), "redo": len(self._redo.get(race_id, []))}

    def undo(self, race_id: int, user: str = "operator") -> dict:
        return self._step(race_id, self._undo, self._redo, "undo", user)

    def redo(self, race_id: int, user: str = "operator") -> dict:
        return self._step(race_id, self._redo, self._undo, "redo", user)

    def _step(self, race_id, src, dst, name, user):
        self._check_unlocked(race_id)
        stack = src.get(race_id) or []
        if not stack:
            raise ValueError(f"Nothing to {name}")
        dst.setdefault(race_id, []).append(self._snapshot(race_id))
        self._restore(race_id, stack.pop())
        self._audit(user, "race", race_id, name, None, None)
        return self.get_race(race_id)

    # ---- editing ------------------------------------------------------------------------------
    def _check_unlocked(self, race_id: int) -> dict:
        race = self.get_race(race_id)
        if race is None:
            raise KeyError("race")
        if race["status"] == "locked":
            raise PermissionError("Race is locked")
        return race

    def set_marker(self, race_id: int, entry_id: int, frame: int | None, user: str = "operator") -> dict:
        race = self._check_unlocked(race_id)
        if race["n_flash"] is None:
            raise ValueError("Race has no recording")
        old = next(e for e in race["entries"] if e["entry_id"] == entry_id)
        self.push_history(race_id)
        t = self._time_for(race_id, frame)
        status = old["status"]
        if frame is not None and status in ("DNF", "DNS"):
            status = "OK"
        self._x("""UPDATE results SET final_frame=?, time_ms=?, status=?, edited=1 WHERE entry_id=?""",
                (frame, t, status, entry_id))
        self._audit(user, "result", entry_id, "final_frame", old["final_frame"], frame)
        self.recompute_places(race_id)
        return self.get_race(race_id)

    def set_lane(self, race_id: int, entry_id: int, lane: int, user: str = "operator") -> dict:
        """Moves an athlete to another lane. The result is taken from the crossing detected in that lane."""
        race = self._check_unlocked(race_id)
        if lane < 1 or lane > 16:
            raise ValueError("lane must be between 1 and 16")
        old = next(e for e in race["entries"] if e["entry_id"] == entry_id)
        if lane == old["lane"]:
            return race
        if any(e["lane"] == lane for e in race["entries"]):
            raise ValueError(f"Lane {lane} is already used in this race: change or swap that athlete first")
        self.push_history(race_id)
        self._x("UPDATE entries SET lane=? WHERE id=?", (lane, entry_id))
        if race["analysis"]:
            det = next((x for x in race["analysis"]["detections"] if x["lane"] == lane), None)
            frame = det["frame"] if det else None
            t = self._time_for(race_id, frame)
            self._x("""UPDATE results SET auto_frame=?, final_frame=?, auto_time_ms=?, time_ms=?, status=?, confidence=?,
                       notes=?, edited=1 WHERE entry_id=?""",
                    (frame, frame, t, t, "OK" if frame is not None else "DNF", det["confidence"] if det else 0.0,
                     "; ".join(det["notes"]) if det else "", entry_id))
        self._audit(user, "entry", entry_id, "lane", old["lane"], lane)
        self.recompute_places(race_id)
        return self.get_race(race_id)

    def set_status(self, race_id: int, entry_id: int, status: str, user: str = "operator") -> dict:
        if status not in STATUSES:
            raise ValueError("bad status")
        race = self._check_unlocked(race_id)
        old = next(e for e in race["entries"] if e["entry_id"] == entry_id)
        self.push_history(race_id)
        self._x("UPDATE results SET status=?, edited=1 WHERE entry_id=?", (status, entry_id))
        self._audit(user, "result", entry_id, "status", old["status"], status)
        self.recompute_places(race_id)
        return self.get_race(race_id)

    def swap_lanes_results(self, race_id: int, entry_a: int, entry_b: int, user: str = "operator") -> dict:
        """Swaps the finish frames of two entries (fixes two athletes that were mixed up)."""
        race = self._check_unlocked(race_id)
        a = next(e for e in race["entries"] if e["entry_id"] == entry_a)
        b = next(e for e in race["entries"] if e["entry_id"] == entry_b)
        self.push_history(race_id)
        for src, dst in ((a, b), (b, a)):
            self._x("UPDATE results SET final_frame=?, time_ms=?, edited=1 WHERE entry_id=?",
                    (src["final_frame"], src["time_ms"], dst["entry_id"]))
        self._audit(user, "result", entry_a, "swap_with", entry_a, entry_b)
        self.recompute_places(race_id)
        return self.get_race(race_id)

    def update_athlete(self, entry_id: int, fields: dict, user: str = "operator") -> None:
        rows = self._q("""SELECT a.* FROM entries e JOIN athletes a ON a.id=e.athlete_id WHERE e.id=?""",
                       (entry_id,))
        if not rows:
            raise KeyError("entry")
        a = rows[0]
        for k in ("bib", "name", "club", "category"):
            if k in fields and fields[k] != a[k]:
                self._x(f"UPDATE athletes SET {k}=? WHERE id=?", (fields[k], a["id"]))
                self._audit(user, "athlete", a["id"], k, a[k], fields[k])

    def set_locked(self, race_id: int, locked: bool, user: str = "operator") -> dict:
        race = self.get_race(race_id)
        if race is None or race["n_flash"] is None:
            raise ValueError("Race has no recording")
        new = "locked" if locked else "review"
        self._x("UPDATE races SET status=? WHERE id=?", (new, race_id))
        self._audit(user, "race", race_id, "status", race["status"], new)
        return self.get_race(race_id)

    def set_status_race(self, race_id: int, status: str) -> None:
        self._x("UPDATE races SET status=? WHERE id=?", (status, race_id))

    def next_scheduled(self, session_id: int) -> int | None:
        r = self._q("SELECT id FROM races WHERE session_id=? AND status='scheduled' ORDER BY ord LIMIT 1",
                    (session_id,))
        return r[0]["id"] if r else None

    def audit_log(self, limit: int = 200) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]

    # ---- export -------------------------------------------------------------------------------
    def export_rows(self, session_id: int) -> list[dict]:
        rows = []
        for race in self.list_races(session_id):
            for e in race["entries"]:
                rows.append({
                    "event": race["event"], "heat": race["heat"], "race_status": race["status"],
                    "place": e["place"] if e["place"] is not None else "",
                    "lane": e["lane"], "bib": e["bib"], "name": e["name"], "club": e["club"],
                    "category": e["category"], "time": e["time"], "time_ms": (
                        round(e["time_ms"], 1) if e["time_ms"] is not None else ""),
                    "status": e["status"], "edited": "yes" if e["edited"] else "",
                })
        return rows

    def export_csv(self, session_id: int) -> str:
        rows = self.export_rows(session_id)
        buf = io.StringIO()
        fields = ["event", "heat", "race_status", "place", "lane", "bib", "name", "club", "category",
                  "time", "time_ms", "status", "edited"]
        w = csv.DictWriter(buf, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
        return buf.getvalue()
