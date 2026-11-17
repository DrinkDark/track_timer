# Track timing – backend, web UI, capture agent

Photo-finish timing: the beacon flash marks t0 in the video, finishers are detected on the finish-line column, results can be corrected by hand and exported (CSV/PDF).

## Layout
- `backend/timing_app/` FastAPI + OpenCV + SQLite (API, detection, analysis, export)
- `frontend/` single-screen web UI (vanilla JS)
- `capture-agent/` host program that reads the USB camera and streams JPEG frames to the backend
- `Dockerfile`, `docker-compose.yml` backend + UI container
- `firmware/` ESP32-S3 firmware for the beacon and the start unit (see `firmware/README.md`)
- `tests/` simulator-based end-to-end and unit tests (`pytest tests`)

## Run with the simulator (no camera)
    TIMING_SOURCE=sim docker compose up --build
Open http://localhost:8000, click **+ Quick race**, **Arm**, then **Simulate start**.

## Run with the camera
1. Start the backend: `docker compose up --build` (Linux, Windows, macOS).
2. Start the agent on the host (the camera must be read on the host; Docker Desktop on Windows/macOS cannot pass USB cameras to containers):

        cd capture-agent && pip install -r requirements.txt
        python agent.py --camera 0 --width 1280 --height 800 --fps 120

   Linux only, alternative: `docker compose --profile camera up --build` (passes /dev/video0).
3. In the UI use **Place on image** to set the finish line, the start/ref/link LED boxes and the lane bands, then **Calibration** for the latency values.

Data (database, recordings, settings, calibration) is stored in `./data`.

## Without Docker
    pip install -r backend/requirements.txt
    cd backend && TIMING_SOURCE=sim TIMING_UI=../frontend python -m timing_app.main

## Start list CSV
Columns: `event,heat,lane,bib,name,club,category`.

## Keys in the review strip
Select an athlete in the results, click the strip to place the finish marker, `←`/`→` move one frame (Shift: 10), `Del` removes the marker, `Esc` clears the free cursor.

## Not verified yet
The Docker build was not run in the development sandbox (no Docker daemon). Camera capture with the real OV9281 is untested; only the simulator was used.
