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
from app.models import Invoice, CreditMemo
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
