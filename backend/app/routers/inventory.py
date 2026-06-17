"""Inventory CRUD + move endpoints."""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
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
from app.auth import require_auth

router = APIRouter(
    prefix="/api/inventory",
    tags=["inventory"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=list[InventoryItemOut])
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
def create_item(payload: InventoryItemCreate, db: Session = Depends(get_db)):
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
    return item


@router.get("/{item_id}", response_model=InventoryItemOut)
def get_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.patch("/{item_id}", response_model=InventoryItemOut)
def update_item(item_id: int, payload: InventoryItemUpdate, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    data = payload.model_dump(exclude_unset=True)
    if data.get("package_unit") == "Pallet" and data.get("weight_lb") and data["weight_lb"] > 2000:
        raise HTTPException(status_code=400, detail="Pallet weight exceeds 2,000 lb")
    for k, v in data.items():
        setattr(item, k, v)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()


@router.post("/{item_id}/move", response_model=InventoryItemOut)
def move_item(item_id: int, payload: InventoryItemMove, db: Session = Depends(get_db)):
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
        "by": payload.by or "system",
        "reason": payload.reason or "",
    })
    item.movements = movements
    db.commit()
    db.refresh(item)
    return item


@router.post("/bulk-import", response_model=dict)
def bulk_import(items: list[InventoryItemCreate], db: Session = Depends(get_db)):
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
    return {"created": created, "errors": errors, "total": len(items)}
