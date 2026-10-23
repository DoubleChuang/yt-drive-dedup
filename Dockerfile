FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so code edits do not invalidate the pip layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY yt_drive_dedup.py auth.py ./

# Unprivileged by default; docker-compose.yml overrides uid/gid with your host
# ids so files written to the mounted volume stay yours.
RUN useradd --create-home --uid 1000 app \
 && mkdir -p /data && chown app:app /data

USER app
WORKDIR /data

ENTRYPOINT ["python", "/app/yt_drive_dedup.py"]
CMD ["--help"]
