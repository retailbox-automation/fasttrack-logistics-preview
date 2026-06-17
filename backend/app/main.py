"""
Fast Track Platform — FastAPI backend.
Phase 1A: persistence layer for prototype data model.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import init_db, get_db
from app.routers import inventory
from app.schemas import HealthOut


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


@app.get("/", tags=["root"])
def root():
    return {
        "name": settings.api_title,
        "version": settings.api_version,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", response_model=HealthOut, tags=["root"])
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_status = "up"
    except Exception as e:
        db_status = f"down: {type(e).__name__}"
    return HealthOut(status="ok", db=db_status, version=settings.api_version)


# Register routers
app.include_router(inventory.router)
