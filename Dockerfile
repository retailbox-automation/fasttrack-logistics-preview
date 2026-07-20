# Root Dockerfile for Zeabur build — delegates to backend/ subfolder
# Zeabur builds from repo root by default; this picks up backend/ source.

FROM python:3.12-slim

WORKDIR /app

# System deps for psycopg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app

# Alembic migrations (baseline + future revisions) + config — present so the DB can be
# stamped/migrated in-container. Runtime schema is still ensured by create_all()+init_db().
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini

# Bake frontend static assets into the same image — FastAPI mounts /static and / -> index.html
RUN mkdir -p ./static
COPY index.html ./static/index.html
RUN echo "=== /app ===" && ls -la /app/ && echo "=== /app/static ===" && ls -la /app/static/ || true

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
