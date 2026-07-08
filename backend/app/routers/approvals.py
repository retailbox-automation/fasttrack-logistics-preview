"""Approval routing — submit reports / billing items for approval, then a
manager/admin approves, rejects, or marks needs-correction. Full audit + SSE.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth, require_roles
from app.audit import log_audit
from app.database import get_db
from app.events import broadcast
from app.models import Approval

router = APIRouter(prefix="/api/approvals", tags=["approvals"])

_DECISIONS = {"approved", "rejected", "needs_correction"}


class ApprovalCreate(BaseModel):
    kind: str
    ref: str
    title: Optional[str] = None
    payload: Optional[dict] = None


class ApprovalDecision(BaseModel):
    status: str
    reason: Optional[str] = None


class ApprovalOut(BaseModel):
    id: int
    kind: str
    ref: str
    title: Optional[str] = None
    status: str
    reason: Optional[str] = None
    submitted_by: Optional[str] = None
    submitted_at: datetime
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


@router.get("", response_model=list[ApprovalOut], dependencies=[Depends(require_auth)])
def list_approvals(kind: Optional[str] = None, status: Optional[str] = Query(None),
                   db: Session = Depends(get_db)):
    q = db.query(Approval)
    if kind:
        q = q.filter(Approval.kind == kind)
    if status:
        q = q.filter(Approval.status == status)
    return q.order_by(Approval.id.desc()).all()


@router.post("", response_model=ApprovalOut)
def submit_approval(payload: ApprovalCreate, request: Request, db: Session = Depends(get_db),
                    claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    a = Approval(kind=payload.kind.strip(), ref=payload.ref.strip(), title=payload.title,
                 payload=payload.payload, status="submitted",
                 submitted_by=claims.get("name") or claims.get("email"))
    db.add(a); db.commit(); db.refresh(a)
    log_audit(db, claims, "submit", "approval", entity_id=str(a.id),
              summary=f"Submitted {a.kind} '{a.ref}' for approval",
              ip=request.client.host if request.client else None)
    broadcast("approvals.changed", {"action": "submit", "kind": a.kind, "ref": a.ref, "by_name": claims.get("name")})
    return a


@router.patch("/{approval_id}", response_model=ApprovalOut)
def decide_approval(approval_id: int, payload: ApprovalDecision, request: Request,
                    db: Session = Depends(get_db),
                    claims: dict = Depends(require_roles("admin", "manager"))):
    a = db.get(Approval, approval_id)
    if not a:
        raise HTTPException(status_code=404, detail="Approval not found")
    if payload.status not in _DECISIONS:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_DECISIONS)}")
    a.status = payload.status
    a.reason = payload.reason
    a.decided_by = claims.get("name") or claims.get("email")
    a.decided_at = datetime.utcnow()
    db.commit(); db.refresh(a)
    log_audit(db, claims, "decide", "approval", entity_id=str(a.id),
              summary=f"{payload.status} {a.kind} '{a.ref}'" + (f" — {payload.reason}" if payload.reason else ""),
              ip=request.client.host if request.client else None)
    broadcast("approvals.changed", {"action": payload.status, "kind": a.kind, "ref": a.ref, "by_name": claims.get("name")})
    return a
