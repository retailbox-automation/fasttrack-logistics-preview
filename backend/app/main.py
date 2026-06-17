"""
Fast Track Platform — FastAPI backend + frontend static serve.
Phase 1B: per-user accounts + roles + audit + optimistic locking.
"""
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pythonjsonlogger import jsonlogger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import init_db, get_db, SessionLocal
from app.routers import inventory, docs
from app.routers import users as users_router
from app.routers import audit as audit_router
from app.schemas import HealthOut
from app.auth import router as auth_router, limiter, hash_password
from app.models import User


# ── Structured JSON logging (per-request id, latency) ──
_root = logging.getLogger()
for h in list(_root.handlers):
    _root.removeHandler(h)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(jsonlogger.JsonFormatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s",
    rename_fields={"asctime": "ts", "levelname": "level"},
))
_root.addHandler(_handler)
_root.setLevel(logging.INFO)
log = logging.getLogger("ft")


# ── Default user seed: created if table is empty ──
DEFAULT_USERS = [
    {"email": "andres@fasttrackgroup.us", "name": "Andrés Yeguez", "role": "admin"},
    {"email": "a.sultan@fasttrackgroup.us", "name": "Adam Sultan", "role": "admin"},
    {"email": "gabriela@fasttrackgroup.us", "name": "Gabriela Pita", "role": "manager"},
    {"email": "luis@fasttrackgroup.us", "name": "Luis Cruz", "role": "manager"},
    {"email": "andrea@fasttrackgroup.us", "name": "Andrea Palmisano", "role": "ops"},
    {"email": "yamisley@fasttrackgroup.us", "name": "Yamisley Barros", "role": "ops"},
]


def seed_users_if_empty():
    """First-boot seed. Default password = SEED_DEFAULT_PASSWORD env (else AUTH_PASSWORD)."""
    db = SessionLocal()
    try:
        count = db.query(User).count()
        if count > 0:
            log.info("seed_skip", extra={"existing_users": count})
            return
        default_pw = os.getenv("SEED_DEFAULT_PASSWORD") or settings.auth_password
        for u in DEFAULT_USERS:
            db.add(User(
                email=u["email"],
                name=u["name"],
                role=u["role"],
                password_hash=hash_password(default_pw),
                is_active=True,
            ))
        db.commit()
        log.info("seed_users_created", extra={"count": len(DEFAULT_USERS)})
    except Exception as e:
        log.exception("seed_users_failed: %s", e)
        db.rollback()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("lifespan_start")
    try:
        init_db()
        log.info("db_init_ok")
        seed_users_if_empty()
    except Exception as e:
        log.exception("db_init_failed: %s", e)
    yield
    log.info("lifespan_shutdown")


_is_prod = os.getenv("ENV", "production").lower() == "production"
_disabled_in_prod = {
    "docs_url": None if _is_prod else "/docs",
    "redoc_url": None if _is_prod else "/redoc",
    "openapi_url": None if _is_prod else "/openapi.json",
}

app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    lifespan=lifespan,
    **_disabled_in_prod,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_log(request: Request, call_next):
    rid = uuid.uuid4().hex[:10]
    request.state.request_id = rid
    started = time.monotonic()
    try:
        response = await call_next(request)
        latency_ms = int((time.monotonic() - started) * 1000)
        log.info("req", extra={
            "rid": rid,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": latency_ms,
            "ip": request.client.host if request.client else None,
        })
        response.headers["X-Request-ID"] = rid
        return response
    except Exception as e:
        log.exception("req_error rid=%s path=%s", rid, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error", "request_id": rid})


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
app.include_router(users_router.router)
app.include_router(audit_router.router)
app.include_router(inventory.router)
app.include_router(docs.router)


# Serve frontend index.html bundled into the image
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
INDEX_PATH = os.path.join(STATIC_DIR, "index.html")


@app.get("/", include_in_schema=False)
def serve_index():
    if os.path.exists(INDEX_PATH):
        return FileResponse(INDEX_PATH, media_type="text/html")
    return {
        "name": settings.api_title,
        "version": settings.api_version,
        "health": "/health",
        "note": "index.html not bundled",
    }
