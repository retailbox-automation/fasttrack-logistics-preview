"""Weekly operations report — server-side aggregation (Stage 4.1).

Authoritative weekly ops summary aggregated from typed data for a Mon-Sun window:
dispatches/trucking, warehouse receipts, billing, customs, staff hours, and
exceptions/pending. This is the foundation for auto-generated / emailed weekly
reports; the approval cycle is already wired separately (/api/approvals).

GET /api/reports/weekly?start=YYYY-MM-DD  (start snaps to that week's Monday;
defaults to the current week). Manager/admin.
"""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import graph
from app.config import settings
from app.database import get_db
from app.email_send import send_email
from app.models import (LoadingList, WarehouseReceipt, Invoice, CreditMemo, CustomsRecord,
                        TimeEntry, AuditLog, InventoryItem, EmailMessage, DiscrepancyReport,
                        ReportSnapshot)
from app.auth import require_roles, require_auth

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
    return _compute_weekly(db, wk_start)


def _compute_weekly(db: Session, wk_start: date) -> dict:
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


@router.get("/kpis", dependencies=[Depends(require_roles("admin", "manager"))])
def kpis(db: Session = Depends(get_db)):
    """Operations KPIs snapshot (all-time): staff hours + activity, throughput, audit coverage."""
    hours_by_user: dict[str, int] = {}
    tes = db.query(TimeEntry).all()
    for t in tes:
        m = int(((t.clock_out_at or t.clock_in_at) - t.clock_in_at).total_seconds() // 60)
        hours_by_user[t.user_name] = hours_by_user.get(t.user_name, 0) + m

    audits = db.query(AuditLog).all()
    actions_by_user: dict[str, int] = {}
    by_action: dict[str, int] = {}
    by_entity: dict[str, int] = {}
    creates_by_entity: dict[str, int] = {}
    for a in audits:
        u = a.user_name or "—"
        actions_by_user[u] = actions_by_user.get(u, 0) + 1
        by_action[a.action] = by_action.get(a.action, 0) + 1
        by_entity[a.entity_kind] = by_entity.get(a.entity_kind, 0) + 1
        if a.action == "create":
            creates_by_entity[a.entity_kind] = creates_by_entity.get(a.entity_kind, 0) + 1

    staff_names = sorted(set(hours_by_user) | set(actions_by_user) - {"—"})
    staff = [{"user": u, "hours": round(hours_by_user.get(u, 0) / 60, 1),
              "actions": actions_by_user.get(u, 0)} for u in staff_names]

    return {
        "staff": staff,
        "throughput": {"creates_by_entity": creates_by_entity},
        "audit": {"total_actions": len(audits), "by_action": by_action,
                  "by_entity": by_entity, "active_users": len([u for u in actions_by_user if u != "—"])},
        "time": {"total_hours": round(sum(hours_by_user.values()) / 60, 1), "entries": len(tes)},
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


_AGING_WARN, _AGING_CRIT = 30, 60  # days in stock
_REQUIRED_FIELDS = ("department", "vessel", "po_number")


@router.get("/inventory-alerts", dependencies=[Depends(require_auth)])
def inventory_alerts(db: Session = Depends(get_db)):
    """Inventory exception alerts: aging stock (storage/billing risk) + missing-data (data quality)."""
    today = date.today()
    items = db.query(InventoryItem).filter(InventoryItem.status == "in_stock").all()
    alerts = []
    for it in items:
        ref = it.warehouse_receipt or it.part_number or f"item-{it.id}"
        if it.received_date:
            age = (today - it.received_date).days
            if age >= _AGING_CRIT:
                alerts.append({"severity": "high", "category": "Aging stock", "ref": ref, "age_days": age,
                               "message": f"{it.part_number} in stock {age}d (WR {it.warehouse_receipt}) — storage/billing risk"})
            elif age >= _AGING_WARN:
                alerts.append({"severity": "med", "category": "Aging stock", "ref": ref, "age_days": age,
                               "message": f"{it.part_number} in stock {age}d — approaching storage threshold"})
        missing = [f for f in _REQUIRED_FIELDS if not getattr(it, f, None)]
        if missing:
            alerts.append({"severity": "med", "category": "Missing data", "ref": ref, "age_days": None,
                           "message": f"{it.part_number}: missing {', '.join(missing)}"})
    order = {"high": 0, "med": 1, "low": 2}
    alerts.sort(key=lambda a: (order.get(a["severity"], 3), -(a["age_days"] or 0)))
    return {
        "alerts": alerts,
        "summary": {
            "total": len(alerts),
            "high": sum(1 for a in alerts if a["severity"] == "high"),
            "aging": sum(1 for a in alerts if a["category"] == "Aging stock"),
            "missing_data": sum(1 for a in alerts if a["category"] == "Missing data"),
            "in_stock_items": len(items),
        },
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/monitoring", dependencies=[Depends(require_roles("admin", "manager"))])
def monitoring(db: Session = Depends(get_db)):
    """System health + integration status + audit coverage + operational exception signals (4.2).

    A single 'is everything healthy' snapshot: are the integrations flowing, is the audit trail
    being written, and what operational problems (open customs, rejected invoices, open
    discrepancies, aging stock) need attention right now."""
    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)

    # ── Integrations ──
    newest_email = db.query(func.max(EmailMessage.received_at)).scalar()
    email_age = int((now - newest_email).total_seconds() // 60) if newest_email else None
    emails_24h = db.query(EmailMessage).filter(EmailMessage.received_at >= day_ago).count()
    if not graph.is_configured():
        email_state = "not_configured"
    elif email_age is None:
        email_state = "idle"
    elif email_age < 60:
        email_state = "ok"
    else:
        email_state = "stale"
    integrations = {
        "email_ingest": {"configured": graph.is_configured(), "newest_minutes": email_age,
                         "last_24h": emails_24h, "state": email_state},
        "database": {"state": "up"},
    }

    # ── Audit coverage ──
    total_audit = db.query(AuditLog).count()
    audit_24h_rows = db.query(AuditLog).filter(AuditLog.created_at >= day_ago).all()
    by_action_24h: dict[str, int] = {}
    users_24h = set()
    for a in audit_24h_rows:
        by_action_24h[a.action] = by_action_24h.get(a.action, 0) + 1
        if a.user_name:
            users_24h.add(a.user_name)
    recent = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(15).all()
    audit = {
        "total": total_audit, "last_24h": len(audit_24h_rows), "active_users_24h": len(users_24h),
        "by_action_24h": by_action_24h,
        "recent": [{"at": a.created_at.isoformat() + "Z", "user": a.user_name or "—",
                    "action": a.action, "entity": a.entity_kind, "entity_id": a.entity_id,
                    "summary": a.summary} for a in recent],
    }

    # ── Operational exception signals ──
    exceptions = []

    def _exc(sev, category, count, message):
        if count:
            exceptions.append({"severity": sev, "category": category, "count": count, "message": message})

    open_cust = db.query(CustomsRecord).filter(CustomsRecord.status.in_(["open", "hold"])).count()
    _exc("high", "Customs", open_cust, f"{open_cust} customs record(s) open or on hold — may block dispatch")
    rejected_inv = db.query(Invoice).filter(Invoice.status == "rejected").count()
    _exc("high", "Billing", rejected_inv, f"{rejected_inv} invoice(s) rejected — need correction")
    pending_inv = db.query(Invoice).filter(Invoice.status.in_(["draft", "pending_approval"])).count()
    _exc("med", "Billing", pending_inv, f"{pending_inv} invoice(s) awaiting approval/send")
    open_disc = db.query(DiscrepancyReport).filter(DiscrepancyReport.status == "open").count()
    _exc("med", "Discrepancies", open_disc, f"{open_disc} discrepancy report(s) still open")
    aging = sum(1 for it in db.query(InventoryItem).filter(InventoryItem.status == "in_stock").all()
                if it.received_date and (date.today() - it.received_date).days >= _AGING_CRIT)
    _exc("med", "Inventory", aging, f"{aging} item(s) aging in stock ≥ {_AGING_CRIT}d")
    if email_state == "stale":
        exceptions.append({"severity": "med", "category": "Integration", "count": 1,
                           "message": f"Email ingest last updated {email_age} min ago"})
    order = {"high": 0, "med": 1, "low": 2}
    exceptions.sort(key=lambda e: order.get(e["severity"], 3))

    # Schema/migration revision (Alembic) — None until the DB is stamped/tracked
    schema_rev = None
    try:
        from sqlalchemy import text as _text
        schema_rev = db.execute(_text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        schema_rev = None

    return {
        "system": {"version": settings.api_version, "schema_revision": schema_rev,
                   "server_time": now.isoformat() + "Z"},
        "integrations": integrations,
        "audit": audit,
        "exceptions": exceptions,
        "exception_count": len(exceptions),
        "generated_at": now.isoformat() + "Z",
    }


# ── Weekly report auto-generation + snapshots (Stage 4.1) ──

def _weekly_email_text(p: dict) -> str:
    wk = p.get("week", {}) or {}
    d = p.get("dispatches", {}) or {}
    r = p.get("receipts", {}) or {}
    b = p.get("billing", {}) or {}
    ex = p.get("exceptions", {}) or {}
    return (
        "Fast Track — Weekly Operations Report\n"
        f"Week {wk.get('start')} to {wk.get('end')}\n\n"
        f"Dispatches: {d.get('loads', 0)} loads · {d.get('vessels', 0)} vessels · {d.get('trucks', 0)} trucks\n"
        f"Warehouse receipts: {r.get('count', 0)} ({r.get('pieces', 0)} pcs)\n"
        f"Invoiced: ${b.get('invoiced_total', 0):,.2f} across {b.get('invoices', 0)} invoice(s)\n"
        f"Open items: {ex.get('invoices_pending', 0)} invoice(s) pending · "
        f"{ex.get('customs_open_or_hold', 0)} customs open/hold\n"
    )


def generate_weekly_snapshot(db: Session, wk_start: date | None = None, generated_by: str = "scheduler") -> ReportSnapshot:
    """Compute the weekly report, persist it as a ReportSnapshot (deduped per week), and email
    it if recipients are configured (logs when SMTP is unset). Idempotent per (weekly, period_start)."""
    wk_start = _monday(wk_start or date.today())
    wk_end = wk_start + timedelta(days=6)
    existing = (db.query(ReportSnapshot)
                .filter(ReportSnapshot.kind == "weekly", ReportSnapshot.period_start == wk_start).first())
    if existing:
        return existing
    payload = _compute_weekly(db, wk_start)
    recipients = settings.weekly_report_recipient_list
    delivery = "none"
    if recipients:
        delivery = send_email(", ".join(recipients),
                              f"Fast Track — weekly ops report ({wk_start.isoformat()})",
                              _weekly_email_text(payload))
    snap = ReportSnapshot(kind="weekly", period_start=wk_start, period_end=wk_end, payload=payload,
                          delivery=delivery, recipients=(", ".join(recipients) if recipients else None),
                          generated_by=generated_by)
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def _snap_out(s: ReportSnapshot, include_payload: bool = False) -> dict:
    out = {"id": s.id, "kind": s.kind,
           "period_start": s.period_start.isoformat() if s.period_start else None,
           "period_end": s.period_end.isoformat() if s.period_end else None,
           "delivery": s.delivery, "recipients": s.recipients, "generated_by": s.generated_by,
           "generated_at": s.generated_at.isoformat() + "Z"}
    if include_payload:
        out["payload"] = s.payload
    return out


@router.post("/weekly/generate")
def generate_weekly(start: str | None = Query(default=None), db: Session = Depends(get_db),
                    claims: dict = Depends(require_roles("admin", "manager"))):
    """Generate + save (and email, if recipients set) a weekly snapshot. Defaults to LAST
    complete week. Idempotent — returns the existing snapshot if that week is already saved."""
    try:
        wk_start = _monday(date.fromisoformat(start)) if start else _monday(date.today() - timedelta(days=7))
    except (ValueError, TypeError):
        wk_start = _monday(date.today() - timedelta(days=7))
    snap = generate_weekly_snapshot(db, wk_start, generated_by=(claims.get("name") or "manual"))
    return _snap_out(snap, include_payload=True)


@router.get("/snapshots", dependencies=[Depends(require_roles("admin", "manager"))])
def snapshots(kind: str = Query(default="weekly"), limit: int = 20, db: Session = Depends(get_db)):
    rows = (db.query(ReportSnapshot).filter(ReportSnapshot.kind == kind)
            .order_by(ReportSnapshot.generated_at.desc()).limit(min(max(limit, 1), 100)).all())
    return [_snap_out(s) for s in rows]


@router.get("/snapshots/{snap_id}", dependencies=[Depends(require_roles("admin", "manager"))])
def snapshot_detail(snap_id: int, db: Session = Depends(get_db)):
    s = db.get(ReportSnapshot, snap_id)
    if not s:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return _snap_out(s, include_payload=True)


@router.delete("/snapshots/{snap_id}", status_code=204, dependencies=[Depends(require_roles("admin"))])
def delete_snapshot(snap_id: int, db: Session = Depends(get_db)):
    s = db.get(ReportSnapshot, snap_id)
    if not s:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    db.delete(s)
    db.commit()
