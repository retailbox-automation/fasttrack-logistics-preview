"""Pick & Pack — controlled picking with per-line qty validation, short-pick
alerts, full audit, and real-time inventory decrement on completion.

Photos attach via the attachments API using shipment_public_id = "PICK-<ref>".
"""
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth, require_roles
from app.audit import log_audit
from app.database import get_db
from app.events import broadcast
from app.models import PickTask, InventoryItem

router = APIRouter(prefix="/api/picks", tags=["picks"])


class PickLineIn(BaseModel):
    inventory_item_id: int
    required: int


class PickCreate(BaseModel):
    shipment_public_id: Optional[str] = None
    lines: List[PickLineIn]
    notes: Optional[str] = None


class PickLineUpdate(BaseModel):
    line_index: int
    picked: int
    scanned: Optional[bool] = None


class PickOut(BaseModel):
    id: int
    ref: str
    shipment_public_id: Optional[str] = None
    status: str
    lines: list
    notes: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    completed_by: Optional[str] = None
    model_config = {"from_attributes": True}


def _next_ref(db: Session) -> str:
    return f"PICK-2026-{db.query(PickTask).count() + 1:04d}"


@router.get("", response_model=list[PickOut], dependencies=[Depends(require_auth)])
def list_picks(db: Session = Depends(get_db)):
    return db.query(PickTask).order_by(PickTask.id.desc()).all()


@router.get("/{pick_id}", response_model=PickOut, dependencies=[Depends(require_auth)])
def get_pick(pick_id: int, db: Session = Depends(get_db)):
    p = db.get(PickTask, pick_id)
    if not p:
        raise HTTPException(status_code=404, detail="Pick task not found")
    return p


@router.post("", response_model=PickOut)
def create_pick(payload: PickCreate, request: Request, db: Session = Depends(get_db),
                claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    if not payload.lines:
        raise HTTPException(status_code=400, detail="At least one line required")
    lines = []
    for ln in payload.lines:
        it = db.get(InventoryItem, ln.inventory_item_id)
        if not it:
            raise HTTPException(status_code=400, detail=f"Inventory item {ln.inventory_item_id} not found")
        if ln.required <= 0:
            raise HTTPException(status_code=400, detail="required must be > 0")
        lines.append({
            "inventory_item_id": it.id,
            "wr": it.warehouse_receipt,
            "part": it.part_number,
            "description": it.description,
            "location": it.location_code,
            "package_unit": it.package_unit,
            "available": it.pieces,
            "required": int(ln.required),
            "picked": 0,
            "scanned": False,
            "status": "pending",
        })
    p = PickTask(ref=_next_ref(db), shipment_public_id=payload.shipment_public_id,
                 status="open", lines=lines, notes=payload.notes,
                 created_by=claims.get("name") or claims.get("email"))
    db.add(p); db.commit(); db.refresh(p)
    log_audit(db, claims, "create", "pick_task", entity_id=str(p.id),
              summary=f"Pick {p.ref} · {len(lines)} line(s)",
              ip=request.client.host if request.client else None)
    broadcast("picks.changed", {"action": "create", "ref": p.ref, "by_name": claims.get("name")})
    return p


@router.patch("/{pick_id}", response_model=PickOut)
def update_line(pick_id: int, payload: PickLineUpdate, db: Session = Depends(get_db),
                claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    p = db.get(PickTask, pick_id)
    if not p:
        raise HTTPException(status_code=404, detail="Pick task not found")
    if p.status == "completed":
        raise HTTPException(status_code=400, detail="Pick already completed")
    lines = list(p.lines or [])
    if payload.line_index < 0 or payload.line_index >= len(lines):
        raise HTTPException(status_code=400, detail="Bad line_index")
    ln = dict(lines[payload.line_index])
    picked = max(0, int(payload.picked))
    req = int(ln.get("required", 0))
    ln["picked"] = picked
    if payload.scanned is not None:
        ln["scanned"] = bool(payload.scanned)
    ln["status"] = "picked" if (req > 0 and picked >= req) else ("pending" if picked == 0 else "short")
    lines[payload.line_index] = ln
    p.lines = lines
    if p.status == "open":
        p.status = "in_progress"
    db.commit(); db.refresh(p)
    broadcast("picks.changed", {"action": "update", "ref": p.ref})
    return p


@router.post("/{pick_id}/complete", response_model=dict)
def complete_pick(pick_id: int, request: Request, db: Session = Depends(get_db),
                  claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    p = db.get(PickTask, pick_id)
    if not p:
        raise HTTPException(status_code=404, detail="Pick task not found")
    if p.status == "completed":
        raise HTTPException(status_code=400, detail="Already completed")
    lines = list(p.lines or [])
    shortfalls = [
        {"wr": ln.get("wr"), "part": ln.get("part"), "required": int(ln.get("required", 0)),
         "picked": int(ln.get("picked", 0)), "missing": int(ln.get("required", 0)) - int(ln.get("picked", 0))}
        for ln in lines if int(ln.get("picked", 0)) < int(ln.get("required", 0))
    ]
    if shortfalls:
        p.status = "short"
        db.commit()
        broadcast("picks.changed", {"action": "short", "ref": p.ref})
        return {"ok": False, "status": "short", "shortfalls": shortfalls,
                "message": f"{len(shortfalls)} line(s) short — cannot complete until picked."}
    now = datetime.utcnow()
    for ln in lines:
        it = db.get(InventoryItem, ln.get("inventory_item_id"))
        if not it:
            continue
        picked = int(ln.get("picked", 0))
        it.pieces = max(0, (it.pieces or 0) - picked)
        movs = list(it.movements or [])
        movs.append({"ts": now.isoformat(), "action": "pick", "qty": picked,
                     "ref": p.ref, "by": claims.get("name") or claims.get("email")})
        it.movements = movs
        ln["status"] = "picked"
    p.status = "completed"
    p.completed_at = now
    p.completed_by = claims.get("name") or claims.get("email")
    p.lines = lines
    db.commit()
    log_audit(db, claims, "complete", "pick_task", entity_id=str(p.id),
              summary=f"Completed pick {p.ref} · {len(lines)} line(s)",
              ip=request.client.host if request.client else None)
    broadcast("picks.changed", {"action": "complete", "ref": p.ref, "by_name": claims.get("name")})
    return {"ok": True, "status": "completed", "ref": p.ref}
