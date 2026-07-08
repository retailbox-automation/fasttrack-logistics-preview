"""Customs status & document tracking (Stage 1.9).

Formalizes per-shipment customs into a tracked, auditable record: ISF / 7512 /
AES-SED filing status, entry number, bonded status + 3-business-day release
timer, FIRMS code, and a list of document requests/responses. Computes a
"loading readiness" verdict (cleared + blockers) so a shipment isn't dispatched
with customs open — tying customs status to loading-list readiness per the spec's
Customs Communication Layer. Also the groundwork for moving customs filing off
Magaya (off-ramp direction).

Ops-manual rules encoded as blockers: cargo without 7512 is refused; ISF filed
48h before EU sailing; bonded stock releases after 3 business days; FIRMS LCS5.
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CustomsRecord, LoadingList
from app.schemas import CustomsRecordCreate, CustomsRecordUpdate, CustomsRecordOut
from app.auth import require_auth, require_roles
from app.audit import log_audit
from app.events import broadcast

router = APIRouter(prefix="/api/customs", tags=["customs"])

_CLEARED = {"filed", "cleared"}
_FILING_STATES = {"na", "pending", "requested", "filed", "cleared"}
_DOC_STATES = {"na", "pending", "requested", "received", "filed", "cleared"}
_STATUSES = {"open", "cleared", "hold"}


def _validate_states(*, isf=None, doc7512=None, aes=None, status=None, docs=None):
    """Reject unknown enum values (fail loud, not store garbage)."""
    for name, val, allowed in (
        ("isf_status", isf, _FILING_STATES),
        ("doc_7512_status", doc7512, _FILING_STATES),
        ("aes_sed_status", aes, _FILING_STATES),
        ("status", status, _STATUSES),
    ):
        if val is not None and val not in allowed:
            raise HTTPException(status_code=400, detail=f"{name} must be one of {sorted(allowed)}")
    for d in (docs or []):
        ds = d.get("status") if isinstance(d, dict) else getattr(d, "status", None)
        if ds is not None and ds not in _DOC_STATES:
            raise HTTPException(status_code=400, detail=f"document status must be one of {sorted(_DOC_STATES)}")


def _blockers(rec: CustomsRecord) -> list[str]:
    """What still stands between this shipment and customs clearance."""
    b = []
    if rec.status == "hold":
        b.append("On customs hold")
    if (rec.doc_7512_status or "pending") not in _CLEARED:
        b.append("7512 not filed (cargo refused without it)")
    if (rec.isf_status or "pending") not in _CLEARED | {"na"}:
        b.append("ISF pending (file 48h before EU sailing)")
    if (rec.aes_sed_status or "na") not in _CLEARED | {"na"}:
        b.append("AES/SED pending")
    if rec.bonded and rec.bonded_release_due and rec.bonded_release_due > date.today():
        b.append(f"Bonded stock not released until {rec.bonded_release_due.isoformat()} (3-business-day rule)")
    for d in (rec.docs or []):
        if isinstance(d, dict) and (d.get("status") or "pending") in ("pending", "requested"):
            b.append(f"Document outstanding: {d.get('type') or 'document'}")
    return b


def _to_out(rec: CustomsRecord) -> CustomsRecordOut:
    out = CustomsRecordOut.model_validate(rec)
    out.blockers = _blockers(rec)
    out.cleared = not out.blockers
    return out


def _next_public_id(db: Session) -> str:
    year = date.today().year
    n = db.query(CustomsRecord).count() + 1
    while db.query(CustomsRecord).filter(CustomsRecord.public_id == f"CE-{year}-{n:04d}").first():
        n += 1
    return f"CE-{year}-{n:04d}"


@router.get("", response_model=list[CustomsRecordOut], dependencies=[Depends(require_auth)])
def list_records(shipment_public_id: str | None = None, db: Session = Depends(get_db)):
    q = db.query(CustomsRecord)
    if shipment_public_id:
        q = q.filter(CustomsRecord.shipment_public_id == shipment_public_id)
    return [_to_out(r) for r in q.order_by(CustomsRecord.id.desc()).all()]


@router.get("/{rec_id}", response_model=CustomsRecordOut, dependencies=[Depends(require_auth)])
def get_record(rec_id: int, db: Session = Depends(get_db)):
    rec = db.get(CustomsRecord, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Customs record not found")
    return _to_out(rec)


@router.post("", response_model=CustomsRecordOut)
def create_record(payload: CustomsRecordCreate, request: Request, db: Session = Depends(get_db),
                  claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    _validate_states(isf=payload.isf_status, doc7512=payload.doc_7512_status,
                     aes=payload.aes_sed_status, docs=payload.docs)
    public_id = (payload.public_id or "").strip() or _next_public_id(db)
    if db.query(CustomsRecord).filter(CustomsRecord.public_id == public_id).first():
        raise HTTPException(status_code=409, detail=f"Customs record {public_id} already exists")
    linked_ll = None
    if payload.shipment_public_id:
        linked_ll = db.query(LoadingList).filter(LoadingList.public_id == payload.shipment_public_id).first()
        if not linked_ll:
            raise HTTPException(status_code=400, detail=f"No shipment {payload.shipment_public_id} to link")
    # Auto-fill from the linked shipment when the field is blank.
    vessel = payload.vessel or (linked_ll.vessel if linked_ll else None)

    rec = CustomsRecord(
        public_id=public_id,
        shipment_public_id=payload.shipment_public_id,
        entry_number=payload.entry_number,
        vessel=vessel,
        broker=payload.broker,
        firms_code=payload.firms_code or "LCS5",
        isf_status=payload.isf_status or "pending",
        doc_7512_status=payload.doc_7512_status or "pending",
        aes_sed_status=payload.aes_sed_status or "na",
        bonded=bool(payload.bonded),
        bonded_release_due=payload.bonded_release_due,
        sailing_date=payload.sailing_date,
        notes=payload.notes,
        docs=[d.model_dump() for d in payload.docs],
        created_by=claims.get("name"),
    )
    # Open by default; auto-mark cleared if nothing blocks it at creation.
    rec.status = "cleared" if not _blockers(rec) else "open"
    db.add(rec)
    db.commit()
    db.refresh(rec)
    log_audit(db, claims, "create", "customs_record", entity_id=str(rec.id),
              summary=f"Customs {rec.public_id}" + (f" → {rec.shipment_public_id}" if rec.shipment_public_id else ""),
              ip=request.client.host if request.client else None)
    broadcast("customs.changed", {"action": "create", "id": rec.id, "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
    return _to_out(rec)


@router.patch("/{rec_id}", response_model=CustomsRecordOut)
def update_record(rec_id: int, payload: CustomsRecordUpdate, request: Request, db: Session = Depends(get_db),
                  claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    rec = db.get(CustomsRecord, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Customs record not found")
    data = payload.model_dump(exclude_unset=True)
    _validate_states(isf=data.get("isf_status"), doc7512=data.get("doc_7512_status"),
                     aes=data.get("aes_sed_status"), status=data.get("status"), docs=data.get("docs"))
    if "docs" in data and data["docs"] is not None:
        rec.docs = data.pop("docs")
    if data.get("shipment_public_id") and not db.query(LoadingList).filter(
            LoadingList.public_id == data["shipment_public_id"]).first():
        raise HTTPException(status_code=400, detail=f"No shipment {data['shipment_public_id']} to link")
    explicit_status = data.pop("status", None)
    for k, v in data.items():
        setattr(rec, k, v)
    # Recompute clearance unless the user explicitly set a status (e.g. manual hold).
    if explicit_status:
        rec.status = explicit_status
    elif rec.status != "hold":
        rec.status = "cleared" if not _blockers(rec) else "open"
    db.commit()
    db.refresh(rec)
    log_audit(db, claims, "update", "customs_record", entity_id=str(rec.id),
              summary=f"Updated customs {rec.public_id}: {list(data.keys()) + (['status'] if explicit_status else [])}",
              ip=request.client.host if request.client else None)
    broadcast("customs.changed", {"action": "update", "id": rec.id, "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
    return _to_out(rec)


@router.delete("/{rec_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_record(rec_id: int, request: Request, db: Session = Depends(get_db),
                  claims: dict = Depends(require_roles("admin", "manager"))):
    rec = db.get(CustomsRecord, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Customs record not found")
    pid = rec.public_id
    db.delete(rec)
    db.commit()
    log_audit(db, claims, "delete", "customs_record", entity_id=str(rec_id), summary=f"Deleted customs {pid}",
              ip=request.client.host if request.client else None)
    broadcast("customs.changed", {"action": "delete", "id": rec_id, "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
