# Fast Track Platform — Backend

FastAPI backend for the FT MSC operations platform. Persistence layer for
the prototype data model (inventory, shipments, SDR, invoices, credit
memos, discrepancy reports, statement of account).

## Stack

- Python 3.12, FastAPI 0.115
- SQLAlchemy 2.0 + psycopg (PostgreSQL driver)
- Pydantic v2 schemas
- Uvicorn (ASGI server)
- Containerized via Dockerfile for Zeabur deploy

## Local dev

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DATABASE_URL='postgresql+psycopg://user:pass@localhost:5432/ft'
uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for interactive API docs.

## Environment variables

| Var | Description |
|---|---|
| `DATABASE_URL` | Postgres connection string. On Zeabur, set to `${POSTGRES_CONNECTION_STRING}` reference (auto-injected from Postgres service) |
| `CORS_ORIGINS` | Comma-separated allowed origins for browser CORS. Default includes GitHub Pages prototype URL |

## Deployment (Zeabur)

This subfolder builds via the included `Dockerfile`. Zeabur auto-detects
on git push to `main`. Configure service:

1. Service → Source → GitHub → `retailbox-automation/fasttrack-logistics-preview`
2. Root directory: `backend/`
3. Variables → `DATABASE_URL` = `${POSTGRES_CONNECTION_STRING}` (reference)
4. Networking → expose port 8000 → assign domain
5. Push to main → auto-deploy

## API surface (Phase 1A)

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Root info |
| `/health` | GET | Liveness + DB ping |
| `/docs` | GET | Interactive Swagger UI |
| `/api/inventory` | GET | List items (filter: q, department, status_filter) |
| `/api/inventory` | POST | Create item (enforces pallet 2000 lb rule) |
| `/api/inventory/{id}` | GET | Get one |
| `/api/inventory/{id}` | PATCH | Update |
| `/api/inventory/{id}` | DELETE | Remove |
| `/api/inventory/{id}/move` | POST | Relocate with audit log |
| `/api/inventory/bulk-import` | POST | Bulk insert (CSV import path) |

More endpoints (loading lists, SDR, invoices, credit memos, SOA) follow
the same pattern — coming after inventory verifies live data flow.
