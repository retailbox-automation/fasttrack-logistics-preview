"""AI endpoints (Stage 2.3 / 2.4 / 2.5) — thin wrappers over app.ai, gated on the Anthropic key.

When the key is unset every capability returns 503 ("not enabled yet") so the frontend degrades
gracefully; on an AI failure it returns 502 with a clear message. GET /status lets the UI show
the right state without triggering a call.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import ai
from app.database import get_db
from app.models import Invoice
from app.auth import require_auth, require_roles

router = APIRouter(prefix="/api/ai", tags=["ai"])

_OPS = ("admin", "manager", "ops")


class NoteIn(BaseModel):
    note: str


class EmailIn(BaseModel):
    subject: str = ""
    body: str


class DraftIn(BaseModel):
    subject: str = ""
    body: str
    context: str = ""


class RemittanceIn(BaseModel):
    email_body: str


def _guard():
    if not ai.is_configured():
        raise HTTPException(status_code=503, detail="AI is not enabled yet — awaiting the Anthropic API key")


def _run(fn, *args):
    _guard()
    try:
        return fn(*args)
    except ai.AIError as e:
        raise HTTPException(status_code=502, detail=str(e))


def _inv_amount(inv: Invoice) -> float:
    sub = sum(float((l or {}).get("qty") or 0) * float((l or {}).get("rate") or 0) for l in (inv.lines or []))
    return round(sub * (1 + float(inv.fuel or 0) / 100.0), 2)


@router.get("/status")
def status(claims: dict = Depends(require_auth)):
    return {"configured": ai.is_configured()}


@router.post("/normalize-note")
def normalize_note(payload: NoteIn, claims: dict = Depends(require_roles(*_OPS))):
    return _run(ai.normalize_note, payload.note)


@router.post("/summarize-email")
def summarize_email(payload: EmailIn, claims: dict = Depends(require_roles(*_OPS))):
    return _run(ai.summarize_email, payload.subject, payload.body)


@router.post("/draft-reply")
def draft_reply(payload: DraftIn, claims: dict = Depends(require_roles(*_OPS))):
    return _run(ai.draft_reply, payload.subject, payload.body, payload.context)


@router.post("/match-remittance")
def match_remittance(payload: RemittanceIn, db: Session = Depends(get_db),
                     claims: dict = Depends(require_roles("admin", "manager"))):
    _guard()
    open_invs = db.query(Invoice).filter(Invoice.status.notin_(["paid", "draft", "rejected"])).all()
    inv_list = [{"number": i.public_id, "amount": _inv_amount(i)} for i in open_invs]
    try:
        return ai.match_remittance(payload.email_body, inv_list)
    except ai.AIError as e:
        raise HTTPException(status_code=502, detail=str(e))
