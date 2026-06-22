"""Email ingestion + listing (Microsoft Graph, read-only).

Slice 1: pull recent messages from the configured FT mailboxes into Postgres,
list/read them via the API. AI classification + draft replies come in Slice 2.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import graph
from app.audit import log_audit
from app.auth import require_auth, require_roles
from app.config import settings
from app.database import get_db
from app.events import broadcast
from app.models import EmailMessage
from app.schemas import EmailMessageOut

router = APIRouter(prefix="/api/emails", tags=["emails"])


def _addr(d: dict | None):
    ea = (d or {}).get("emailAddress", {}) or {}
    return ea.get("name"), ea.get("address")


def _parse_dt(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


@router.get("", response_model=list[EmailMessageOut], dependencies=[Depends(require_auth)])
def list_emails(
    mailbox: str | None = None,
    q: str | None = None,
    unread: bool | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    query = db.query(EmailMessage)
    if mailbox:
        query = query.filter(EmailMessage.mailbox == mailbox)
    if unread is not None:
        query = query.filter(EmailMessage.is_read == (not unread))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            EmailMessage.subject.ilike(like),
            EmailMessage.from_email.ilike(like),
            EmailMessage.from_name.ilike(like),
            EmailMessage.body_preview.ilike(like),
        ))
    return (query.order_by(EmailMessage.received_at.desc().nullslast())
            .offset(offset).limit(limit).all())


@router.get("/{email_id}", response_model=EmailMessageOut, dependencies=[Depends(require_auth)])
def get_email(email_id: int, db: Session = Depends(get_db)):
    m = db.get(EmailMessage, email_id)
    if not m:
        raise HTTPException(status_code=404, detail="Email not found")
    return m


@router.post("/sync", response_model=dict)
def sync_emails(
    request: Request,
    mailbox: str | None = None,
    top: int = Query(25, le=100),
    db: Session = Depends(get_db),
    claims: dict = Depends(require_roles("admin", "manager", "ops")),
):
    """Pull recent messages from Graph into the DB. Upsert by graph_id (dedup)."""
    if not graph.is_configured():
        raise HTTPException(status_code=503, detail="Microsoft Graph not configured (MS_* env vars missing)")
    boxes = [mailbox] if mailbox else settings.graph_mailbox_list
    if not boxes:
        raise HTTPException(status_code=400, detail="No mailboxes configured (set MS_GRAPH_MAILBOXES)")

    result: dict = {}
    total_new = 0
    for mb in boxes:
        try:
            msgs = graph.fetch_messages(mb, top=top)
        except graph.GraphError as e:
            result[mb] = {"error": str(e)[:200]}
            continue
        new = 0
        for g in msgs:
            gid = g.get("id")
            if not gid:
                continue
            if db.query(EmailMessage.id).filter(EmailMessage.graph_id == gid).first():
                continue
            fn, fa = _addr(g.get("from"))
            db.add(EmailMessage(
                graph_id=gid,
                internet_message_id=g.get("internetMessageId"),
                mailbox=mb,
                subject=g.get("subject"),
                from_name=fn,
                from_email=fa,
                to_recipients=[{"name": n, "email": a} for n, a in (_addr(r) for r in (g.get("toRecipients") or []))],
                received_at=_parse_dt(g.get("receivedDateTime")),
                sent_at=_parse_dt(g.get("sentDateTime")),
                body_preview=g.get("bodyPreview"),
                importance=g.get("importance"),
                is_read=bool(g.get("isRead")),
                has_attachments=bool(g.get("hasAttachments")),
                web_link=g.get("webLink"),
                conversation_id=g.get("conversationId"),
            ))
            new += 1
        db.commit()
        result[mb] = {"fetched": len(msgs), "new": new}
        total_new += new

    log_audit(db, claims, "sync", "email_message",
              summary=f"Synced emails: {total_new} new across {len(boxes)} mailbox(es)",
              payload=result,
              ip=request.client.host if request.client else None)
    broadcast("emails.changed", {"action": "sync", "new": total_new, "by_name": claims.get("name")})
    return {"new": total_new, "mailboxes": result}
