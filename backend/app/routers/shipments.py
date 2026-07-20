"""Shipments — dedicated typed backend for Loading Lists (Stage 1.4).

Replaces generic-docs persistence for loading lists with a typed table, adds
server-side truck totals (weight / pieces / pallets) computed from real
inventory, and auto-marks linked inventory by LL membership (pulled / loaded)
— the "status badges auto-update from Loading List membership" behavior.

Frontend round-trips its array shape unchanged (SDR / invoices / discrepancy /
barcoding read LOADING_LISTS by public_id, untouched). Cross-dock semantics and
the full Magaya-mimic shipment-creation flow are deferred to Stage 1.5.

TODO: validate the Loading List document layout against MSC's official LL
template once the client sends it (ops manual §4.4 used meanwhile).
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import LoadingList, InventoryItem, CustomsRecord, WarehouseReceipt
from app.schemas import ShipmentCreate, ShipmentUpdate, ShipmentOut, ShipmentBulk
from app.auth import require_auth, require_roles
from app.audit import log_audit
from app.events import broadcast
from app import pdf_docs

router = APIRouter(prefix="/api/shipments", tags=["shipments"])

_LOADED_STATUSES = {"dispatched", "loaded", "received"}


def _totals_for(db: Session, backend_ids: list) -> dict:
    """Server-side truck totals from real inventory rows."""
    if not backend_ids:
        return {"items": 0, "pieces": 0, "pallets": 0, "weight_lb": 0.0}
    rows = db.query(InventoryItem).filter(InventoryItem.id.in_(backend_ids)).all()
    return {
        "items": len(rows),
        "pieces": sum(int(r.pieces or 0) for r in rows),
        "pallets": sum(1 for r in rows if (r.package_unit or "").lower() == "pallet"),
        "weight_lb": round(sum(float(r.weight_lb or 0) for r in rows), 1),
    }


def _mark_inventory(db: Session, backend_ids: list, ll_status: str):
    """Reflect LL membership onto inventory status (pulled/loaded). Never clobbers
    non-stock states like 'bonded'."""
    if not backend_ids:
        return
    new_status = "loaded" if ll_status in _LOADED_STATUSES else "pulled"
    rows = db.query(InventoryItem).filter(InventoryItem.id.in_(backend_ids)).all()
    for r in rows:
        if (r.status or "in_stock") in ("in_stock", "pulled", "loaded"):
            r.status = new_status


def _apply(ll: LoadingList, payload: ShipmentCreate):
    for f in ("public_id", "vessel", "truck", "seal", "driver", "port", "departure",
              "cruise", "status", "po_number", "invoice_number", "vendor",
              "customs_docs", "notes", "item_ids", "cross_dock_item_ids",
              "truck_dimensions", "delivery_address", "created_by", "meta"):
        setattr(ll, f, getattr(payload, f))


@router.get("", response_model=list[ShipmentOut], dependencies=[Depends(require_auth)])
def list_shipments(db: Session = Depends(get_db)):
    return db.query(LoadingList).order_by(LoadingList.id.desc()).all()


@router.get("/{ll_id}", response_model=ShipmentOut, dependencies=[Depends(require_auth)])
def get_shipment(ll_id: int, db: Session = Depends(get_db)):
    ll = db.get(LoadingList, ll_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Loading list not found")
    return ll


@router.get("/{ll_id}/pdf", dependencies=[Depends(require_auth)])
def shipment_pdf(ll_id: int, doc: str = "ll", db: Session = Depends(get_db)):
    """Server-generated PDF of a dispatch document (doc = ll | cr | do)."""
    ll = db.get(LoadingList, ll_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Loading list not found")
    key = (doc or "ll").lower()
    gen = pdf_docs.GENERATORS.get(key)
    if not gen:
        raise HTTPException(status_code=400, detail="doc must be one of: ll, cr, do")
    ids = (ll.meta or {}).get("inv_backend_ids", []) or []
    items = db.query(InventoryItem).filter(InventoryItem.id.in_(ids)).all() if ids else []
    pdf_bytes = gen(ll, items)
    fname = f"{pdf_docs.FILENAMES[key]}-{ll.public_id}.pdf"
    return StreamingResponse(iter([pdf_bytes]), media_type="application/pdf",
                             headers={"Content-Disposition": f'inline; filename="{fname}"'})


@router.post("", response_model=ShipmentOut)
def create_shipment(payload: ShipmentCreate, request: Request, db: Session = Depends(get_db),
                    claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    if db.query(LoadingList).filter(LoadingList.public_id == payload.public_id).first():
        raise HTTPException(status_code=409, detail="public_id already exists")
    ll = LoadingList()
    _apply(ll, payload)
    ll.totals = _totals_for(db, payload.inventory_item_ids)
    _mark_inventory(db, payload.inventory_item_ids, payload.status)
    db.add(ll)
    db.commit()
    db.refresh(ll)
    log_audit(db, claims, "create", "shipment", entity_id=str(ll.id),
              summary=f"Loading List {ll.public_id} → {ll.vessel} ({ll.totals.get('items')} items)",
              ip=request.client.host if request.client else None)
    broadcast("shipments.changed", {"action": "create", "id": ll.id, "by_name": claims.get("name")})
    return ll


@router.patch("/{ll_id}", response_model=ShipmentOut)
def update_shipment(ll_id: int, payload: ShipmentUpdate, request: Request, db: Session = Depends(get_db),
                    claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    ll = db.get(LoadingList, ll_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Loading list not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(ll, k, v)
    if "status" in data:
        _mark_inventory(db, (ll.meta or {}).get("inv_backend_ids", []), ll.status)
    db.commit()
    db.refresh(ll)
    log_audit(db, claims, "update", "shipment", entity_id=str(ll.id),
              summary=f"Updated {ll.public_id}: {list(data.keys())}",
              ip=request.client.host if request.client else None)
    broadcast("shipments.changed", {"action": "update", "id": ll.id, "by_name": claims.get("name")})
    return ll


class AddReceiptIn(BaseModel):
    wr_id: int


@router.post("/{ll_id}/add-receipt", response_model=ShipmentOut)
def add_receipt(ll_id: int, payload: AddReceiptIn, request: Request, db: Session = Depends(get_db),
                claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    """Pull a Warehouse Receipt's received cargo straight onto this loading list (Stage 1.5,
    the part that doesn't need the warehouse walkthrough). Appends the WR's still-in-stock
    inventory to the load's backend ids, re-marks that inventory as pulled/loaded, and
    recomputes truck totals. Idempotent: items already on the load are skipped. (Cross-dock
    semantics remain deferred to the walkthrough.)"""
    ll = db.get(LoadingList, ll_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Loading list not found")
    wr = db.get(WarehouseReceipt, payload.wr_id)
    if not wr:
        raise HTTPException(status_code=404, detail="Warehouse receipt not found")

    existing = list((ll.meta or {}).get("inv_backend_ids", []) or [])
    candidate = [i for i in (wr.item_ids or []) if i not in existing]
    rows = db.query(InventoryItem).filter(InventoryItem.id.in_(candidate)).all() if candidate else []
    addable = [r.id for r in rows if (r.status or "in_stock") in ("in_stock", "pulled")]
    if not addable:
        raise HTTPException(status_code=400,
                            detail="Nothing to add — this receipt's items are already on the load, loaded elsewhere, or out of stock")

    new_meta = dict(ll.meta or {})
    new_meta["inv_backend_ids"] = existing + addable
    ll.meta = new_meta  # reassign so SQLAlchemy flags the JSON column dirty
    ll.totals = _totals_for(db, new_meta["inv_backend_ids"])
    _mark_inventory(db, addable, ll.status)
    db.commit()
    db.refresh(ll)
    log_audit(db, claims, "add-receipt", "shipment", entity_id=str(ll.id),
              summary=f"Pulled {wr.public_id} onto {ll.public_id} (+{len(addable)} item(s))",
              ip=request.client.host if request.client else None)
    broadcast("shipments.changed", {"action": "add-receipt", "id": ll.id, "wr": wr.public_id,
                                    "added": len(addable), "by_name": claims.get("name")})
    broadcast("inventory.changed", {"action": "pull", "count": len(addable), "by_name": claims.get("name")})
    return ll


@router.delete("/{ll_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shipment(ll_id: int, request: Request, db: Session = Depends(get_db),
                    claims: dict = Depends(require_roles("admin", "manager"))):
    ll = db.get(LoadingList, ll_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Loading list not found")
    pid = ll.public_id
    # Null any customs record's soft-link to this shipment so it doesn't dangle.
    db.query(CustomsRecord).filter(CustomsRecord.shipment_public_id == pid).update(
        {CustomsRecord.shipment_public_id: None}, synchronize_session=False)
    db.delete(ll)
    db.commit()
    log_audit(db, claims, "delete", "shipment", entity_id=str(ll_id), summary=f"Deleted {pid}",
              ip=request.client.host if request.client else None)
    broadcast("shipments.changed", {"action": "delete", "id": ll_id, "by_name": claims.get("name")})


@router.put("/bulk", response_model=list[ShipmentOut])
def replace_all(payload: ShipmentBulk, request: Request, db: Session = Depends(get_db),
                claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    """Replace-all (backs the frontend save path). Typed persistence + server totals;
    marks inventory by LL membership. Mirrors the old docs.replaceAll semantics."""
    db.query(LoadingList).delete()
    created = []
    for item in payload.items:
        ll = LoadingList()
        _apply(ll, item)
        ll.totals = _totals_for(db, item.inventory_item_ids)
        _mark_inventory(db, item.inventory_item_ids, item.status)
        db.add(ll)
        created.append(ll)
    db.commit()
    for ll in created:
        db.refresh(ll)
    log_audit(db, claims, "replace_all", "shipment",
              summary=f"Saved {len(created)} loading list(s)",
              ip=request.client.host if request.client else None)
    broadcast("shipments.changed", {"action": "replace_all", "count": len(created), "by_name": claims.get("name")})
    return created
