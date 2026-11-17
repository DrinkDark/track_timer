"""Entry point: python -m timing_app.main  (env: TIMING_SOURCE=agent|sim, TIMING_DATA, PORT)."""
import os

import uvicorn

from .api import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")))
