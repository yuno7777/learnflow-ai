# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libmagic/poppler are not needed: extraction is pure Python (pypdf, python-docx).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl build-essential \
 && rm -rf /var/lib/apt/lists/*

# Dependency layer first so source edits do not invalidate the wheel cache.
COPY pyproject.toml ./
COPY learnflow/__init__.py ./learnflow/__init__.py
RUN pip install --upgrade pip && pip install -e .

COPY . .

RUN useradd --create-home --uid 10001 learnflow \
 && mkdir -p /data/uploads \
 && chown -R learnflow:learnflow /app /data
USER learnflow

EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "learnflow.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS worker
USER learnflow
HEALTHCHECK NONE
CMD ["arq", "learnflow.worker.main.WorkerSettings"]
