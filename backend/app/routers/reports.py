"""Weekly operations report — server-side aggregation (Stage 4.1).

Authoritative weekly ops summary aggregated from typed data for a Mon-Sun window:
dispatches/trucking, warehouse receipts, billing, customs, staff hours, and
exceptions/pending. This is the foundation for auto-generated / emailed weekly
reports; the approval cycle is already wired separately (/api/approvals).

GET /api/reports/weekly?start=YYYY-MM-DD  (start snaps to that week's Monday;
defaults to the current week). Manager/admin.
"""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import LoadingList, WarehouseReceipt, Invoice, CreditMemo, CustomsRecord, TimeEntry
from app.auth import require_roles

router = APIRouter(prefix="/api/reports", tags=["reports"])

_DISPATCHED = {"dispatched", "loaded", "received", "sent"}


def _inv_total(inv) -> float:
    sub = sum(float((l or {}).get("qty") or 0) * float((l or {}).get("rate") or 0) for l in (inv.lines or []))
    return round(sub * (1 + float(inv.fuel or 0) / 100.0), 2)


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


@router.get("/weekly", dependencies=[Depends(require_roles("admin", "manager"))])
def weekly(start: str | None = Query(default=None), db: Session = Depends(get_db)):
    try:
        wk_start = _monday(date.fromisoformat(start)) if start else _monday(date.today())
    except (ValueError, TypeError):
        wk_start = _monday(date.today())
    wk_end = wk_start + timedelta(days=6)

    def in_d(d):
        return d is not None and wk_start <= d <= wk_end

    def in_dt(dt):
        return dt is not None and wk_start <= dt.date() <= wk_end

    # Dispatches / trucking
    lls = [ll for ll in db.query(LoadingList).all() if (ll.status or "") in _DISPATCHED and in_d(ll.departure)]
    dispatches = {
        "loads": len(lls),
        "vessels": len({ll.vessel for ll in lls if ll.vessel}),
        "trucks": len({ll.truck for ll in lls if ll.truck}),
        "items": [{"public_id": ll.public_id, "vessel": ll.vessel, "truck": ll.truck,
                   "departure": ll.departure.isoformat() if ll.departure else None,
                   "status": ll.status} for ll in lls],
    }

    # Warehouse receipts
    wrs = [w for w in db.query(WarehouseReceipt).all() if in_d(w.received_date)]

    def _wsum(key):
        return round(sum(float((w.totals or {}).get(key) or 0) for w in wrs), 1)

    receipts = {"count": len(wrs), "pieces": _wsum("pieces"), "pallets": _wsum("pallets"), "weight_lb": _wsum("weight_lb")}

    # Billing
    invs = [i for i in db.query(Invoice).all() if in_d(i.invoice_date)]
    by_status, inv_total = {}, 0.0
    for i in invs:
        t = _inv_total(i)
        inv_total += t
        by_status[i.status] = round(by_status.get(i.status, 0.0) + t, 2)
    cms = [c for c in db.query(CreditMemo).all() if in_d(c.issue_date)]
    billing = {
        "invoices": len(invs), "invoiced_total": round(inv_total, 2), "by_status": by_status,
        "credit_memos": len(cms), "credits_total": round(sum(float(c.amount or 0) for c in cms), 2),
    }

    # Customs
    cust = [c for c in db.query(CustomsRecord).all() if in_dt(c.created_at)]
    customs = {"count": len(cust),
               "cleared": sum(1 for c in cust if c.status == "cleared"),
               "open": sum(1 for c in cust if c.status == "open"),
               "hold": sum(1 for c in cust if c.status == "hold")}

    # Staff hours
    tes = [t for t in db.query(TimeEntry).all() if in_dt(t.clock_in_at)]
    mins_by_user, total_min = {}, 0
    for t in tes:
        m = int(((t.clock_out_at or t.clock_in_at) - t.clock_in_at).total_seconds() // 60)
        total_min += m
        mins_by_user[t.user_name] = mins_by_user.get(t.user_name, 0) + m
    staff_hours = {"entries": len(tes), "total_hours": round(total_min / 60, 1),
                   "by_user": {u: round(m / 60, 1) for u, m in sorted(mins_by_user.items())}}

    # Exceptions / pending
    pend_inv = [i for i in db.query(Invoice).all() if i.status in ("draft", "pending_approval")]
    open_cust = db.query(CustomsRecord).filter(CustomsRecord.status.in_(["open", "hold"])).count()
    exceptions = {
        "invoices_pending": len(pend_inv),
        "invoices_pending_total": round(sum(_inv_total(i) for i in pend_inv), 2),
        "customs_open_or_hold": open_cust,
    }

    return {
        "week": {"start": wk_start.isoformat(), "end": wk_end.isoformat()},
        "dispatches": dispatches, "receipts": receipts, "billing": billing,
        "customs": customs, "staff_hours": staff_hours, "exceptions": exceptions,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
