"""
Fast Track Platform — FastAPI backend + frontend static serve.
Phase 1A: persistence layer for prototype data model + bundled UI.
"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import init_db, get_db
from app.routers import inventory
from app.schemas import HealthOut
from app.auth import router as auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-create tables on first boot (Phase 1A — Alembic migrations later)
    init_db()
    yield


app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthOut, tags=["root"])
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_status = "up"
    except Exception as e:
        db_status = f"down: {type(e).__name__}"
    return HealthOut(status="ok", db=db_status, version=settings.api_version)


# Register routers
app.include_router(auth_router)
app.include_router(inventory.router)


# Serve frontend (index.html bundled into the image at /app/static/index.html)
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
INDEX_PATH = os.path.join(STATIC_DIR, "index.html")


@app.get("/", include_in_schema=False)
def serve_index():
    if os.path.exists(INDEX_PATH):
        return FileResponse(INDEX_PATH, media_type="text/html")
    return {
        "name": settings.api_title,
        "version": settings.api_version,
        "docs": "/docs",
        "health": "/health",
    }


if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
