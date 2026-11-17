FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    TIMING_DATA=/data TIMING_UI=/app/frontend HOST=0.0.0.0 PORT=8000
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt
COPY backend/timing_app /app/timing_app
COPY frontend /app/frontend
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "timing_app.main"]
