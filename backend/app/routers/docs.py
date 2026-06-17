"""Generic per-kind document storage.

Each prototype module persists its array via PUT /api/docs/{kind}, fetched via
GET /api/docs/{kind}. The whole-array replacement is intentional: Phase 1A
operates at low volume and prefers code simplicity over per-row diff sync.

ALLOWED_KINDS guards against arbitrary user-supplied kinds.
"""
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.database import get_db
from app.models import Document


ALLOWED_KINDS = {
    "loading_lists",
    "sdrs",
    "invoices",
    "credit_memos",
    "discrepancy_reports",
    "notifications",
}


router = APIRouter(
    prefix="/api/docs",
    tags=["docs"],
    dependencies=[Depends(require_auth)],
)


class DocsReplaceIn(BaseModel):
    items: list[Any]


def _check_kind(kind: str) -> None:
    if kind not in ALLOWED_KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown kind '{kind}'. Allowed: {sorted(ALLOWED_KINDS)}")


@router.get("/{kind}", response_model=list)
def list_docs(kind: str, db: Session = Depends(get_db)):
    _check_kind(kind)
    rows = (
        db.query(Document)
        .filter(Document.kind == kind)
        .order_by(Document.seq.asc())
        .all()
    )
    return [r.data for r in rows]


@router.put("/{kind}", response_model=dict)
def replace_docs(kind: str, payload: DocsReplaceIn, db: Session = Depends(get_db)):
    """Replace ALL documents for this kind. Frontend sends the full array."""
    _check_kind(kind)
    db.query(Document).filter(Document.kind == kind).delete(synchronize_session=False)
    for i, data in enumerate(payload.items):
        if not isinstance(data, dict):
            # Wrap non-dict (e.g. strings) so JSON column accepts it
            data = {"_value": data}
        db.add(Document(kind=kind, seq=i, data=data))
    db.commit()
    return {"kind": kind, "count": len(payload.items)}
