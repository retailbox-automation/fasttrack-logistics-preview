"""Billing seed / backfill (Slice C.1).

Populates the typed billing tables (invoices, shipment_detail_reports, credit_memos)
when they are EMPTY, from one of two sources (same camelCase shape the frontend persists):
  1. existing generic-docs blobs (Document.kind in invoices/sdrs/credit_memos) — real data
     an operator already entered; else
  2. the demo dataset below — identical to the frontend seed the client already sees — so the
     billing UI stays populated after it's rewired to the typed endpoints (Slice C.2).

Idempotent: a kind is seeded only if its typed table is empty (runs once; safe on redeploy).
Inserts via the ORM directly — NOT the POST endpoints, which require auth, reject unknown keys
(e.g. createdAt), and would overwrite issued_by/prepared_by with the signed-in user. public_id
is set explicitly (never auto-generated) so cross-references (sdrId↔invoiceId↔appliedToInvoiceId)
stay intact. Leaves the generic /api/docs layer live in parallel until the frontend is rewired.
"""
from datetime import date, datetime
import logging

from app.database import SessionLocal
from app.models import Document, Invoice, ShipmentDetailReport, CreditMemo

log = logging.getLogger("ft.billing_seed")


# ── coercion helpers (empty string / None → None; only for nullable columns) ──
def _nz(v):
    return v if (v is not None and v != "") else None


def _d(s):  # date from 'YYYY-MM-DD'
    s = _nz(s)
    try:
        return date.fromisoformat(s) if s else None
    except (ValueError, TypeError):
        return None


def _dt(s):  # datetime from 'YYYY-MM-DD HH:MM' (fallbacks incl. plain date)
    s = _nz(s)
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ── camelCase dict → ORM row (shared by docs-backfill and demo seed) ──
def _invoice_from(d: dict) -> Invoice:
    return Invoice(
        public_id=d["id"],
        sdr_public_id=_nz(d.get("sdrId")),
        loading_list_public_id=_nz(d.get("loadingListId")),
        pon=d.get("pon") or "",                        # NOT-NULL: keep ""
        invoice_date=_d(d.get("invoiceDate")) or date.today(),
        issue_date=_dt(d.get("issueDate")) or datetime.utcnow(),
        bill_to=d.get("billTo") or "",                 # NOT-NULL: keep ""
        attn=_nz(d.get("attn")),
        terms=d.get("terms") or "NET 30",
        currency=d.get("currency") or "USD",
        fuel=float(d.get("fuel") or 0),
        gwx=_nz(d.get("gwx")),
        cruise_id=_nz(d.get("cruiseId")),
        issued_by=_nz(d.get("issuedBy")),
        out_of_scope=bool(d.get("outOfScope", False)),
        status=d.get("status") or "draft",
        notes=_nz(d.get("notes")),
        lines=d.get("lines") or [],
    )


def _sdr_from(d: dict) -> ShipmentDetailReport:
    return ShipmentDetailReport(
        public_id=d["id"],
        gwx=d.get("gwx") or "",                        # NOT-NULL
        loading_list_public_id=_nz(d.get("loadingListId")),
        bill_to=d.get("billTo") or "",                 # NOT-NULL
        ms_contact=_nz(d.get("msContact")),
        prepared_by=d.get("preparedBy") or "",         # NOT-NULL
        period_start=_d(d.get("periodStart")),
        period_end=_d(d.get("periodEnd")),
        pon=_nz(d.get("pon")),
        status=d.get("status") or "draft",
        notes=_nz(d.get("notes")),
        variables=_nz(d.get("variables")),
        invoice_public_id=_nz(d.get("invoiceId")),
        qtys=d.get("qtys") or {},
        sent_at=_dt(d.get("sentAt")),
        variables_at=_dt(d.get("variablesAt")),
        po_received_at=_dt(d.get("poReceivedAt")),
        closed_at=_dt(d.get("closedAt")),
        created_at=_dt(d.get("createdAt")) or datetime.utcnow(),
    )


def _cm_from(d: dict) -> CreditMemo:
    return CreditMemo(
        public_id=d["id"],
        applied_to_invoice_public_id=d.get("appliedToInvoiceId") or "",   # NOT-NULL
        amount=float(d.get("amount") or 0),
        issue_date=_d(d.get("issueDate")) or date.today(),
        due_date=_d(d.get("dueDate")),
        reason=d.get("reason") or "Other",             # NOT-NULL
        description=_nz(d.get("description")),
        issued_by=_nz(d.get("issuedBy")),
        status=d.get("status") or "open",
        soa_position=d.get("soaPosition") or "below",
    )


