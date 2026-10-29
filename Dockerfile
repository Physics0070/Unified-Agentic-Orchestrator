# Single always-on service: builds the React frontend, then serves it and the
# FastAPI backend from one uvicorn process (see render.yaml).
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
# libmagic is needed by python-magic (upload type sniffing).
RUN apt-get update && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/*
# api/requirements.txt = runtime deps (no torch/datasets/dev tools); add the
# ASGI server and libmagic binding the Vercel function does not need.
COPY api/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt "uvicorn[standard]==0.52.1" python-magic==0.4.27
COPY backend/ backend/
COPY migrations/ migrations/
COPY --from=frontend /app/frontend/dist frontend/dist
RUN useradd --create-home app && mkdir -p /app/data && chown -R app /app/data
USER app
EXPOSE 10000
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
