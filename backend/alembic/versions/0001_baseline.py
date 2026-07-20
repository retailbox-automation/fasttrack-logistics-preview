"""baseline — schema as managed by create_all() up to 2026-07-20

This is a marker revision. The live schema is created/ensured at startup by
`Base.metadata.create_all()` + the idempotent ALTERs in `app.database.init_db()`.
The baseline is a no-op so the existing production DB can be *stamped* at this
revision (no DDL run), giving future Alembic migrations a base to chain from.

Autogenerate still works from here: `alembic revision --autogenerate` reflects the
ACTUAL database (not this chain), so it emits only real diffs against the models.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-20
"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op: baseline schema is ensured by create_all() + init_db() idempotent ALTERs.
    pass


def downgrade() -> None:
    # No destructive baseline downgrade.
    pass
