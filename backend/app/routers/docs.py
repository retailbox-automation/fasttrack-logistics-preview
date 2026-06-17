"""Generic per-kind document storage.

Each prototype module persists its array via PUT /api/docs/{kind}, fetched via
GET /api/docs/{kind}. The whole-array replacement is intentional: Phase 1A
operates at low volume and prefers code simplicity over per-row diff sync.

Concurrency: GET returns `version` header; PUT requires `If-Match` to match
the server's current version. Mismatch → 409 (concurrent write). Frontend
falls back to reload-and-retry.

ALLOWED_KINDS guards against arbitrary user-supplied kinds.
Permission: viewer = read-only. ops/manager/admin can mutate.
"""
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Header
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import require_auth, require_roles
from app.audit import log_audit
from app.database import get_db
from app.events import broadcast
from app.models import Document


ALLOWED_KINDS = {
    "loading_lists",
    "sdrs",
    "invoices",
    "credit_memos",
    "discrepancy_reports",
    "notifications",
    "emails",
    "barcode_history",
    "ct_requests",
    "ct_audit_trail",
    "shipments",
}


router = APIRouter(prefix="/api/docs", tags=["docs"])


class DocsReplaceIn(BaseModel):
    items: list[Any]


def _check_kind(kind: str) -> None:
    if kind not in ALLOWED_KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown kind '{kind}'. Allowed: {sorted(ALLOWED_KINDS)}")


def _kind_version(db: Session, kind: str) -> int:
    """Aggregate version across rows of a kind. Increments by 1 each PUT."""
    v = db.query(func.coalesce(func.max(Document.version), 0)).filter(Document.kind == kind).scalar()
    return int(v or 0)


@router.get("/{kind}", response_model=list, dependencies=[Depends(require_auth)])
def list_docs(kind: str, response: Response, db: Session = Depends(get_db)):
    _check_kind(kind)
    rows = (
        db.query(Document)
        .filter(Document.kind == kind)
        .order_by(Document.seq.asc())
        .all()
    )
    response.headers["ETag"] = f'"{_kind_version(db, kind)}"'
    return [r.data for r in rows]


@router.put("/{kind}", response_model=dict)
def replace_docs(
    kind: str,
    payload: DocsReplaceIn,
    request: Request,
    db: Session = Depends(get_db),
    claims: dict = Depends(require_roles("admin", "manager", "ops")),
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
):
    """Replace ALL documents for this kind. Frontend sends the full array.

    Optional If-Match header for optimistic concurrency. If provided and
    doesn't match current version → 409. Frontend can re-fetch and retry.
    """
    _check_kind(kind)
    current_version = _kind_version(db, kind)
    if if_match is not None:
        # Strip surrounding quotes if present
        client_v = if_match.strip().strip('"')
        if client_v != str(current_version):
            raise HTTPException(
                status_code=409,
                detail=f"Concurrent write detected. Server version {current_version}, client expected {client_v}. Re-fetch and retry.",
                headers={"ETag": f'"{current_version}"'},
            )
    new_version = current_version + 1
    db.query(Document).filter(Document.kind == kind).delete(synchronize_session=False)
    for i, data in enumerate(payload.items):
        if not isinstance(data, dict):
            data = {"_value": data}
        db.add(Document(kind=kind, seq=i, data=data, version=new_version))
    db.commit()
    log_audit(
        db, claims, "replace_all", kind,
        summary=f"Replaced {len(payload.items)} item(s); v{current_version}→v{new_version}",
        ip=request.client.host if request.client else None,
    )
    broadcast("doc.updated", {
        "kind": kind,
        "version": new_version,
        "by_user_id": claims.get("user_id"),
        "by_name": claims.get("name"),
        "count": len(payload.items),
    })
    return {"kind": kind, "count": len(payload.items), "version": new_version}
