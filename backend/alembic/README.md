# Alembic migrations

## Current state (2026-07-20)

Alembic is set up as the **migration foundation**, but the **runtime schema is still
ensured by `app.database.init_db()`** — `Base.metadata.create_all()` (creates missing
tables) + a list of idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. That path is
unchanged and remains the source of truth for now.

On startup, `_ensure_alembic_baseline()` **stamps** the DB at revision `0001_baseline`
if it isn't tracked yet (writes only `alembic_version`, no DDL). So the production DB is
tracked and future migrations have a base to chain from. The Dockerfile copies this
`alembic/` dir + `alembic.ini` into the image, so the `alembic` CLI is runnable in-container.

`env.py` points at `app.database.Base.metadata` and uses `settings.database_url`, so
autogenerate compares the models to the **actual DB** and emits only real diffs.

## Making a schema change

Short term (current hybrid) — keep the two in sync:
1. Add/adjust the model in `app/models.py`.
2. Add the column to the `add_column_if_missing` list in `app/database.py` (idempotent ALTER)
   — this is what actually applies at runtime today.
3. (For the record / future) generate a migration in-container and commit it:
   ```
   alembic revision --autogenerate -m "add X"
   ```

## Applying migrations (in-container, when we move off the hybrid)

```
alembic current          # show tracked revision
alembic history          # chain
alembic upgrade head     # apply pending migrations
alembic stamp head       # mark applied without running (for retrofits)
```

## Transition plan (deliberate, needs a staging DB)

To make Alembic the sole source of truth: stop calling `create_all()` + the ALTER list at
startup and instead run `alembic upgrade head`. This must be validated against a **staging
copy of the production DB first** (existing columns would collide with naive `add_column`
migrations), so it is intentionally **not** wired into startup yet.
