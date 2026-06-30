"""Email ingestion + listing (Microsoft Graph, read-only).

Slice 1: pull recent messages from the configured FT mailboxes into Postgres,
list/read them via the API. AI classification + draft replies come in Slice 2.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import graph, email_ingest
from app.audit import log_audit
from app.auth import require_auth, require_roles
from app.config import settings
from app.database import get_db
from app.events import broadcast
from app.models import EmailMessage
from app.schemas import EmailMessageOut

router = APIRouter(prefix="/api/emails", tags=["emails"])


def _visible_mailboxes(claims: dict) -> list[str] | None:
    """Mailboxes the caller may see. None = all (admin). Otherwise an explicit allow-list.

    Rule (per Andrés, 2026-06-30): admins see every mailbox; every other role
    sees only their own mailbox (the one whose address == their login email).
    """
    if claims.get("role") == "admin":
        return None
    own = (claims.get("email") or "").lower()
    return [own] if own else []


@router.get("/_mailboxes", response_model=dict, dependencies=[Depends(require_auth)])
def allowed_mailboxes(claims: dict = Depends(require_auth)):
    """Mailbox addresses the current user is allowed to view (drives the UI switcher)."""
    allowed = _visible_mailboxes(claims)
    boxes = settings.graph_mailbox_list
    if allowed is None:
        return {"role": claims.get("role"), "all": True, "mailboxes": boxes}
    return {"role": claims.get("role"), "all": False,
            "mailboxes": [b for b in boxes if b.lower() in allowed]}


@router.get("", response_model=list[EmailMessageOut])
def list_emails(
    mailbox: str | None = None,
    q: str | None = None,
    unread: bool | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    claims: dict = Depends(require_auth),
):
    query = db.query(EmailMessage)
    # Visibility scoping: non-admins are locked to their own mailbox regardless
    # of the requested `mailbox` param.
    allowed = _visible_mailboxes(claims)
    if allowed is not None:
        # func.lower for case-insensitive match against the stored mailbox
        query = query.filter(EmailMessage.mailbox.in_(allowed))
    elif mailbox:
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


@router.get("/{email_id}", response_model=EmailMessageOut)
def get_email(email_id: int, db: Session = Depends(get_db), claims: dict = Depends(require_auth)):
    m = db.get(EmailMessage, email_id)
    if not m:
        raise HTTPException(status_code=404, detail="Email not found")
    allowed = _visible_mailboxes(claims)
    if allowed is not None and (m.mailbox or "").lower() not in allowed:
        # Hide existence from users outside this mailbox
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

    summary = email_ingest.sync_mailboxes(db, boxes, top)
    log_audit(db, claims, "sync", "email_message",
              summary=f"Synced emails: {summary['new']} new across {len(boxes)} mailbox(es)",
              payload=summary["mailboxes"],
              ip=request.client.host if request.client else None)
    broadcast("emails.changed", {"action": "sync", "new": summary["new"], "by_name": claims.get("name")})
    return summary
