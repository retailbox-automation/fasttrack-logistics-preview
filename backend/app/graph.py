"""Microsoft Graph client — app-only (client credentials), read-only mail.

Stdlib-only (urllib) to avoid adding an HTTP dependency. Sync, matches the
rest of the (sync) FastAPI handlers. Token is cached in-memory until ~1 min
before expiry.
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import settings

log = logging.getLogger("ft.graph")

_GRAPH = "https://graph.microsoft.com/v1.0"
_SELECT = (
    "id,internetMessageId,subject,from,toRecipients,receivedDateTime,"
    "sentDateTime,bodyPreview,body,importance,isRead,hasAttachments,webLink,conversationId"
)

_token_lock = threading.Lock()
_token_cache = {"value": None, "exp": 0.0}


class GraphError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.ms_tenant_id and settings.ms_client_id and settings.ms_client_secret)


def _http(method: str, url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 30):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8") or "{}"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise GraphError(f"HTTP {e.code}: {body[:300]}")
    except Exception as e:
        raise GraphError(f"{type(e).__name__}: {e}")


def get_token() -> str:
    now = time.time()
    with _token_lock:
        if _token_cache["value"] and _token_cache["exp"] - 60 > now:
            return _token_cache["value"]
    if not is_configured():
        raise GraphError("Microsoft Graph not configured (MS_* env vars missing)")
    url = f"https://login.microsoftonline.com/{settings.ms_tenant_id}/oauth2/v2.0/token"
    body = urllib.parse.urlencode({
        "client_id": settings.ms_client_id,
        "client_secret": settings.ms_client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }).encode("utf-8")
    _, payload = _http("POST", url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    tok = payload.get("access_token")
    if not tok:
        raise GraphError(f"token error: {payload.get('error_description') or payload.get('error') or payload}")
    with _token_lock:
        _token_cache["value"] = tok
        _token_cache["exp"] = now + int(payload.get("expires_in", 3600))
    log.info("graph_token_ok")
    return tok


def fetch_messages(mailbox: str, top: int = 25) -> list[dict]:
    """Most recent `top` messages from a mailbox (newest first)."""
    token = get_token()
    qs = urllib.parse.urlencode({
        "$top": top,
        "$select": _SELECT,
        "$orderby": "receivedDateTime desc",
    })
    url = f"{_GRAPH}/users/{urllib.parse.quote(mailbox)}/messages?{qs}"
    # Prefer plain-text bodies — safe to display as text, no HTML/XSS handling needed.
    headers = {"Authorization": f"Bearer {token}", "Prefer": 'outlook.body-content-type="text"'}
    _, payload = _http("GET", url, headers=headers)
    return payload.get("value", [])
