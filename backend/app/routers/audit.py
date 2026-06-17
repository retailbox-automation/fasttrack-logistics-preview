"""Read-only audit log viewer."""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.database import get_db
from app.models import AuditLog


router = APIRouter(prefix="/api/audit", tags=["audit"], dependencies=[Depends(require_auth)])


class AuditOut(BaseModel):
    id: int
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    user_role: Optional[str] = None
    action: str
    entity_kind: str
    entity_id: Optional[str] = None
    summary: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[AuditOut])
def list_audit(
    limit: int = Query(100, le=500),
    entity_kind: Optional[str] = None,
    entity_id: Optional[str] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    q = db.query(AuditLog).order_by(AuditLog.id.desc())
    if entity_kind:
        q = q.filter(AuditLog.entity_kind == entity_kind)
    if entity_id:
        q = q.filter(AuditLog.entity_id == entity_id)
    if user_id is not None:
        q = q.filter(AuditLog.user_id == user_id)
    return q.limit(limit).all()