# ── demo dataset (verbatim from the frontend seed the client already sees) ──
_DEMO_INVOICES = [
    {"id": "FT-INV-46225", "sdrId": "SDR-2026-0048", "loadingListId": "LL-2026-0048",
     "pon": "4597196559-OCY00018", "invoiceDate": "2026-05-31", "issueDate": "2026-05-31 16:08",
     "billTo": "MSC Ocean Cay", "attn": "Jorge Vega · Logistics Manager USA", "terms": "NET 30",
     "currency": "USD", "fuel": 15, "gwx": "GWX-2026-0048", "cruiseId": "SS20260530MIAOC",
     "issuedBy": "Gabriela Pita", "status": "paid",
     "notes": "Loading List LL-2026-0048 dispatch · MSC Seashore · Miami turnaround. Includes $42 credit adjustment for DR-2026-0010.",
     "lines": [
         {"code": "ADS-002655", "desc": "Truck delivery at port 53ft (dry)", "qty": 1, "rate": 612.00},
         {"code": "ADS-002954", "desc": "Loading / unloading", "qty": 1, "rate": 480.00},
         {"code": "ADS-000022", "desc": "PO handling", "qty": 1, "rate": 30.00},
         {"code": "ADS-03257", "desc": "Pallet in transit", "qty": 7, "rate": 25.00},
         {"code": "ADS-002856", "desc": "Bonded fee", "qty": 1, "rate": 120.00},
     ]},
    {"id": "FT-INV-46247", "sdrId": "SDR-2026-0049", "loadingListId": "LL-2026-0049",
     "pon": "4597196559-OCY00021", "invoiceDate": "2026-06-02", "issueDate": "2026-06-02 11:22",
     "billTo": "MSC Ocean Cay", "attn": "Jorge Vega · Logistics Manager USA", "terms": "NET 30",
     "currency": "USD", "fuel": 15, "gwx": "GWX-2026-0049", "cruiseId": "SE20260602MIAOC",
     "issuedBy": "Luis Cruz", "status": "sent",
     "notes": "Loading List LL-2026-0049 dispatch · MSC Seascape · Ocean Cay turnaround. DR-2026-0011 investigation pending — may require adjustment.",
     "lines": [
         {"code": "ADS-002655", "desc": "Truck delivery at port 53ft (dry)", "qty": 1, "rate": 612.00},
         {"code": "ADS-002954", "desc": "Loading / unloading", "qty": 1, "rate": 480.00},
         {"code": "ADS-000022", "desc": "PO handling", "qty": 1, "rate": 30.00},
         {"code": "ADS-03257", "desc": "Pallet in transit", "qty": 7, "rate": 25.00},
     ]},
    {"id": "FT-INV-46258", "sdrId": None, "loadingListId": None,
     "pon": "4597196559-OCY00019", "invoiceDate": "2026-06-05", "issueDate": "2026-06-05 09:30",
     "billTo": "MSC Procurement & Logistics", "attn": "Ana Sanchez · MSC Inventory Check",
     "terms": "NET 45", "currency": "USD", "fuel": 0, "gwx": "", "cruiseId": "",
     "issuedBy": "Luis Cruz", "outOfScope": True, "status": "pending_approval",
     "notes": "May warehouse storage + inventory check reconciliation services. Manual invoice — no source LL. OUT-OF-SCOPE / Luis-managed PO direct with MSC.",
     "lines": [
         {"code": "ADS-000328", "desc": "Container storage at yard (per day)", "qty": 12, "rate": 45.00},
         {"code": "ADS-000022", "desc": "PO handling", "qty": 4, "rate": 30.00},
     ]},
    {"id": "FT-INV-46260", "sdrId": None, "loadingListId": "LL-2026-0050",
     "pon": "4597196559-OCY00024", "invoiceDate": "2026-06-07", "issueDate": "2026-06-07 14:00",
     "billTo": "MSC Procurement & Logistics", "attn": "Jorge Vega · Logistics Manager USA",
     "terms": "NET 30", "currency": "USD", "fuel": 15, "gwx": "GWX-2026-0050", "cruiseId": "MV20260608MIAPCV",
     "issuedBy": "Gabriela Pita", "status": "draft",
     "notes": "Draft for upcoming MSC Meraviglia dispatch · PCV. Pending Loading List finalization + seal/driver assignment.",
     "lines": [
         {"code": "ADS-002655", "desc": "Truck delivery at port 53ft (dry)", "qty": 1, "rate": 612.00},
         {"code": "ADS-002954", "desc": "Loading / unloading", "qty": 1, "rate": 480.00},
         {"code": "ADS-000022", "desc": "PO handling", "qty": 1, "rate": 30.00},
     ]},
]

