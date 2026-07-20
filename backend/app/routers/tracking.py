"""Live truck tracking (client priority, Jul 2).

Tracks dispatched loads' current location + status. Manual location source now (ops update
via PATCH); Apple AirTag / GPS is a pluggable source layered on later (pending Andrés's
decision on the mechanism). `located_at` is stamped server-side on each update.

GET  /api/tracking          → loads in transit (dispatched/loaded) or with a tracking status
PATCH /api/tracking/{ll_id} → update current_location / tracking_status / tracking_note
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import LoadingList
from app.schemas import TrackingUpdate, TrackingOut, TRACKING_STATES
from app.auth import require_auth, require_roles
from app.audit import log_audit
from app.events import broadcast

router = APIRouter(prefix="/api/tracking", tags=["tracking"])

_IN_TRANSIT = ("dispatched", "loaded")


@router.get("", response_model=list[TrackingOut], dependencies=[Depends(require_auth)])
def list_tracking(db: Session = Depends(get_db)):
    q = db.query(LoadingList).filter(
        or_(LoadingList.status.in_(_IN_TRANSIT), LoadingList.tracking_status.isnot(None)))
    return q.order_by(LoadingList.id.desc()).all()


@router.patch("/{ll_id}", response_model=TrackingOut)
def update_tracking(ll_id: int, payload: TrackingUpdate, request: Request, db: Session = Depends(get_db),
                    claims: dict = Depends(require_roles("admin", "manager", "ops"))):
    ll = db.get(LoadingList, ll_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Loading list not found")
    data = payload.model_dump(exclude_unset=True)
    ts = data.get("tracking_status")
    if ts is not None and ts not in TRACKING_STATES:
        raise HTTPException(status_code=400, detail=f"tracking_status must be one of {sorted(TRACKING_STATES)}")
    for k, v in data.items():
        setattr(ll, k, v)
    ll.located_at = datetime.utcnow()
    db.commit()
    db.refresh(ll)
    log_audit(db, claims, "update", "truck_tracking", entity_id=str(ll.id),
              summary=f"Tracking {ll.public_id}: {ll.tracking_status or '—'} @ {ll.current_location or '—'}",
              ip=request.client.host if request.client else None)
    broadcast("tracking.changed", {"action": "update", "id": ll.id,
                                   "by_user_id": claims.get("user_id"), "by_name": claims.get("name")})
    return ll
