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
    ]
    with engine.begin() as conn:
        for tbl, col, defn in add_column_if_missing:
            try:
                conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} {defn}"))
                log.info("schema_ensure", extra={"table": tbl, "column": col})
            except Exception as e:
                log.warning("schema_ensure_failed table=%s col=%s err=%s", tbl, col, e)
