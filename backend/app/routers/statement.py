"""Billing — Statement of Account (SOA), server-side (Slice D).

Ports the client-side SOA ledger to the server: per-entity (bill_to) interleaving of
invoices + credit memos (a credit memo sits above/below its invoice per soa_position),
a running balance, and totals. No rate card needed — invoice totals come from
Σ(line qty×rate) + fuel% (same formula as the typed invoice endpoint).

Rules ported verbatim from the frontend renderSOA:
  • lines sorted by invoice_date; per invoice: emit "above" CMs, the invoice, then "below" CMs.
  • invoice: total_invoiced += total; if paid → total_paid += total (running unchanged);
    else running += total, total_outstanding += total.
  • credit memo: total_credits += amount; if the related invoice is NOT paid →
    running -= amount, total_outstanding -= amount (credits on a paid invoice don't move it).
  • outstanding_balance = max(0, total_outstanding).

Endpoints under /api/billing (NOT /api/audit — that path is the AuditLog viewer).
Manager+admin only (matches the frontend SOA view gate).
"""
import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Invoice, CreditMemo, ShipmentDetailReport, LoadingList, RateCard
from app.auth import require_roles

router = APIRouter(prefix="/api/billing", tags=["billing-statement"])


def _inv_total(inv: Invoice) -> float:
    subtotal = sum(
        float((ln or {}).get("qty") or 0) * float((ln or {}).get("rate") or 0)
        for ln in (inv.lines or [])
    )
    return round(subtotal * (1 + float(inv.fuel or 0) / 100.0), 2)


def _due_date(inv: Invoice):
    if not inv.invoice_date:
        return None
    m = re.search(r"NET\s*(\d+)", inv.terms or "", re.I)
    return inv.invoice_date + timedelta(days=int(m.group(1))) if m else inv.invoice_date


def _week(d):
    return d.isocalendar()[1] if d else None  # ISO week (frontend used a custom calc; cosmetic)


def _entities(db: Session) -> list[str]:
    return sorted({r[0] for r in db.query(Invoice.bill_to).distinct().all() if r[0]})


def _statement(db: Session, entity: str):
    invs = db.query(Invoice).filter(Invoice.bill_to == entity).all()
    invs.sort(key=lambda i: (i.invoice_date or date.min))
    cms_by_inv: dict[str, list[CreditMemo]] = {}
    for cm in db.query(CreditMemo).all():
        cms_by_inv.setdefault(cm.applied_to_invoice_public_id, []).append(cm)

    lines: list[dict] = []
    running = total_invoiced = total_paid = total_credits = total_outstanding = 0.0

    def emit_cm(cm: CreditMemo, inv_paid: bool):
        nonlocal running, total_credits, total_outstanding
        amt = float(cm.amount or 0)
        total_credits += amt
        if not inv_paid:
            running -= amt
            total_outstanding -= amt
        lines.append({
            "kind": "credit_memo", "doc_id": cm.public_id,
            "applied_to": cm.applied_to_invoice_public_id,
            "date": cm.issue_date.isoformat() if cm.issue_date else None,
            "due_date": cm.due_date.isoformat() if cm.due_date else None,
            "week": _week(cm.issue_date), "reason": cm.reason,
            "amount": -round(amt, 2), "status": cm.status,
            "soa_position": cm.soa_position, "running_balance": round(running, 2),
        })

    for inv in invs:
        paid = inv.status == "paid"
        total = _inv_total(inv)
        related = cms_by_inv.get(inv.public_id, [])
        for cm in [c for c in related if c.soa_position == "above"]:
            emit_cm(cm, paid)
        total_invoiced += total
        if paid:
            total_paid += total
        else:
            running += total
            total_outstanding += total
        due = _due_date(inv)
        lines.append({
            "kind": "invoice", "doc_id": inv.public_id,
            "source": inv.sdr_public_id or inv.loading_list_public_id or "manual",
            "date": inv.invoice_date.isoformat() if inv.invoice_date else None,
            "due_date": due.isoformat() if due else None, "week": _week(inv.invoice_date),
            "notes": inv.notes, "amount": total, "status": inv.status,
            "running_balance": round(running, 2),
        })
        for cm in [c for c in related if c.soa_position != "above"]:
            emit_cm(cm, paid)

    totals = {
        "total_invoiced": round(total_invoiced, 2),
        "credits_applied": -round(total_credits, 2),
        "paid": round(total_paid, 2),
        "outstanding_balance": round(max(0.0, total_outstanding), 2),
    }
    return lines, totals


@router.get("/statement", dependencies=[Depends(require_roles("admin", "manager"))])
def statement(bill_to: str = Query(default=None), db: Session = Depends(get_db)):
    entities = _entities(db)
    if not bill_to:
        bill_to = entities[0] if entities else None
    if not bill_to:
        return {"bill_to": None, "lines": [], "totals": {}, "line_count": 0, "entities": []}
    lines, totals = _statement(db, bill_to)
    return {"bill_to": bill_to, "lines": lines, "totals": totals,
            "line_count": len(lines), "entities": entities}


