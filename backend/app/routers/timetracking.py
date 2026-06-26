"""Employee time tracking — clock in/out + manager visibility (per Andrés 6/19).

Roles: any signed-in user clocks themselves and sees their own entries.
admin (Andrés) + manager (Gabriela/Luis) see everyone, can correct entries,
view the report, and export CSV.
"""
import csv
import io
from datetime import datetime, date, time as dtime

from fastapi import APIRouter, Depends, HTTPException, Request, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.audit import log_audit
from app.auth import require_auth, require_roles
from app.database import get_db
from app.events import broadcast
from app.models import TimeEntry
from app.schemas import TimeEntryOut, ClockInOut, TimeEntryUpdate

router = APIRouter(prefix="/api/time", tags=["time"])

MANAGER_ROLES = ("admin", "manager")


def _is_manager(claims: dict) -> bool:
    return claims.get("role") in MANAGER_ROLES


def _open_entry(db: Session, user_id: int):
    return (db.query(TimeEntry)
            .filter(TimeEntry.user_id == user_id, TimeEntry.clock_out_at.is_(None))
            .order_by(TimeEntry.clock_in_at.desc()).first())


@router.get("/status", response_model=dict, dependencies=[Depends(require_auth)])
def my_status(claims: dict = Depends(require_auth), db: Session = Depends(get_db)):
    uid = claims.get("user_id")
    if not uid:
        return {"clocked_in": False, "entry": None}
    e = _open_entry(db, uid)
    return {"clocked_in": e is not None, "entry": TimeEntryOut.model_validate(e).model_dump(mode="json") if e else None}


@router.post("/clock-in", response_model=TimeEntryOut)
def clock_in(payload: ClockInOut, request: Request, claims: dict = Depends(require_auth), db: Session = Depends(get_db)):
    uid = claims.get("user_id")
    if not uid:
        raise HTTPException(status_code=403, detail="Shared/legacy login can't clock in — sign in with your own account")
    if _open_entry(db, uid):
        raise HTTPException(status_code=409, detail="Already clocked in — clock out first")
    e = TimeEntry(user_id=uid, user_name=claims.get("name") or "", clock_in_at=datetime.utcnow(), note=payload.note)
    db.add(e)
    db.commit()
    db.refresh(e)
    log_audit(db, claims, "clock_in", "time_entry", entity_id=str(e.id),
              summary=f"{e.user_name} clocked in", ip=request.client.host if request.client else None)
    broadcast("time.changed", {"action": "clock_in", "user_id": uid, "by_name": e.user_name})
    return e


@router.post("/clock-out", response_model=TimeEntryOut)
def clock_out(payload: ClockInOut, request: Request, claims: dict = Depends(require_auth), db: Session = Depends(get_db)):
    uid = claims.get("user_id")
    if not uid:
        raise HTTPException(status_code=403, detail="Shared/legacy login can't clock out")
    e = _open_entry(db, uid)
    if not e:
        raise HTTPException(status_code=409, detail="Not clocked in")
    e.clock_out_at = datetime.utcnow()
    if payload.note:
        e.note = (e.note + " | " if e.note else "") + payload.note
    db.commit()
    db.refresh(e)
    log_audit(db, claims, "clock_out", "time_entry", entity_id=str(e.id),
              summary=f"{e.user_name} clocked out ({e.duration_minutes} min)",
              ip=request.client.host if request.client else None)
    broadcast("time.changed", {"action": "clock_out", "user_id": uid, "by_name": e.user_name})
    return e


@router.get("/active", response_model=list[TimeEntryOut], dependencies=[Depends(require_roles(*MANAGER_ROLES))])
def active_now(db: Session = Depends(get_db)):
    """Who is currently clocked in (manager view)."""
    return (db.query(TimeEntry).filter(TimeEntry.clock_out_at.is_(None))
            .order_by(TimeEntry.clock_in_at.asc()).all())


