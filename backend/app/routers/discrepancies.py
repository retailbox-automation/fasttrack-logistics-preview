"""Discrepancy reports — typed (Stage 4.1).

Promotes discrepancy reports from the generic /api/docs/discrepancy_reports blob to a
typed resource: /api/discrepancies CRUD (variance line items in `lines`). Feeds the
weekly report + MSC reconciliation. Same pattern as customs/billing. Manager/admin write.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DiscrepancyReport
from app.schemas import (DiscrepancyCreate, DiscrepancyUpdate, DiscrepancyOut,
                         DISCREPANCY_STATES, DISCREPANCY_SEVERITIES)
from app.auth import require_auth, require_roles
from app.audit import log_audit
from app.events import broadcast

router = APIRouter(prefix="/api/discrepancies", tags=["discrepancies"])


def _validate(status_val=None, severity=None) -> None:
    if status_val is not None and status_val not in DISCREPANCY_STATES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(DISCREPANCY_STATES)}")
    if severity is not None and severity not in DISCREPANCY_SEVERITIES:
        raise HTTPException(status_code=400, detail=f"severity must be one of {sorted(DISCREPANCY_SEVERITIES)}")


def _next_public_id(db: Session) -> str:
    year = date.today().year
    n = db.query(DiscrepancyReport).count() + 1
    while db.query(DiscrepancyReport).filter(DiscrepancyReport.public_id == f"DR-{year}-{n:04d}").first():
        n += 1
    return f"DR-{year}-{n:04d}"


@router.get("", response_model=list[DiscrepancyOut], dependencies=[Depends(require_auth)])
def list_discrepancies(status: str | None = None, severity: str | None = None, db: Session = Depends(get_db)):
    q = db.query(DiscrepancyReport)
    if status:
        q = q.filter(DiscrepancyReport.status == status)
    if severity:
        q = q.filter(DiscrepancyReport.severity == severity)
    return q.order_by(DiscrepancyReport.id.desc()).all()


@router.get("/{dr_id}", response_model=DiscrepancyOut, dependencies=[Depends(require_auth)])
def get_discrepancy(dr_id: int, db: Session = Depends(get_db)):
    rec = db.get(DiscrepancyReport, dr_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Discrepancy report not found")
    return rec


@router.post("", response_model=DiscrepancyOut)
def create_discrepancy(payload: DiscrepancyCreate, request: Request, db: Session = Depends(get_db),
                       claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    _validate(payload.status, payload.severity)
    public_id = (payload.public_id or "").strip() or _next_public_id(db)
    if db.query(DiscrepancyReport).filter(DiscrepancyReport.public_id == public_id).first():
        raise HTTPException(status_code=409, detail=f"Discrepancy {public_id} already exists")
    rec = DiscrepancyReport(
        public_id=public_id,
        loading_list_public_id=payload.loading_list_public_id,
        reported_at=payload.reported_at or date.today(),
        reported_by=payload.reported_by,
        ft_respondent=payload.ft_respondent or claims.get("name") or "",
        status=payload.status or "open",
        severity=payload.severity or "minor",
        notes=payload.notes,
        resolution_notes=payload.resolution_notes,
        lines=payload.lines or [],
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    log_audit(db, claims, "create", "discrepancy_report", entity_id=str(rec.id),
              summary=f"Discrepancy {rec.public_id} ({rec.severity}) · {rec.status}",
              ip=request.client.host if request.client else None)
    broadcast("discrepancy_reports.changed", {"action": "create", "id": rec.id,
                                              "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
    return rec


@router.patch("/{dr_id}", response_model=DiscrepancyOut)
def update_discrepancy(dr_id: int, payload: DiscrepancyUpdate, request: Request, db: Session = Depends(get_db),
                       claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    rec = db.get(DiscrepancyReport, dr_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Discrepancy report not found")
    data = payload.model_dump(exclude_unset=True)
    _validate(data.get("status"), data.get("severity"))
    if "lines" in data and data["lines"] is not None:
        rec.lines = data.pop("lines")
    for k, v in data.items():
        setattr(rec, k, v)
    db.commit()
    db.refresh(rec)
    log_audit(db, claims, "update", "discrepancy_report", entity_id=str(rec.id),
              summary=f"Updated discrepancy {rec.public_id}: {list(data.keys())}",
              ip=request.client.host if request.client else None)
    broadcast("discrepancy_reports.changed", {"action": "update", "id": rec.id,
                                              "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
    return rec


@router.delete("/{dr_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_discrepancy(dr_id: int, request: Request, db: Session = Depends(get_db),
                       claims: dict = Depends(require_roles("admin", "manager"))):
    rec = db.get(DiscrepancyReport, dr_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Discrepancy report not found")
    pid = rec.public_id
    db.delete(rec)
    db.commit()
    log_audit(db, claims, "delete", "discrepancy_report", entity_id=str(dr_id), summary=f"Deleted discrepancy {pid}",
              ip=request.client.host if request.client else None)
    broadcast("discrepancy_reports.changed", {"action": "delete", "id": dr_id,
                                              "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
