"""Warehouse Receipts — the intake side of the shipment lifecycle (Stage 1.5).

Mirrors Magaya's Warehouse Receipt: receiving goods creates a WR (header +
lines), and each line generates a real InventoryItem tagged with the WR number,
so received cargo flows straight into the inventory control center. This closes
the loop with Shipments (LL/CR/DO), which is the dispatch/outbound side.

On create: validate lines, enforce the 2,000 lb pallet rule per line, generate
inventory rows, compute totals, audit + broadcast (inventory + WR). Deleting a
WR removes the receipt document; the generated inventory persists unless
`remove_inventory=true` (then only still-in_stock rows it created are removed).
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import WarehouseReceipt, InventoryItem
from app.schemas import WarehouseReceiptCreate, WarehouseReceiptOut
from app.auth import require_auth, require_roles
from app.audit import log_audit
from app.events import broadcast
from app import pdf_docs

router = APIRouter(prefix="/api/warehouse-receipts", tags=["warehouse-receipts"])


def _next_public_id(db: Session) -> str:
    year = date.today().year
    n = db.query(WarehouseReceipt).count() + 1
    # Skip collisions in case of manual ids / gaps.
    while db.query(WarehouseReceipt).filter(WarehouseReceipt.public_id == f"WR-{year}-{n:04d}").first():
        n += 1
    return f"WR-{year}-{n:04d}"


@router.get("", response_model=list[WarehouseReceiptOut], dependencies=[Depends(require_auth)])
def list_receipts(db: Session = Depends(get_db)):
    return db.query(WarehouseReceipt).order_by(WarehouseReceipt.id.desc()).all()


@router.get("/{wr_id}", response_model=WarehouseReceiptOut, dependencies=[Depends(require_auth)])
def get_receipt(wr_id: int, db: Session = Depends(get_db)):
    wr = db.get(WarehouseReceipt, wr_id)
    if not wr:
        raise HTTPException(status_code=404, detail="Warehouse receipt not found")
    return wr


@router.get("/{wr_id}/pdf", dependencies=[Depends(require_auth)])
def receipt_pdf(wr_id: int, db: Session = Depends(get_db)):
    wr = db.get(WarehouseReceipt, wr_id)
    if not wr:
        raise HTTPException(status_code=404, detail="Warehouse receipt not found")
    items = db.query(InventoryItem).filter(InventoryItem.id.in_(wr.item_ids or [])).all() if wr.item_ids else []
    pdf_bytes = pdf_docs.warehouse_receipt_pdf(wr, items)
    return StreamingResponse(iter([pdf_bytes]), media_type="application/pdf",
                             headers={"Content-Disposition": f'inline; filename="warehouse-receipt-{wr.public_id}.pdf"'})


@router.post("", response_model=WarehouseReceiptOut)
def create_receipt(payload: WarehouseReceiptCreate, request: Request, db: Session = Depends(get_db),
                   claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    if not payload.lines:
        raise HTTPException(status_code=400, detail="At least one received line is required")

    public_id = (payload.public_id or "").strip() or _next_public_id(db)
    if db.query(WarehouseReceipt).filter(WarehouseReceipt.public_id == public_id).first():
        raise HTTPException(status_code=409, detail=f"Warehouse receipt {public_id} already exists")

    received_by = payload.received_by or claims.get("name")
    received_date = payload.received_date or date.today()

    # Validate before touching the DB — a bad line fails the whole receipt.
    for i, ln in enumerate(payload.lines):
        if (ln.package_unit or "").lower() == "pallet" and ln.weight_lb and ln.weight_lb > 2000:
            raise HTTPException(status_code=400,
                                detail=f"Line {i + 1}: pallet weight exceeds 2,000 lb (ops manual §8.2)")
        if ln.pieces < 0 or ln.quantity < 0:
            raise HTTPException(status_code=400, detail=f"Line {i + 1}: pieces/quantity cannot be negative")

    item_ids, line_snapshot = [], []
    for ln in payload.lines:
        item = InventoryItem(
            warehouse_receipt=public_id,
            part_number=ln.part_number,
            description=ln.description,
            department=ln.department or payload.department,
            vessel=payload.vessel,
            package_unit=ln.package_unit or "Box",
            pieces=int(ln.pieces or 0),
            quantity=int(ln.quantity or 0),
            location_code=ln.location_code or "RECEIVING",
            po_number=payload.po_number,
            weight_lb=ln.weight_lb,
            received_date=received_date,
            received_by=received_by,
            status="in_stock",
        )
        db.add(item)
        db.flush()  # assign item.id
        item_ids.append(item.id)
        line_snapshot.append({
            "inventory_item_id": item.id,
            "part_number": ln.part_number,
            "description": ln.description,
            "department": item.department,
            "package_unit": item.package_unit,
            "pieces": item.pieces,
            "quantity": item.quantity,
            "weight_lb": ln.weight_lb,
            "location_code": item.location_code,
        })

    totals = {
        "items": len(line_snapshot),
        "pieces": sum(int(l["pieces"] or 0) for l in line_snapshot),
        "pallets": sum(1 for l in line_snapshot if (l["package_unit"] or "").lower() == "pallet"),
        "weight_lb": round(sum(float(l["weight_lb"] or 0) for l in line_snapshot), 1),
    }

    wr = WarehouseReceipt(
        public_id=public_id,
        received_date=received_date,
        received_by=received_by,
        vessel=payload.vessel,
        department=payload.department,
        vendor=payload.vendor,
        po_number=payload.po_number,
        carrier=payload.carrier,
        tracking=payload.tracking,
        status="received",
        notes=payload.notes,
        lines=line_snapshot,
        item_ids=item_ids,
        totals=totals,
        created_by=claims.get("name"),
    )
    db.add(wr)
    db.commit()
    db.refresh(wr)

    ip = request.client.host if request.client else None
    log_audit(db, claims, "create", "warehouse_receipt", entity_id=str(wr.id),
              summary=f"Received {wr.public_id} → {totals['items']} item(s), {totals['pieces']} pcs into inventory",
              payload={"public_id": wr.public_id, "item_ids": item_ids, "totals": totals}, ip=ip)
    broadcast("warehouse_receipts.changed", {"action": "create", "id": wr.id, "by_name": claims.get("name")})
    broadcast("inventory.changed", {"action": "receive", "count": len(item_ids), "by_name": claims.get("name")})
    return wr


@router.delete("/{wr_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_receipt(wr_id: int, request: Request, remove_inventory: bool = False,
                   db: Session = Depends(get_db),
                   claims: dict = Depends(require_roles("admin", "manager"))):
    wr = db.get(WarehouseReceipt, wr_id)
    if not wr:
        raise HTTPException(status_code=404, detail="Warehouse receipt not found")
    pid = wr.public_id
    removed = 0
    if remove_inventory and wr.item_ids:
        rows = db.query(InventoryItem).filter(InventoryItem.id.in_(wr.item_ids)).all()
        for r in rows:
            if (r.status or "in_stock") == "in_stock":  # never delete cargo already pulled/loaded
                db.delete(r)
                removed += 1
    db.delete(wr)
    db.commit()
    log_audit(db, claims, "delete", "warehouse_receipt", entity_id=str(wr_id),
              summary=f"Deleted {pid}" + (f" + {removed} in-stock inventory row(s)" if removed else ""),
              ip=request.client.host if request.client else None)
    broadcast("warehouse_receipts.changed", {"action": "delete", "id": wr_id, "by_name": claims.get("name")})
    if removed:
        broadcast("inventory.changed", {"action": "delete", "count": removed, "by_name": claims.get("name")})
