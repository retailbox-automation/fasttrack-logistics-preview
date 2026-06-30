"""Reference / lookup data read API — single source of truth for dropdowns + validation."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.database import get_db
from app.models import ReferenceItem

router = APIRouter(prefix="/api/reference", tags=["reference"])

# Accept singular or plural in the path.
_ALIAS = {
    "vessel": "vessel", "vessels": "vessel",
    "port": "port", "ports": "port",
    "department": "department", "departments": "department",
    "service_code": "service_code", "service_codes": "service_code", "ads": "service_code",
}


@router.get("/{kind}", dependencies=[Depends(require_auth)])
def list_reference(kind: str, db: Session = Depends(get_db)):
    canonical = _ALIAS.get(kind.lower())
    if not canonical:
        raise HTTPException(status_code=404, detail=f"Unknown reference kind. Valid: {sorted(set(_ALIAS.values()))}")
    rows = (db.query(ReferenceItem)
            .filter(ReferenceItem.kind == canonical, ReferenceItem.active == True)
            .order_by(ReferenceItem.sort_order, ReferenceItem.name).all())
    return [{"code": r.code, "name": r.name, "meta": r.meta} for r in rows]
