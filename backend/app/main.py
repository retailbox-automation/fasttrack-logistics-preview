"""
Fast Track Platform — FastAPI backend + frontend static serve.
Phase 1A: persistence layer for prototype data model + bundled UI.
"""
import logging
import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import init_db, get_db
from app.routers import inventory
from app.schemas import HealthOut
from app.auth import router as auth_router

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("ft")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Lifespan start — initializing DB tables")
    try:
        init_db()
        log.info("DB init OK")
    except Exception as e:
        log.exception("DB init FAILED: %s", e)
        # Don't crash the app — health endpoint will report db down
    yield
    log.info("Lifespan shutdown")


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


# Serve frontend index.html bundled into the image
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
INDEX_PATH = os.path.join(STATIC_DIR, "index.html")
log.info("STATIC_DIR=%s exists=%s INDEX_PATH=%s exists=%s",
         STATIC_DIR, os.path.isdir(STATIC_DIR), INDEX_PATH, os.path.exists(INDEX_PATH))


@app.get("/", include_in_schema=False)
def serve_index():
    if os.path.exists(INDEX_PATH):
        return FileResponse(INDEX_PATH, media_type="text/html")
    return {
        "name": settings.api_title,
        "version": settings.api_version,
        "docs": "/docs",
        "health": "/health",
        "note": "index.html not bundled — frontend served from /static when present",
    }


@app.get("/_debug", tags=["root"])
def debug_fs():
    """Debug — what's in the container filesystem."""
    info = {
        "cwd": os.getcwd(),
        "__file__": __file__,
        "STATIC_DIR": STATIC_DIR,
        "STATIC_DIR_exists": os.path.isdir(STATIC_DIR),
        "INDEX_PATH": INDEX_PATH,
        "INDEX_PATH_exists": os.path.exists(INDEX_PATH),
        "app_root": os.listdir("/app") if os.path.isdir("/app") else None,
        "DATABASE_URL_prefix": (settings.database_url or "")[:40] + "...",
    }
    return info
