"""Warehouse barcode scanning + loading-list reconciliation (Stage 3.4, FT-internal side).

Real scan flow for receiving / picking / loading. Ship-side 'gangway' reconciliation needs
MSC IT access and is intentionally NOT built here (returns a clear 400). A scan resolves a
code to an inventory item (explicit barcode → part number → warehouse receipt), records a
ScanEvent, and — for picking/loading against a loading list — reports live reconciliation:
matched / missing / unexpected vs the list's expected items (ll.meta.inv_backend_ids).

POST /api/barcoding/scan            {code, mode, loading_list_id?}
GET  /api/barcoding/reconcile/{ll}  ?mode=loading
GET  /api/barcoding/session/{ll}    recent scans
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models import InventoryItem, LoadingList, ScanEvent
from app.auth import require_auth, require_roles
from app.events import broadcast

router = APIRouter(prefix="/api/barcoding", tags=["barcoding"])

SCAN_MODES = {"receiving", "picking", "loading"}
LL_MODES = {"picking", "loading"}


class ScanIn(BaseModel):
    code: str
    mode: str
    loading_list_id: Optional[int] = None


def _item_out(i: InventoryItem) -> dict:
    return {"id": i.id, "part_number": i.part_number, "warehouse_receipt": i.warehouse_receipt,
            "description": i.description, "vessel": i.vessel, "department": i.department,
            "location_code": i.location_code, "status": i.status}


def _resolve_item(db: Session, code: str) -> Optional[InventoryItem]:
    c = (code or "").strip()
    if not c:
        return None
    return (db.query(InventoryItem)
            .filter(or_(InventoryItem.barcode == c, InventoryItem.part_number == c,
                        InventoryItem.warehouse_receipt == c))
            .first())


def _expected_ids(ll: LoadingList) -> list:
    # Real inventory backend ids live in meta.inv_backend_ids (item_ids are frontend indexes).
    return list((ll.meta or {}).get("inv_backend_ids", []) or [])


def _reconcile(db: Session, ll: LoadingList, mode: str) -> dict:
    expected_ids = _expected_ids(ll)
    expected = db.query(InventoryItem).filter(InventoryItem.id.in_(expected_ids)).all() if expected_ids else []
    scanned_ids = {s.inventory_item_id for s in db.query(ScanEvent).filter(
        ScanEvent.loading_list_id == ll.id, ScanEvent.mode == mode,
        ScanEvent.inventory_item_id.isnot(None)).all()}
    exp_set = set(expected_ids)
    matched = [i for i in expected if i.id in scanned_ids]
    missing = [i for i in expected if i.id not in scanned_ids]
    unexpected_ids = [sid for sid in scanned_ids if sid not in exp_set]
    unexpected = db.query(InventoryItem).filter(InventoryItem.id.in_(unexpected_ids)).all() if unexpected_ids else []
    total = len(expected)
    return {
        "loading_list_id": ll.id, "public_id": ll.public_id, "mode": mode,
        "expected_total": total,
        "matched_count": len(matched), "missing_count": len(missing), "unexpected_count": len(unexpected),
        "matched": [_item_out(i) for i in matched],
        "missing": [_item_out(i) for i in missing],
        "unexpected": [_item_out(i) for i in unexpected],
        "progress": round(100 * len(matched) / total) if total else 0,
        "complete": total > 0 and len(matched) == total and not unexpected,
    }


@router.post("/scan")
def scan(payload: ScanIn, request: Request, db: Session = Depends(get_db),
         claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    mode = (payload.mode or "").lower()
    if mode == "gangway":
        raise HTTPException(status_code=400,
                            detail="Gangway (ship-side) scanning needs MSC integration — not available yet")
    if mode not in SCAN_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {sorted(SCAN_MODES)}")

    ll = None
    if mode in LL_MODES:
        if not payload.loading_list_id:
            raise HTTPException(status_code=400, detail=f"{mode} scan requires an active loading_list_id")
        ll = db.get(LoadingList, payload.loading_list_id)
        if not ll:
            raise HTTPException(status_code=404, detail="Loading list not found")

    item = _resolve_item(db, payload.code)

    if item is None:
        result = "unknown"
    elif mode == "receiving":
        result = "received"
    else:  # picking / loading — classify against the loading list's expected items
        if item.id not in set(_expected_ids(ll)):
            result = "unexpected"
        else:
            already = db.query(ScanEvent).filter(
                ScanEvent.loading_list_id == ll.id, ScanEvent.mode == mode,
                ScanEvent.inventory_item_id == item.id, ScanEvent.result == "matched").first()
            result = "duplicate" if already else "matched"

    db.add(ScanEvent(loading_list_id=(ll.id if ll else None), mode=mode,
                     code=(payload.code or "").strip(), inventory_item_id=(item.id if item else None),
                     result=result, scanned_by=claims.get("name")))
    db.commit()

    out = {"result": result, "code": (payload.code or "").strip(), "mode": mode,
           "item": _item_out(item) if item else None}
    if ll is not None:
        out["reconciliation"] = _reconcile(db, ll, mode)
        broadcast("barcoding.changed", {"action": "scan", "loading_list_id": ll.id, "result": result})
    return out


@router.get("/reconcile/{ll_id}", dependencies=[Depends(require_auth)])
def reconcile(ll_id: int, mode: str = Query(default="loading"), db: Session = Depends(get_db)):
    if mode not in LL_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {sorted(LL_MODES)}")
    ll = db.get(LoadingList, ll_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Loading list not found")
    return _reconcile(db, ll, mode)


@router.get("/session/{ll_id}", dependencies=[Depends(require_auth)])
def session(ll_id: int, mode: str = Query(default="loading"), limit: int = 50, db: Session = Depends(get_db)):
    evs = (db.query(ScanEvent).filter(ScanEvent.loading_list_id == ll_id, ScanEvent.mode == mode)
           .order_by(ScanEvent.scanned_at.desc()).limit(min(max(limit, 1), 200)).all())
    return [{"id": e.id, "code": e.code, "result": e.result, "inventory_item_id": e.inventory_item_id,
             "scanned_by": e.scanned_by, "scanned_at": e.scanned_at.isoformat() + "Z"} for e in evs]