_DEMO_SDRS = [
    {"id": "SDR-2026-0048", "gwx": "GWX-2026-0048", "loadingListId": "LL-2026-0048",
     "billTo": "MSC Procurement & Logistics", "msContact": "MSC PL Finance + Jorge Vega CC",
     "preparedBy": "Gabriela Pita", "periodStart": "2026-05-30", "periodEnd": "2026-05-30",
     "createdAt": "2026-05-29 17:00", "sentAt": "2026-05-29 17:42", "variablesAt": "2026-06-01 09:15",
     "poReceivedAt": "2026-06-01 14:30", "pon": "4597196559-OCY00018", "closedAt": "2026-05-31 16:08",
     "status": "closed", "notes": "MSC Seashore turnaround dispatch · Miami home port.",
     "variables": "No variables this cycle — standard weekend turnaround.", "invoiceId": "FT-INV-46225",
     "qtys": {"ADS-002655": 1, "ADS-002954": 1, "ADS-000022": 1, "ADS-03257": 7, "ADS-002856": 1, "ADS-001456": 1}},
    {"id": "SDR-2026-0049", "gwx": "GWX-2026-0049", "loadingListId": "LL-2026-0049",
     "billTo": "MSC Procurement & Logistics", "msContact": "MSC PL Finance + Jorge Vega CC",
     "preparedBy": "Luis Cruz", "periodStart": "2026-06-02", "periodEnd": "2026-06-02",
     "createdAt": "2026-06-01 14:00", "sentAt": "2026-06-01 14:14", "variablesAt": "",
     "poReceivedAt": "2026-06-02 11:00", "pon": "4597196559-OCY00021", "closedAt": "2026-06-02 11:22",
     "status": "closed", "notes": "MSC Seascape Ocean Cay turnaround.", "variables": "",
     "invoiceId": "FT-INV-46247",
     "qtys": {"ADS-002655": 1, "ADS-002954": 1, "ADS-000022": 1, "ADS-03257": 7, "ADS-001456": 1}},
    {"id": "SDR-2026-0050", "gwx": "GWX-2026-0050", "loadingListId": "LL-2026-0050",
     "billTo": "MSC Procurement & Logistics", "msContact": "MSC PL Finance + Jorge Vega CC",
     "preparedBy": "Gabriela Pita", "periodStart": "2026-06-08", "periodEnd": "2026-06-08",
     "createdAt": "2026-06-06 17:00", "sentAt": "", "variablesAt": "", "poReceivedAt": "",
     "pon": "", "closedAt": "", "status": "draft",
     "notes": "MSC Meraviglia PCV dispatch. Awaiting LL finalization + seal/driver assignment before sending to MSC.",
     "variables": "", "invoiceId": None,
     "qtys": {"ADS-002655": 1, "ADS-002954": 1, "ADS-000022": 1, "ADS-03257": 3, "ADS-001456": 1}},
]

_DEMO_CMS = [
    {"id": "FT-CM-0042", "appliedToInvoiceId": "FT-INV-46225", "amount": 42.00,
     "issueDate": "2026-06-02", "dueDate": "2026-06-30", "reason": "Damaged goods (MSC recovery invoice)",
     "description": "Applied to INV #FT-INV-46225. 2 boxes damaged at offload — DR-2026-0010 reference. MSC recovery invoice MSC-RI-2026-0114 received 2026-06-01.",
     "issuedBy": "Gabriela Pita", "status": "applied", "soaPosition": "below"},
    {"id": "FT-CM-0043", "appliedToInvoiceId": "FT-INV-46247", "amount": 89.40,
     "issueDate": "2026-06-08", "dueDate": "2026-07-02", "reason": "Missing items / short delivery",
     "description": "Applied to INV #FT-INV-46247. 1 box missing per MSC count (DR-2026-0011 investigation). Adjustment pending MSC confirmation.",
     "issuedBy": "Luis Cruz", "status": "open", "soaPosition": "above"},
]


def _docs_dicts(db, kind: str) -> list:
    rows = db.query(Document).filter(Document.kind == kind).order_by(Document.seq).all()
    return [r.data for r in rows if isinstance(r.data, dict)]


def _seed_billing_kind(db, model, kind: str, demo: list, mapper) -> int:
    if db.query(model).count() > 0:
        return 0  # already populated — idempotent skip
    source = _docs_dicts(db, kind) or demo
    origin = "docs" if _docs_dicts(db, kind) else "demo"
    n = 0
    for d in source:
        pid = d.get("id")
        if not pid:
            continue
        try:
            if db.query(model).filter(model.public_id == pid).first():
                continue  # dedupe within a source with duplicate ids
            db.add(mapper(d))
            db.commit()  # per-row commit → one bad row doesn't lose the batch
            n += 1
        except Exception as e:
            db.rollback()
            log.warning("billing_seed skip %s %s: %s", kind, pid, e)
    if n:
        log.info("billing_seed %s: inserted %d row(s) from %s", kind, n, origin)
    return n


def seed_billing_if_empty():
    """Seed typed billing tables from docs blobs (real data) or the demo set, if empty."""
    db = SessionLocal()
    try:
        _seed_billing_kind(db, Invoice, "invoices", _DEMO_INVOICES, _invoice_from)
        _seed_billing_kind(db, ShipmentDetailReport, "sdrs", _DEMO_SDRS, _sdr_from)
        _seed_billing_kind(db, CreditMemo, "credit_memos", _DEMO_CMS, _cm_from)
    finally:
        db.close()
