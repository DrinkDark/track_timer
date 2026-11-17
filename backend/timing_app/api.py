"""FastAPI application: REST API, live preview, frame ingest from the capture agent, static UI."""
from __future__ import annotations

import asyncio
import json
import os
import random
import struct
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
import numpy as np
from fastapi import Body, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import Database
from .export_pdf import build_pdf
from .framestore import FrameReader
from .pipeline import Pipeline, rotate_img
from .simulator import SimScene, SimulatedSource

HEADER = struct.Struct("<Qd")   # frame counter (u64), host time (f64), then the JPEG bytes


def create_app(data_dir: str | Path | None = None, source: str | None = None,
               sim_paced: bool = True) -> FastAPI:
    data = Path(data_dir) if data_dir else config.data_dir()
    data.mkdir(parents=True, exist_ok=True)
    source = source or os.environ.get("TIMING_SOURCE", "agent")

    settings = config.JsonStore(data / "settings.json", config.DEFAULT_SETTINGS)
    calibration = config.JsonStore(data / "calibration.json", config.DEFAULT_CALIBRATION)
    db = Database(data / "timing.db")

    scene: SimScene | None = None
    if source == "sim":
        scene = SimScene(width=int(os.environ.get("SIM_W", 360)), height=int(os.environ.get("SIM_H", 480)),
                         fps=float(os.environ.get("SIM_FPS", 120)))
        if not (data / "settings.json").exists():
            settings.update(scene.default_settings())

    pipeline = Pipeline(data, settings.get, calibration.get,
                        on_recorded=lambda rid, result: db.apply_recording(rid, result))
    state = {"agent_connected": False, "sim": None}

    # Races left "armed" by a previous run go back to what they were.
    for r in db._q("SELECT id, n_flash FROM races WHERE status IN ('armed')"):
        db.set_status_race(r["id"], "review" if r["n_flash"] is not None else "scheduled")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pipeline.start()
        if scene is not None:
            src = SimulatedSource(scene, pipeline, paced=sim_paced)
            state["sim"] = src
            src.start()
        yield
        if state["sim"]:
            state["sim"].stop()
        pipeline.stop()

    app = FastAPI(title="Track timing", lifespan=lifespan)
    app.state.pipeline = pipeline
    app.state.db = db
    app.state.settings = settings
    app.state.calibration = calibration
    app.state.scene = scene
    app.state.sim_state = state

    def user_of(request: Request) -> str:
        return request.headers.get("x-user", "operator")

    def rec_dir(race_id: int) -> Path:
        return data / "races" / str(race_id)

    # ---- state, settings, calibration -----------------------------------------------------
    @app.get("/api/state")
    def get_state():
        snap = pipeline.snapshot()
        snap["source"] = source
        snap["agent_connected"] = state["agent_connected"]
        return snap

    @app.get("/api/settings")
    def get_settings():
        return settings.get()

    @app.put("/api/settings")
    def put_settings(patch: dict = Body(...)):
        return settings.update(patch)

    @app.get("/api/calibration")
    def get_calibration():
        return calibration.get()

    @app.put("/api/calibration")
    def put_calibration(patch: dict = Body(...)):
        return calibration.update(patch)

    # ---- sessions, start list, races -------------------------------------------------------
    @app.get("/api/sessions")
    def sessions():
        return db.list_sessions()

    @app.post("/api/sessions")
    def new_session(body: dict = Body(...)):
        sid = db.create_session(body.get("name") or "Session", body.get("date"))
        return {"id": sid}

    @app.post("/api/sessions/{sid}/startlist")
    async def startlist(sid: int, request: Request):
        raw = (await request.body()).decode("utf-8-sig", errors="replace")
        if raw.lstrip().startswith("{"):
            raw = json.loads(raw).get("csv", "")
        try:
            return db.import_startlist(sid, raw)
        except KeyError:
            raise HTTPException(404, "session not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/sessions/{sid}/races")
    def races(sid: int):
        return db.list_races(sid)

    @app.post("/api/sessions/{sid}/races")
    def new_race(sid: int, body: dict = Body(...)):
        n = int(body.get("lanes", 4))
        rid = db.create_race(sid, body.get("event") or "Race", str(body.get("heat") or "1"),
                             list(range(1, n + 1)))
        return {"id": rid}

    @app.get("/api/races/{rid}")
    def race(rid: int):
        r = db.get_race(rid)
        if r is None:
            raise HTTPException(404, "race not found")
        return r

    # ---- arming ----------------------------------------------------------------------------
    @app.post("/api/races/{rid}/arm")
    def arm(rid: int):
        r = db.get_race(rid)
        if r is None:
            raise HTTPException(404, "race not found")
        if r["status"] == "locked":
            raise HTTPException(409, "Race is locked")
        if pipeline.state in ("recording", "analysing"):
            raise HTTPException(409, "A recording is in progress")
        # Only one race can be armed.
        if pipeline.state == "armed" and pipeline.race_id is not None and pipeline.race_id != rid:
            prev = db.get_race(pipeline.race_id)
            if prev:
                db.set_status_race(prev["id"], "review" if prev["n_flash"] is not None else "scheduled")
        pipeline.arm(rid)
        db.set_status_race(rid, "armed")
        return {"ok": True}

    @app.post("/api/disarm")
    def disarm():
        rid = pipeline.race_id
        pipeline.disarm()
        if rid is not None:
            r = db.get_race(rid)
            if r and r["status"] == "armed":
                db.set_status_race(rid, "review" if r["n_flash"] is not None else "scheduled")
        return {"ok": True}

    # ---- review and edits ------------------------------------------------------------------
    @app.put("/api/races/{rid}/entries/{eid}/marker")
    def marker(rid: int, eid: int, request: Request, body: dict = Body(...)):
        try:
            frame = body.get("frame")
            if frame is not None and (isinstance(frame, bool) or not isinstance(frame, (int, float))):
                raise ValueError("frame must be a number or null")
            return db.set_marker(rid, eid, None if frame is None else int(frame), user_of(request))
        except PermissionError as exc:
            raise HTTPException(409, str(exc))
        except (ValueError, KeyError, StopIteration) as exc:
            raise HTTPException(400, str(exc))

    @app.put("/api/races/{rid}/entries/{eid}/lane")
    def lane(rid: int, eid: int, request: Request, body: dict = Body(...)):
        try:
            return db.set_lane(rid, eid, int(body.get("lane")), user_of(request))
        except PermissionError as exc:
            raise HTTPException(409, str(exc))
        except (ValueError, TypeError, KeyError, StopIteration) as exc:
            raise HTTPException(400, str(exc) or "bad lane")

    @app.put("/api/races/{rid}/entries/{eid}/status")
    def status(rid: int, eid: int, request: Request, body: dict = Body(...)):
        try:
            return db.set_status(rid, eid, body.get("status", ""), user_of(request))
        except PermissionError as exc:
            raise HTTPException(409, str(exc))
        except (ValueError, KeyError, StopIteration) as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/races/{rid}/swap")
    def swap(rid: int, request: Request, body: dict = Body(...)):
        try:
            return db.swap_lanes_results(rid, int(body["a"]), int(body["b"]), user_of(request))
        except PermissionError as exc:
            raise HTTPException(409, str(exc))
        except (ValueError, KeyError, StopIteration) as exc:
            raise HTTPException(400, str(exc))

    @app.put("/api/entries/{eid}/athlete")
    def athlete(eid: int, request: Request, body: dict = Body(...)):
        try:
            db.update_athlete(eid, body, user_of(request))
        except KeyError:
            raise HTTPException(404, "entry not found")
        return {"ok": True}

    @app.post("/api/races/{rid}/lock")
    def lock(rid: int, request: Request, body: dict = Body(default={"locked": True})):
        try:
            return db.set_locked(rid, bool(body.get("locked", True)), user_of(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/races/{rid}/reanalyze")
    def reanalyze(rid: int):
        from . import analysis
        r = db.get_race(rid)
        if r is None or r["n_flash"] is None:
            raise HTTPException(400, "Race has no recording")
        if r["status"] == "locked":
            raise HTTPException(409, "Race is locked")
        result = analysis.analyze(rec_dir(rid), settings.get(), calibration.get())
        db.apply_recording(rid, result, reanalysis=True)
        return db.get_race(rid)

    @app.post("/api/races/{rid}/undo")
    def undo(rid: int, request: Request):
        try:
            return db.undo(rid, user_of(request))
        except PermissionError as exc:
            raise HTTPException(409, str(exc))
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/races/{rid}/redo")
    def redo(rid: int, request: Request):
        try:
            return db.redo(rid, user_of(request))
        except PermissionError as exc:
            raise HTTPException(409, str(exc))
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/races/{rid}/history")
    def history(rid: int):
        return db.history(rid)

    @app.get("/api/audit")
    def audit():
        return db.audit_log()

    # ---- media -----------------------------------------------------------------------------
    @app.get("/api/races/{rid}/strip.jpg")
    def strip(rid: int):
        p = rec_dir(rid) / "strip.jpg"
        if not p.exists():
            raise HTTPException(404, "no strip")
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})

    @app.get("/api/races/{rid}/frame/{n}.jpg")
    def frame(rid: int, n: int):
        try:
            reader = FrameReader(rec_dir(rid))
            jpeg = reader.get(n)
        except (OSError, IndexError):
            raise HTTPException(404, "frame not found")
        r = db.get_race(rid)
        rotate = (r["analysis"] or {}).get("rotate", 0) if r else 0
        if rotate:
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
            ok, buf = cv2.imencode(".jpg", rotate_img(img, rotate), [cv2.IMWRITE_JPEG_QUALITY, 88])
            jpeg = buf.tobytes()
        return Response(jpeg, media_type="image/jpeg")

    @app.get("/api/live.jpg")
    def live_jpg():
        if pipeline.preview is None:
            raise HTTPException(404, "no frame yet")
        return Response(pipeline.preview, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/live.mjpg")
    async def live_mjpg():
        async def gen():
            last = None
            while True:
                p = pipeline.preview
                if p is not None and p is not last:
                    last = p
                    yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(p)).encode() \
                        + b"\r\n\r\n" + p + b"\r\n"
                await asyncio.sleep(0.08)
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    # ---- export ----------------------------------------------------------------------------
    @app.get("/api/sessions/{sid}/export.csv")
    def export_csv(sid: int):
        return Response(db.export_csv(sid), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="results_{sid}.csv"'})

    @app.get("/api/sessions/{sid}/export.pdf")
    def export_pdf(sid: int):
        sess = next((s for s in db.list_sessions() if s["id"] == sid), None)
        if sess is None:
            raise HTTPException(404, "session not found")
        pdf = build_pdf(sess, db.list_races(sid), data)
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="results_{sid}.pdf"'})

    # ---- frame ingest from the capture agent -----------------------------------------------
    @app.websocket("/ws/ingest")
    async def ingest(ws: WebSocket):
        await ws.accept()
        state["agent_connected"] = True
        try:
            while True:
                msg = await ws.receive_bytes()
                if len(msg) <= HEADER.size:
                    continue
                idx, t_host = HEADER.unpack_from(msg, 0)
                pipeline.push_frame(int(idx), float(t_host), msg[HEADER.size:])
        except WebSocketDisconnect:
            pass
        finally:
            state["agent_connected"] = False

    # ---- simulator -------------------------------------------------------------------------
    @app.post("/api/sim/start")
    def sim_start(body: dict = Body(default={})):
        src = state["sim"]
        if src is None:
            raise HTTPException(400, "Simulator is not the active source")
        if pipeline.state in ("recording", "analysing"):
            raise HTTPException(409, "A recording is in progress")
        n = len(scene_lanes(settings.get()))
        times = body.get("times")
        if times:
            times = {int(k): float(v) for k, v in times.items()}
        else:
            base = random.uniform(10.8, 12.4)
            offs = random.sample([0.0, 0.31, 0.62, 0.97, 1.35, 1.72], n)
            times = {lane: round(base + offs[lane - 1], 3) for lane in range(1, n + 1)}
        tau0 = src.trigger(float(body.get("delay_s", 1.0)), times, body.get("link", "radio"))
        return {"tau0": tau0, "times": times}

    def scene_lanes(s: dict):
        return s["lanes"]

    # ---- UI --------------------------------------------------------------------------------
    ui = Path(os.environ.get("TIMING_UI", Path(__file__).resolve().parents[2] / "frontend"))
    if not ui.exists():
        ui = Path("/app/frontend")
    if ui.exists():
        app.mount("/", StaticFiles(directory=str(ui), html=True), name="ui")
    return app
