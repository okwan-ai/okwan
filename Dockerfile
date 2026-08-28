# Pinned to 3.12 deliberately. The Codespace drifted to 3.14 and the repo
# has only ever been designed against 3.12 — production should not be the
# place that divergence surfaces.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps ./apps

RUN pip install --no-cache-dir -e .

# Render supplies PORT; default for local `docker run`.
ENV PORT=8000
EXPOSE 8000

# One worker per instance: DuckDB sessions are per-request and in-memory,
# so several workers in one container multiply peak memory without adding
# throughput. Scale by adding instances, not workers.
CMD uvicorn okwan_api.main:app --host 0.0.0.0 --port ${PORT} --workers 1
