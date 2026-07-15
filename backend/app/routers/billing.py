"""Billing — typed Invoices (Stage 2.1, vertical 1).

Promotes invoices from the generic JSON-blob docs layer (PUT/GET /api/docs/invoices,
whole-array replace) to a typed, persisted resource with server-side totals, status
validation, and audit — same pattern as customs.py / warehouse_receipts.py. SDRs and
credit memos follow in the next vertical, then SOA + reconciliation move server-side.

Totals (server-authoritative): subtotal = Σ(qty × rate) over lines; fuel surcharge =
subtotal × fuel%; total = subtotal + fuel. The trucking/import/other op breakdown the
frontend shows needs the rate card and arrives with the rate matrix (Stage 2.2) — the
server has no ADS rate lookup yet.

Additive: the generic /api/docs/invoices layer stays live until the frontend is rewired
(Slice C), so mounting this does not affect prod.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Invoice, ShipmentDetailReport, CreditMemo
from app.schemas import (
    InvoiceCreate, InvoiceUpdate, InvoiceOut, INVOICE_STATES,
    SDRCreate, SDRUpdate, SDROut, SDR_STATES,
    CreditMemoCreate, CreditMemoUpdate, CreditMemoOut, CM_STATES, SOA_POSITIONS,
)
from app.auth import require_auth, require_roles
from app.audit import log_audit
from app.events import broadcast

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


def _line_subtotal(lines: list) -> float:
    total = 0.0
    for ln in (lines or []):
        qty = (ln.get("qty") if isinstance(ln, dict) else getattr(ln, "qty", 0)) or 0
        rate = (ln.get("rate") if isinstance(ln, dict) else getattr(ln, "rate", 0)) or 0
        total += float(qty) * float(rate)
    return round(total, 2)


def _to_out(inv: Invoice) -> InvoiceOut:
    out = InvoiceOut.model_validate(inv)
    subtotal = _line_subtotal(inv.lines)
    fuel_amt = round(subtotal * (float(inv.fuel or 0) / 100.0), 2)
    out.subtotal = subtotal
    out.fuel_amt = fuel_amt
    out.total = round(subtotal + fuel_amt, 2)
    return out


def _validate_status(status_val) -> None:
    if status_val is not None and status_val not in INVOICE_STATES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(INVOICE_STATES)}")


def _next_public_id(db: Session) -> str:
    n = db.query(Invoice).count() + 1
    while db.query(Invoice).filter(Invoice.public_id == f"FT-INV-{n:05d}").first():
        n += 1
    return f"FT-INV-{n:05d}"


@router.get("", response_model=list[InvoiceOut], dependencies=[Depends(require_auth)])
def list_invoices(bill_to: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Invoice)
    if bill_to:
        q = q.filter(Invoice.bill_to == bill_to)
    if status:
        q = q.filter(Invoice.status == status)
    return [_to_out(i) for i in q.order_by(Invoice.id.desc()).all()]


@router.get("/{inv_id}", response_model=InvoiceOut, dependencies=[Depends(require_auth)])
def get_invoice(inv_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return _to_out(inv)


@router.post("", response_model=InvoiceOut)
def create_invoice(payload: InvoiceCreate, request: Request, db: Session = Depends(get_db),
                   claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    _validate_status(payload.status)
    public_id = (payload.public_id or "").strip() or _next_public_id(db)
    if db.query(Invoice).filter(Invoice.public_id == public_id).first():
        raise HTTPException(status_code=409, detail=f"Invoice {public_id} already exists")
    inv = Invoice(
        public_id=public_id,
        sdr_public_id=payload.sdr_public_id,
        loading_list_public_id=payload.loading_list_public_id,
        pon=payload.pon,
        invoice_date=payload.invoice_date or date.today(),
        bill_to=payload.bill_to,
        attn=payload.attn,
        terms=payload.terms,
        currency=payload.currency,
        fuel=payload.fuel,
        gwx=payload.gwx,
        cruise_id=payload.cruise_id,
        issued_by=payload.issued_by or claims.get("name"),
        out_of_scope=bool(payload.out_of_scope),
        status=payload.status or "draft",
        notes=payload.notes,
        lines=[ln.model_dump() for ln in payload.lines],
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    out = _to_out(inv)
    ip = request.client.host if request.client else None
    log_audit(db, claims, "create", "invoice", entity_id=str(inv.id),
              summary=f"Invoice {inv.public_id} · {inv.bill_to} · ${out.total:,.2f}",
              payload={"public_id": inv.public_id, "total": out.total, "status": inv.status}, ip=ip)
    broadcast("invoices.changed", {"action": "create", "id": inv.id,
                                   "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
    return out


@router.patch("/{inv_id}", response_model=InvoiceOut)
def update_invoice(inv_id: int, payload: InvoiceUpdate, request: Request, db: Session = Depends(get_db),
                   claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    data = payload.model_dump(exclude_unset=True)
    _validate_status(data.get("status"))
    if "lines" in data and data["lines"] is not None:
        inv.lines = data.pop("lines")
    for k, v in data.items():
        setattr(inv, k, v)
    db.commit()
    db.refresh(inv)
    out = _to_out(inv)
    log_audit(db, claims, "update", "invoice", entity_id=str(inv.id),
              summary=f"Updated invoice {inv.public_id}: {list(data.keys())}",
              ip=request.client.host if request.client else None)
    broadcast("invoices.changed", {"action": "update", "id": inv.id,
                                   "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
    return out


@router.delete("/{inv_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_invoice(inv_id: int, request: Request, db: Session = Depends(get_db),
                   claims: dict = Depends(require_roles("admin", "manager"))):
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    pid = inv.public_id
    db.delete(inv)
    db.commit()
    log_audit(db, claims, "delete", "invoice", entity_id=str(inv_id), summary=f"Deleted invoice {pid}",
              ip=request.client.host if request.client else None)
    broadcast("invoices.changed", {"action": "delete", "id": inv_id,
                                   "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})


# ─────────────────────────── Shipment Detail Reports (SDR) ───────────────────────────
sdr_router = APIRouter(prefix="/api/sdrs", tags=["sdrs"])


def _validate_sdr_status(status_val) -> None:
    if status_val is not None and status_val not in SDR_STATES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(SDR_STATES)}")


def _next_sdr_id(db: Session) -> str:
    year = date.today().year
    n = db.query(ShipmentDetailReport).count() + 1
    while db.query(ShipmentDetailReport).filter(ShipmentDetailReport.public_id == f"SDR-{year}-{n:04d}").first():
        n += 1
    return f"SDR-{year}-{n:04d}"


@sdr_router.get("", response_model=list[SDROut], dependencies=[Depends(require_auth)])
def list_sdrs(bill_to: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(ShipmentDetailReport)
    if bill_to:
        q = q.filter(ShipmentDetailReport.bill_to == bill_to)
    if status:
        q = q.filter(ShipmentDetailReport.status == status)
    return q.order_by(ShipmentDetailReport.id.desc()).all()


@sdr_router.get("/{sdr_id}", response_model=SDROut, dependencies=[Depends(require_auth)])
def get_sdr(sdr_id: int, db: Session = Depends(get_db)):
    rec = db.get(ShipmentDetailReport, sdr_id)
    if not rec:
        raise HTTPException(status_code=404, detail="SDR not found")
    return rec


@sdr_router.post("", response_model=SDROut)
def create_sdr(payload: SDRCreate, request: Request, db: Session = Depends(get_db),
               claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    _validate_sdr_status(payload.status)
    public_id = (payload.public_id or "").strip() or _next_sdr_id(db)
    if db.query(ShipmentDetailReport).filter(ShipmentDetailReport.public_id == public_id).first():
        raise HTTPException(status_code=409, detail=f"SDR {public_id} already exists")
    rec = ShipmentDetailReport(
        public_id=public_id,
        gwx=payload.gwx,
        loading_list_public_id=payload.loading_list_public_id,
        bill_to=payload.bill_to,
        ms_contact=payload.ms_contact,
        prepared_by=payload.prepared_by or claims.get("name"),
        period_start=payload.period_start,
        period_end=payload.period_end,
        pon=payload.pon,
        status=payload.status or "draft",
        notes=payload.notes,
        variables=payload.variables,
        invoice_public_id=payload.invoice_public_id,
        qtys=payload.qtys or {},
        sent_at=payload.sent_at,
        variables_at=payload.variables_at,
        po_received_at=payload.po_received_at,
        closed_at=payload.closed_at,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    log_audit(db, claims, "create", "sdr", entity_id=str(rec.id),
              summary=f"SDR {rec.public_id} · {rec.bill_to} · {rec.status}",
              ip=request.client.host if request.client else None)
    broadcast("sdrs.changed", {"action": "create", "id": rec.id,
                               "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
    return rec


@sdr_router.patch("/{sdr_id}", response_model=SDROut)
def update_sdr(sdr_id: int, payload: SDRUpdate, request: Request, db: Session = Depends(get_db),
               claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    rec = db.get(ShipmentDetailReport, sdr_id)
    if not rec:
        raise HTTPException(status_code=404, detail="SDR not found")
    data = payload.model_dump(exclude_unset=True)
    _validate_sdr_status(data.get("status"))
    for k, v in data.items():
        setattr(rec, k, v)
    db.commit()
    db.refresh(rec)
    log_audit(db, claims, "update", "sdr", entity_id=str(rec.id),
              summary=f"Updated SDR {rec.public_id}: {list(data.keys())}",
              ip=request.client.host if request.client else None)
    broadcast("sdrs.changed", {"action": "update", "id": rec.id,
                               "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
    return rec


@sdr_router.delete("/{sdr_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_sdr(sdr_id: int, request: Request, db: Session = Depends(get_db),
               claims: dict = Depends(require_roles("admin", "manager"))):
    rec = db.get(ShipmentDetailReport, sdr_id)
    if not rec:
        raise HTTPException(status_code=404, detail="SDR not found")
    pid = rec.public_id
    db.delete(rec)
    db.commit()
    log_audit(db, claims, "delete", "sdr", entity_id=str(sdr_id), summary=f"Deleted SDR {pid}",
              ip=request.client.host if request.client else None)
    broadcast("sdrs.changed", {"action": "delete", "id": sdr_id,
                               "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})


# ─────────────────────────────── Credit Memos ───────────────────────────────
cm_router = APIRouter(prefix="/api/credit-memos", tags=["credit-memos"])


def _validate_cm(status_val=None, soa=None) -> None:
    if status_val is not None and status_val not in CM_STATES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(CM_STATES)}")
    if soa is not None and soa not in SOA_POSITIONS:
        raise HTTPException(status_code=400, detail=f"soa_position must be one of {sorted(SOA_POSITIONS)}")


def _next_cm_id(db: Session) -> str:
    n = db.query(CreditMemo).count() + 1
    while db.query(CreditMemo).filter(CreditMemo.public_id == f"FT-CM-{n:04d}").first():
        n += 1
    return f"FT-CM-{n:04d}"


@cm_router.get("", response_model=list[CreditMemoOut], dependencies=[Depends(require_auth)])
def list_credit_memos(applied_to: str | None = None, db: Session = Depends(get_db)):
    q = db.query(CreditMemo)
    if applied_to:
        q = q.filter(CreditMemo.applied_to_invoice_public_id == applied_to)
    return q.order_by(CreditMemo.id.desc()).all()


@cm_router.get("/{cm_id}", response_model=CreditMemoOut, dependencies=[Depends(require_auth)])
def get_credit_memo(cm_id: int, db: Session = Depends(get_db)):
    rec = db.get(CreditMemo, cm_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    return rec


@cm_router.post("", response_model=CreditMemoOut)
def create_credit_memo(payload: CreditMemoCreate, request: Request, db: Session = Depends(get_db),
                       claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    _validate_cm(payload.status, payload.soa_position)
    public_id = (payload.public_id or "").strip() or _next_cm_id(db)
    if db.query(CreditMemo).filter(CreditMemo.public_id == public_id).first():
        raise HTTPException(status_code=409, detail=f"Credit memo {public_id} already exists")
    rec = CreditMemo(
        public_id=public_id,
        applied_to_invoice_public_id=payload.applied_to_invoice_public_id,
        amount=payload.amount,
        issue_date=payload.issue_date or date.today(),
        due_date=payload.due_date,
        reason=payload.reason,
        description=payload.description,
        issued_by=payload.issued_by or claims.get("name"),
        status=payload.status or "open",
        soa_position=payload.soa_position or "below",
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    log_audit(db, claims, "create", "credit_memo", entity_id=str(rec.id),
              summary=f"Credit memo {rec.public_id} → {rec.applied_to_invoice_public_id} · ${rec.amount:,.2f}",
              ip=request.client.host if request.client else None)
    broadcast("credit_memos.changed", {"action": "create", "id": rec.id,
                                       "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
    return rec


@cm_router.patch("/{cm_id}", response_model=CreditMemoOut)
def update_credit_memo(cm_id: int, payload: CreditMemoUpdate, request: Request, db: Session = Depends(get_db),
                       claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    rec = db.get(CreditMemo, cm_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    data = payload.model_dump(exclude_unset=True)
    _validate_cm(data.get("status"), data.get("soa_position"))
    for k, v in data.items():
        setattr(rec, k, v)
    db.commit()
    db.refresh(rec)
    log_audit(db, claims, "update", "credit_memo", entity_id=str(rec.id),
              summary=f"Updated credit memo {rec.public_id}: {list(data.keys())}",
              ip=request.client.host if request.client else None)
    broadcast("credit_memos.changed", {"action": "update", "id": rec.id,
                                       "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
    return rec


@cm_router.delete("/{cm_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_credit_memo(cm_id: int, request: Request, db: Session = Depends(get_db),
                       claims: dict = Depends(require_roles("admin", "manager"))):
    rec = db.get(CreditMemo, cm_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Credit memo not found")
    pid = rec.public_id
    db.delete(rec)
    db.commit()
    log_audit(db, claims, "delete", "credit_memo", entity_id=str(cm_id), summary=f"Deleted credit memo {pid}",
              ip=request.client.host if request.client else None)
    broadcast("credit_memos.changed", {"action": "delete", "id": cm_id,
                                       "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
