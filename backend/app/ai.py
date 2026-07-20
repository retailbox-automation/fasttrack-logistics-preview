"""Claude AI layer (Stage 2.3 / 2.4 / 2.5) — messy-note normalization, email triage + draft
replies, and payment-remittance matching.

Gated on settings.anthropic_api_key: is_configured() is False until the key lands, so the whole
feature is built + testable now (endpoints report 'not enabled') and flips on the moment Andrés
sends the key — same pattern as the email ingest + SMTP. Calls the Anthropic Messages API over
HTTPS via urllib (NO SDK dependency → no rebuild risk). Haiku for high-volume classification,
Sonnet for careful extraction / drafting (per the project plan).
"""
import json
import logging
import urllib.request
import urllib.error

from app.config import settings

log = logging.getLogger("ft.ai")

_URL = "https://api.anthropic.com/v1/messages"
_VERSION = "2023-06-01"


class AIError(Exception):
    """AI call failure (unset/invalid key, rate limit, upstream error, unparseable output)."""


def is_configured() -> bool:
    return bool(settings.anthropic_api_key)


def _call(model: str, system: str, user: str, max_tokens: int | None = None) -> str:
    if not is_configured():
        raise AIError("AI is not enabled — awaiting Anthropic API key")
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens or settings.ai_max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(_URL, data=body, method="POST")
    req.add_header("x-api-key", settings.anthropic_api_key)
    req.add_header("anthropic-version", _VERSION)
    req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=settings.ai_timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        log.warning("ai_http_error status=%s detail=%s", e.code, detail)
        if e.code in (401, 403):
            raise AIError("Anthropic API key was rejected — check the key")
        if e.code == 429:
            raise AIError("AI rate limit reached — try again shortly")
        raise AIError(f"AI service error ({e.code})")
    except AIError:
        raise
    except Exception as e:
        log.warning("ai_call_failed: %s", e)
        raise AIError("Could not reach the AI service")
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()


def _json_call(model: str, system: str, user: str, max_tokens: int | None = None) -> dict:
    raw = _call(model, system, user + "\n\nReturn ONLY valid JSON — no prose, no code fences.", max_tokens)
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
        s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        log.warning("ai_bad_json: %s", raw[:200])
        raise AIError("AI returned an unparseable response")


# ── Capabilities ──

def normalize_note(note: str) -> dict:
    """Turn a messy multi-language shipment note into structured fields (Stage 2.3)."""
    system = (
        "You are a logistics operations assistant for Fast Track, which provisions MSC Cruises "
        "vessels. Normalize messy multi-language (EN/ES/IT/PT/FR) warehouse/shipment notes into "
        "structured data. Be precise and conservative; use null when unsure."
    )
    user = (
        f'Shipment note:\n"""\n{note}\n"""\n\n'
        'Extract this JSON: {"status": string, "location": string|null, '
        '"next_action": string|null, "risk": string|null, "owner": string|null, '
        '"deadline_days": number|null, "language": string, "summary": string}'
    )
    return _json_call(settings.ai_model_smart, system, user)


def summarize_email(subject: str, body: str) -> dict:
    """Triage an inbound email — summary, urgency, category, next action (Stage 2.4)."""
    system = (
        "You are a logistics customer-service assistant for Fast Track (MSC Cruises supply chain). "
        "Triage inbound emails. Languages: EN/ES/IT/PT/FR."
    )
    user = (
        f'Subject: {subject}\n\nBody:\n"""\n{body}\n"""\n\n'
        'Return this JSON: {"summary": string (1-2 sentences), '
        '"urgency": "low"|"medium"|"high", "category": string, '
        '"next_action": string, "language": string, "is_request": boolean}'
    )
    return _json_call(settings.ai_model_fast, system, user)


def draft_reply(subject: str, body: str, context: str = "") -> dict:
    """Draft a professional reply (Stage 2.4, human-in-the-loop — a person reviews + sends)."""
    system = (
        "You are a logistics customer-service agent for Fast Track (MSC Cruises supply chain). "
        "Draft a clear, professional reply in the SAME language as the incoming email. A human "
        "reviews and sends it — do not invent facts; if information is missing, ask for it politely."
    )
    ctx = f"\n\nRelevant context:\n{context}" if context else ""
    user = f'Incoming email —\nSubject: {subject}\nBody:\n"""\n{body}\n"""{ctx}\n\nWrite only the reply body.'
    return {"draft": _call(settings.ai_model_smart, system, user, max_tokens=1024)}


def match_remittance(email_body: str, open_invoices: list) -> dict:
    """Match a payment-remittance email to open invoices (Stage 2.5)."""
    system = (
        "You match MSC payment remittance advice emails to a list of open invoices. Only match "
        "invoices you are confident about; never invent invoice numbers."
    )
    inv_lines = "\n".join(f"- {i.get('number')}: {i.get('amount')}" for i in open_invoices) or "(none)"
    user = (
        f'Open invoices (number: amount):\n{inv_lines}\n\n'
        f'Remittance email:\n"""\n{email_body}\n"""\n\n'
        'Return this JSON: {"matches": [{"invoice_number": string, "amount": number, '
        '"confidence": "high"|"medium"|"low"}], "unmatched_note": string}'
    )
    return _json_call(settings.ai_model_smart, system, user)
