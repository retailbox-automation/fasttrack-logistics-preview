import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from typing import Generator
from app.config import settings

log = logging.getLogger("ft.db")


class Base(DeclarativeBase):
    pass


# Zeabur Postgres uses postgresql:// URL — convert to psycopg driver
db_url = settings.database_url
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(db_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Auto-create tables on startup + ad-hoc ALTER column add-ons.

    create_all() creates missing tables but never modifies existing schema.
    For columns added after first deploy, we run idempotent ALTERs here.
    Use Alembic for future schema changes.
    """
    from app import models  # noqa: F401 — import to register models
    Base.metadata.create_all(bind=engine)

    # Idempotent ALTERs for columns added post-initial-deploy
    add_column_if_missing = [
        ("documents", "version", "INTEGER NOT NULL DEFAULT 1"),
        ("email_messages", "body_content", "TEXT"),
        ("loading_lists", "meta", "JSONB"),
        ("loading_lists", "totals", "JSONB"),
        ("users", "must_change_password", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("invoices", "sdr_public_id", "VARCHAR(32)"),
        ("invoices", "loading_list_public_id", "VARCHAR(32)"),
        ("shipment_detail_reports", "loading_list_public_id", "VARCHAR(32)"),
        ("discrepancy_reports", "loading_list_public_id", "VARCHAR(32)"),
        ("loading_lists", "current_location", "VARCHAR(128)"),
        ("loading_lists", "tracking_status", "VARCHAR(24)"),
        ("loading_lists", "located_at", "TIMESTAMP"),
        ("loading_lists", "tracking_note", "TEXT"),
        ("users", "reset_token_hash", "VARCHAR(255)"),
        ("users", "reset_token_expires", "TIMESTAMP"),
        ("loading_lists", "current_lat", "DOUBLE PRECISION"),
        ("loading_lists", "current_lng", "DOUBLE PRECISION"),
        ("loading_lists", "driver_token_hash", "VARCHAR(64)"),
        ("loading_lists", "driver_token_expires", "TIMESTAMP"),
        ("inventory_items", "barcode", "VARCHAR(64)"),
    ]
    with engine.begin() as conn:
        for tbl, col, defn in add_column_if_missing:
            try:
                conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} {defn}"))
                log.info("schema_ensure", extra={"table": tbl, "column": col})
            except Exception as e:
                log.warning("schema_ensure_failed table=%s col=%s err=%s", tbl, col, e)

    _ensure_alembic_baseline()


_ALEMBIC_BASELINE_REV = "0001_baseline"  # must match alembic/versions/0001_baseline.py


def _ensure_alembic_baseline():
    """Stamp the DB at the Alembic baseline if it isn't tracked yet, so future Alembic
    migrations have a base to chain from. Writes only the alembic_version marker — NO schema
    DDL (the schema is ensured above by create_all + idempotent ALTERs).

    Done with direct SQL rather than alembic's `command.stamp`: stamping is literally
    `CREATE TABLE alembic_version (...)` + `INSERT version_num`, and doing it directly avoids
    depending on the alembic Config / env.py / script-discovery machinery inside the container
    (which is brittle and CWD-sensitive). `alembic current`/`upgrade` in-container then read
    this exactly as if `alembic stamp head` had run. Never raises — supplementary to the
    runtime create_all path, so a failure must not break startup."""
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
            existing = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
            if existing is None:
                conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                             {"v": _ALEMBIC_BASELINE_REV})
                log.info("alembic_baseline_stamped", extra={"revision": _ALEMBIC_BASELINE_REV})
    except Exception as e:
        log.warning("alembic_baseline_skipped err=%s", e)
