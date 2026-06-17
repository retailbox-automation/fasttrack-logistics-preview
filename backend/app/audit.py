"""Audit-log helper. Called by routers after every mutation."""
from typing import Optional
from sqlalchemy.orm import Session
from app.models import AuditLog


def log_audit(
    db: Session,
    claims: dict,
    action: str,
    entity_kind: str,
    entity_id: Optional[str] = None,
    summary: Optional[str] = None,
    payload: Optional[dict] = None,
    ip: Optional[str] = None,
    commit: bool = True,
):
    """Append-only — never raises on error (audit must not break the request)."""
    try:
        entry = AuditLog(
            user_id=claims.get("user_id"),
            user_name=claims.get("name"),
            user_role=claims.get("role"),
            action=action,
            entity_kind=entity_kind,
            entity_id=str(entity_id) if entity_id is not None else None,
            summary=summary,
            payload=payload,
            ip=ip,
        )
        db.add(entry)
        if commit:
            db.commit()
    except Exception:
        # Audit failure must not break the user's mutation
        db.rollback()
