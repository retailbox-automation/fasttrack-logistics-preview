"""Inventory CRUD + move endpoints."""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.database import get_db
from app.models import InventoryItem
from app.schemas import (
    InventoryItemCreate,
    InventoryItemUpdate,
    InventoryItemMove,
    InventoryItemOut,
)
from app.auth import require_auth, require_roles
from app.audit import log_audit

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


@router.get("", response_model=list[InventoryItemOut], dependencies=[Depends(require_auth)])
def list_items(
    q: str | None = None,
    department: str | None = None,
    status_filter: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(InventoryItem)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                InventoryItem.warehouse_receipt.ilike(like),
                InventoryItem.part_number.ilike(like),
                InventoryItem.description.ilike(like),
                InventoryItem.location_code.ilike(like),
            )
        )
    if department:
        query = query.filter(InventoryItem.department == department)
    if status_filter:
        query = query.filter(InventoryItem.status == status_filter)
    return query.order_by(InventoryItem.id.desc()).all()


@router.post("", response_model=InventoryItemOut, status_code=status.HTTP_201_CREATED)
def create_item(payload: InventoryItemCreate, request: Request, db: Session = Depends(get_db), claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    # Pallet 2,000 lb rule per ops manual §8.2
    if payload.package_unit == "Pallet" and payload.weight_lb and payload.weight_lb > 2000:
        raise HTTPException(
            status_code=400,
            detail="Pallet weight exceeds 2,000 lb — FT operational rule (ops manual §8.2)",
        )
    item = InventoryItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    log_audit(db, claims, "create", "inventory_item", entity_id=str(item.id),
              summary=f"Created {item.warehouse_receipt} · {item.part_number}",
              payload=payload.model_dump(),
              ip=request.client.host if request.client else None)
    return item


@router.get("/{item_id}", response_model=InventoryItemOut, dependencies=[Depends(require_auth)])
def get_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.patch("/{item_id}", response_model=InventoryItemOut)
def update_item(item_id: int, payload: InventoryItemUpdate, request: Request, db: Session = Depends(get_db), claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    data = payload.model_dump(exclude_unset=True)
    if data.get("package_unit") == "Pallet" and data.get("weight_lb") and data["weight_lb"] > 2000:
        raise HTTPException(status_code=400, detail="Pallet weight exceeds 2,000 lb")
    changed = {k: v for k, v in data.items() if getattr(item, k, None) != v}
    for k, v in data.items():
        setattr(item, k, v)
    db.commit()
    db.refresh(item)
    if changed:
        log_audit(db, claims, "update", "inventory_item", entity_id=str(item.id),
                  summary=f"Updated {item.warehouse_receipt} · {item.part_number}: {list(changed.keys())}",
                  payload=changed,
                  ip=request.client.host if request.client else None)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, request: Request, db: Session = Depends(get_db), claims: dict = Depends(require_roles("admin", "manager"))):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    summary = f"Deleted {item.warehouse_receipt} · {item.part_number}"
    db.delete(item)
    db.commit()
    log_audit(db, claims, "delete", "inventory_item", entity_id=str(item_id),
              summary=summary,
              ip=request.client.host if request.client else None)


@router.post("/{item_id}/move", response_model=InventoryItemOut)
def move_item(item_id: int, payload: InventoryItemMove, request: Request, db: Session = Depends(get_db), claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    """Relocate item with audit log (per Andrés 6/11)."""
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    old_loc = item.location_code
    if payload.new_location == old_loc:
        raise HTTPException(status_code=400, detail="New location is same as current")
    item.location_code = payload.new_location
    movements = list(item.movements or [])
    movements.insert(0, {
        "time": datetime.utcnow().isoformat(timespec="seconds"),
        "action": "moved",
        "from": old_loc,
        "to": payload.new_location,
        "by": claims.get("name") or payload.by or "system",
        "reason": payload.reason or "",
    })
    item.movements = movements
    db.commit()
    db.refresh(item)
    log_audit(db, claims, "move", "inventory_item", entity_id=str(item.id),
              summary=f"Moved {item.warehouse_receipt} · {item.part_number} from {old_loc} → {payload.new_location}",
              payload={"from": old_loc, "to": payload.new_location, "reason": payload.reason},
              ip=request.client.host if request.client else None)
    return item


@router.post("/bulk-import", response_model=dict)
def bulk_import(items: list[InventoryItemCreate], request: Request, db: Session = Depends(get_db), claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    """Bulk import inventory items (for Andrea's Magaya export CSV workflow)."""
    created = 0
    errors = []
    for idx, payload in enumerate(items):
        try:
            if payload.package_unit == "Pallet" and payload.weight_lb and payload.weight_lb > 2000:
                errors.append({"index": idx, "error": "Pallet > 2000 lb"})
                continue
            db.add(InventoryItem(**payload.model_dump()))
            created += 1
        except Exception as e:
            errors.append({"index": idx, "error": str(e)})
    db.commit()
    log_audit(db, claims, "bulk_import", "inventory_item",
              summary=f"Bulk-imported {created} item(s), {len(errors)} error(s)",
              ip=request.client.host if request.client else None)
    return {"created": created, "errors": errors, "total": len(items)}