@router.get("/statement/summary", dependencies=[Depends(require_roles("admin", "manager"))])
def statement_summary(db: Session = Depends(get_db)):
    today = date.today()
    ents = []
    g = {"outstanding": 0.0, "overdue_count": 0, "overdue_amount": 0.0,
         "due_soon_count": 0, "total_invoiced": 0.0, "paid": 0.0}
    for e in _entities(db):
        _, totals = _statement(db, e)
        invs = db.query(Invoice).filter(Invoice.bill_to == e).all()
        overdue = due_soon = 0
        for inv in invs:
            if inv.status == "paid":
                continue
            due = _due_date(inv)
            if not due:
                continue
            days = (due - today).days
            if days < 0:
                overdue += 1
                g["overdue_amount"] += _inv_total(inv)
            elif 0 <= days <= 7:
                due_soon += 1
        ents.append({
            "bill_to": e, "invoice_count": len(invs),
            "outstanding": totals["outstanding_balance"],
            "credits": -totals["credits_applied"],
            "overdue_count": overdue, "due_soon_count": due_soon,
        })
        g["outstanding"] += totals["outstanding_balance"]
        g["total_invoiced"] += totals["total_invoiced"]
        g["paid"] += totals["paid"]
        g["overdue_count"] += overdue
        g["due_soon_count"] += due_soon
    g = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in g.items()}
    return {"global": g, "entities": ents}


# ── MSC agency-cost audit / reconciliation (Stage 2.6) ──
_MISSING_PO = re.compile(r"^(pending|tbd|n/?a|-)?$", re.I)
_DISPATCHED = {"dispatched", "loaded", "received", "sent"}
_SDR_BILLABLE = {"po_received", "closed"}


def _rate_map(db: Session) -> dict:
    """Baseline rate card (ship/port NULL) keyed by ADS code."""
    rows = db.query(RateCard).filter(
        RateCard.ship.is_(None), RateCard.port.is_(None), RateCard.active.is_(True)).all()
    return {r.ads_code: r for r in rows}


def _expected_from_sdr(sdr, rmap: dict) -> float:
    """Expected billable = Σ(qty × baseline rate) + fuel% if a surcharge code is present."""
    base, fuel_pct = 0.0, 0.0
    for code, qty in (sdr.qtys or {}).items():
        r = rmap.get(code)
        if not r:
            continue
        if r.percent_surcharge:
            fuel_pct = max(fuel_pct, float(r.percent_surcharge))
        else:
            base += float(qty or 0) * float(r.rate or 0)
    return round(base * (1 + fuel_pct), 2)


@router.get("/audit", dependencies=[Depends(require_roles("admin", "manager"))])
def audit(db: Session = Depends(get_db)):
    invoices = db.query(Invoice).all()
    sdrs = db.query(ShipmentDetailReport).all()
    lls = db.query(LoadingList).all()
    rmap = _rate_map(db)
    findings: list[dict] = []

    def add(sev, cat, ref, msg, amount=0.0):
        findings.append({"severity": sev, "category": cat, "ref": ref,
                         "message": msg, "amount": round(float(amount), 2)})

    inv_by_ll, inv_by_sdr = {}, {}
    for inv in invoices:
        if inv.loading_list_public_id:
            inv_by_ll.setdefault(inv.loading_list_public_id, []).append(inv)
        if inv.sdr_public_id:
            inv_by_sdr.setdefault(inv.sdr_public_id, []).append(inv)
    sdr_by_ll = {s.loading_list_public_id: s for s in sdrs if s.loading_list_public_id}

    # 1 — unbilled loading list (revenue leakage)
    for ll in lls:
        if (ll.status or "draft") in _DISPATCHED and ll.public_id not in inv_by_ll:
            sdr = sdr_by_ll.get(ll.public_id)
            add("high", "Unbilled shipment", ll.public_id,
                f"{ll.vessel or ll.public_id} was dispatched but has no invoice — likely lost revenue.",
                _expected_from_sdr(sdr, rmap) if sdr else 0.0)

    # 2 — SDR not invoiced
    for s in sdrs:
        if (s.status or "draft") in _SDR_BILLABLE and not s.invoice_public_id and s.public_id not in inv_by_sdr:
            add("high", "SDR not invoiced", s.public_id,
                f"{s.public_id} is {s.status} but has no invoice.", _expected_from_sdr(s, rmap))

    # 3 — missing PO
    for inv in invoices:
        if _MISSING_PO.match((inv.pon or "").strip()):
            add("med", "Missing PO", inv.public_id, f"{inv.public_id} has no PO number (no PO, no service).")

    # 4 — duplicate PO
    pon_map: dict[str, list] = {}
    for inv in invoices:
        p = (inv.pon or "").strip()
        if p and not _MISSING_PO.match(p):
            pon_map.setdefault(p, []).append(inv.public_id)
    for p, ids in pon_map.items():
        if len(ids) > 1:
            add("high", "Duplicate PO", p,
                f"PO {p} appears on {len(ids)} invoices ({', '.join(ids)}) — possible double-bill.")

    # 5 — rate mismatch / unknown ADS code
    for inv in invoices:
        for ln in (inv.lines or []):
            code = (ln or {}).get("code")
            if not code:
                continue
            r = rmap.get(code)
            if not r:
                add("med", "Unknown ADS code", inv.public_id, f"{inv.public_id}: {code} is not in the rate card.")
            elif not r.percent_surcharge and abs(float(ln.get("rate") or 0) - float(r.rate or 0)) > 0.01:
                billed, card = float(ln.get("rate") or 0), float(r.rate or 0)
                add("med", "Rate mismatch", inv.public_id,
                    f"{inv.public_id}: {code} billed ${billed:.2f} vs card ${card:.2f}.",
                    (billed - card) * float(ln.get("qty") or 0))

    order = {"high": 0, "med": 1, "low": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 3), f["category"]))
    return {
        "findings": findings,
        "summary": {
            "findings_count": len(findings),
            "high_severity_count": sum(1 for f in findings if f["severity"] == "high"),
            "est_revenue_at_risk": round(sum(max(f["amount"], 0) for f in findings)),
            "records_checked": {"loading_lists": len(lls), "invoices": len(invoices),
                                "sdrs": len(sdrs), "total": len(lls) + len(invoices) + len(sdrs)},
        },
    }