@router.get("/entries", response_model=list[TimeEntryOut])
def list_entries(
    user_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(200, le=1000),
    claims: dict = Depends(require_auth),
    db: Session = Depends(get_db),
):
    q = db.query(TimeEntry)
    if _is_manager(claims):
        if user_id:
            q = q.filter(TimeEntry.user_id == user_id)
    else:
        # non-managers: own entries only
        if not claims.get("user_id"):
            return []
        q = q.filter(TimeEntry.user_id == claims["user_id"])
    if date_from:
        q = q.filter(TimeEntry.clock_in_at >= datetime.combine(date_from, dtime.min))
    if date_to:
        q = q.filter(TimeEntry.clock_in_at <= datetime.combine(date_to, dtime.max))
    return q.order_by(TimeEntry.clock_in_at.desc()).limit(limit).all()


@router.get("/report", response_model=list[dict], dependencies=[Depends(require_roles(*MANAGER_ROLES))])
def report(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    """Per-employee totals over a period (closed shifts only)."""
    q = db.query(
        TimeEntry.user_id,
        TimeEntry.user_name,
        func.count(TimeEntry.id),
        func.sum(func.extract("epoch", TimeEntry.clock_out_at - TimeEntry.clock_in_at)),
    ).filter(TimeEntry.clock_out_at.isnot(None))
    if date_from:
        q = q.filter(TimeEntry.clock_in_at >= datetime.combine(date_from, dtime.min))
    if date_to:
        q = q.filter(TimeEntry.clock_in_at <= datetime.combine(date_to, dtime.max))
    q = q.group_by(TimeEntry.user_id, TimeEntry.user_name).order_by(TimeEntry.user_name)
    out = []
    for uid, name, cnt, secs in q.all():
        mins = int((secs or 0) // 60)
        out.append({"user_id": uid, "user_name": name, "shifts": cnt,
                    "total_minutes": mins, "total_hours": round(mins / 60, 2)})
    return out


@router.get("/export.csv", dependencies=[Depends(require_roles(*MANAGER_ROLES))])
def export_csv(date_from: date | None = None, date_to: date | None = None, db: Session = Depends(get_db)):
    q = db.query(TimeEntry)
    if date_from:
        q = q.filter(TimeEntry.clock_in_at >= datetime.combine(date_from, dtime.min))
    if date_to:
        q = q.filter(TimeEntry.clock_in_at <= datetime.combine(date_to, dtime.max))
    rows = q.order_by(TimeEntry.user_name, TimeEntry.clock_in_at).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Employee", "Clock in (UTC)", "Clock out (UTC)", "Duration (min)", "Note", "Edited by"])
    for e in rows:
        w.writerow([e.user_name, e.clock_in_at.isoformat(timespec="minutes"),
                    e.clock_out_at.isoformat(timespec="minutes") if e.clock_out_at else "",
                    e.duration_minutes, e.note or "", e.edited_by or ""])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=time_entries.csv"})


@router.patch("/entries/{entry_id}", response_model=TimeEntryOut)
def correct_entry(entry_id: int, payload: TimeEntryUpdate, request: Request,
                  claims: dict = Depends(require_roles(*MANAGER_ROLES)), db: Session = Depends(get_db)):
    """Manager correction of a time entry (audited)."""
    e = db.get(TimeEntry, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="Entry not found")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No changes")
    if "clock_in_at" in data and data["clock_in_at"]:
        e.clock_in_at = data["clock_in_at"].replace(tzinfo=None)
    if "clock_out_at" in data:
        e.clock_out_at = data["clock_out_at"].replace(tzinfo=None) if data["clock_out_at"] else None
    if "note" in data:
        e.note = data["note"]
    if e.clock_out_at and e.clock_out_at < e.clock_in_at:
        raise HTTPException(status_code=400, detail="Clock-out is before clock-in")
    e.edited_by = claims.get("name")
    e.edited_at = datetime.utcnow()
    db.commit()
    db.refresh(e)
    log_audit(db, claims, "correct", "time_entry", entity_id=str(e.id),
              summary=f"Corrected {e.user_name}'s entry", payload={k: str(v) for k, v in data.items()},
              ip=request.client.host if request.client else None)
    broadcast("time.changed", {"action": "correct", "user_id": e.user_id, "by_name": claims.get("name")})
    return e
