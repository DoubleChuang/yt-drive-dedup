FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so editing code does not invalidate the heavy pip layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# --no-deps: the runtime dependencies are already installed above, so this is a
# fast copy of the package into site-packages instead of a second resolve.
COPY pyproject.toml README.md ./
COPY ytdd ./ytdd
RUN pip install --no-cache-dir --no-deps .

# Unprivileged by default; compose overrides uid/gid with the host ids so the
# files written to the mounted volume stay owned by whoever ran it.
RUN useradd --create-home --uid 1000 app \
 && mkdir -p /data && chown app:app /data

USER app
WORKDIR /data

ENTRYPOINT ["python", "-m", "ytdd"]
CMD ["--help"]
