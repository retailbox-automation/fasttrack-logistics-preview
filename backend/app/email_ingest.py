"""Email ingestion service — shared by the /api/emails/sync endpoint and the
background auto-sync scheduler. Pulls recent messages from Microsoft Graph,
upserts into the DB (dedup by graph_id)."""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app import graph
from app.models import EmailMessage

log = logging.getLogger("ft.email_ingest")


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


def _row_from_graph(mailbox: str, g: dict) -> EmailMessage:
    fn, fa = _addr(g.get("from"))
    return EmailMessage(
        graph_id=g["id"],
        internet_message_id=g.get("internetMessageId"),
        mailbox=mailbox,
        subject=g.get("subject"),
        from_name=fn,
        from_email=fa,
        to_recipients=[{"name": n, "email": a} for n, a in (_addr(r) for r in (g.get("toRecipients") or []))],
        received_at=_parse_dt(g.get("receivedDateTime")),
        sent_at=_parse_dt(g.get("sentDateTime")),
        body_preview=g.get("bodyPreview"),
        body_content=(g.get("body") or {}).get("content"),
        importance=g.get("importance"),
        is_read=bool(g.get("isRead")),
        has_attachments=bool(g.get("hasAttachments")),
        web_link=g.get("webLink"),
        conversation_id=g.get("conversationId"),
    )


def sync_mailboxes(db: Session, boxes: list[str], top: int = 25) -> dict:
    """Pull recent messages from each mailbox into the DB. Returns per-mailbox counts."""
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
            db.add(_row_from_graph(mb, g))
            new += 1
        db.commit()
        result[mb] = {"fetched": len(msgs), "new": new}
        total_new += new
    return {"new": total_new, "mailboxes": result}
