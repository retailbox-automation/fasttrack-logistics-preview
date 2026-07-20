"""Live truck tracking (client priority, Jul 2).

Tracks dispatched loads' current location + status. Two location sources:
- manual (ops updates via PATCH), and
- **driver-mode GPS**: a dispatcher generates a per-load driver link (token, no login); the
  driver opens it on a phone and it streams geolocation to `/driver/{id}/ping`, building a
  route trail (`tracking_pings`) and updating the load's live position. `located_at` is
  stamped server-side on each update.

GET   /api/tracking                    → loads in transit (dispatched/loaded) or being tracked
PATCH /api/tracking/{ll_id}            → manual current_location / tracking_status / note
POST  /api/tracking/{ll_id}/driver-link→ (admin/manager) mint a driver share link
GET   /api/tracking/driver/{ll_id}     → (pre-auth, token) minimal load info for the driver page
POST  /api/tracking/driver/{ll_id}/ping→ (pre-auth, token) driver streams a GPS fix
GET   /api/tracking/{ll_id}/trail      → (authed) recent GPS trail for the map
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import LoadingList, TrackingPing
from app.schemas import (TrackingUpdate, TrackingOut, TRACKING_STATES,
                         DriverLinkOut, DriverBootstrapOut, DriverPingIn, TrailPoint)
from app.auth import require_auth, require_roles, limiter
from app.audit import log_audit
from app.events import broadcast

router = APIRouter(prefix="/api/tracking", tags=["tracking"])

_IN_TRANSIT = ("dispatched", "loaded")
DRIVER_TOKEN_TTL_MIN = 48 * 60  # a driver link is good for the trip (2 days)


def _hash_token(token: str) -> str:
    # Driver token is 256-bit random → a fast SHA-256 digest is safe (no bcrypt cost per ping).
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _valid_driver(ll: LoadingList, token: str) -> bool:
    if not ll or not ll.driver_token_hash or not ll.driver_token_expires:
        return False
    if ll.driver_token_expires < datetime.utcnow():
        return False
    return secrets.compare_digest(ll.driver_token_hash, _hash_token(token or ""))


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


# ── Driver-mode GPS ──────────────────────────────────────────────────────────

@router.post("/{ll_id}/driver-link", response_model=DriverLinkOut)
def create_driver_link(ll_id: int, request: Request, db: Session = Depends(get_db),
                       claims: dict = Depends(require_roles("admin", "manager"))):
    """Mint a one-per-load driver link. Returns the URL to hand to the driver (copy / WhatsApp);
    opening it streams that phone's GPS to this load. Generating a new link invalidates the old."""
    ll = db.get(LoadingList, ll_id)
    if not ll:
        raise HTTPException(status_code=404, detail="Loading list not found")
    token = secrets.token_urlsafe(32)
    ll.driver_token_hash = _hash_token(token)
    ll.driver_token_expires = datetime.utcnow() + timedelta(minutes=DRIVER_TOKEN_TTL_MIN)
    db.commit()
    driver_url = f"{settings.app_base_url}/?driver={token}&ll={ll.id}"
    log_audit(db, claims, "driver-link", "truck_tracking", entity_id=str(ll.id),
              summary=f"Driver GPS link generated for {ll.public_id}",
              ip=request.client.host if request.client else None)
    return DriverLinkOut(loading_list_id=ll.id, public_id=ll.public_id,
                         driver_url=driver_url, expires_in_minutes=DRIVER_TOKEN_TTL_MIN)


@router.get("/driver/{ll_id}", response_model=DriverBootstrapOut)
def driver_bootstrap(ll_id: int, token: str, db: Session = Depends(get_db)):
    """Pre-auth: the driver page loads its own load info by token (no login)."""
    ll = db.get(LoadingList, ll_id)
    if not _valid_driver(ll, token):
        raise HTTPException(status_code=401, detail="Invalid or expired driver link")
    return DriverBootstrapOut(loading_list_id=ll.id, public_id=ll.public_id, vessel=ll.vessel,
                              port=ll.port, destination=ll.delivery_address,
                              tracking_status=ll.tracking_status)


@router.post("/driver/{ll_id}/ping", status_code=204)
@limiter.limit("40/minute")
def driver_ping(ll_id: int, payload: DriverPingIn, request: Request, db: Session = Depends(get_db)):
    """Pre-auth: a driver's phone streams a GPS fix (token in body). Appends to the trail and
    updates the load's live position; flips a staged/idle load to en_route on first fix."""
    ll = db.get(LoadingList, ll_id)
    if not _valid_driver(ll, payload.token):
        raise HTTPException(status_code=401, detail="Invalid or expired driver link")
    if not (-90.0 <= payload.lat <= 90.0 and -180.0 <= payload.lng <= 180.0):
        raise HTTPException(status_code=400, detail="Invalid coordinates")
    db.add(TrackingPing(loading_list_id=ll.id, lat=payload.lat, lng=payload.lng,
                        accuracy=payload.accuracy, speed=payload.speed, heading=payload.heading))
    ll.current_lat = payload.lat
    ll.current_lng = payload.lng
    ll.current_location = f"{payload.lat:.5f}, {payload.lng:.5f}"
    ll.located_at = datetime.utcnow()
    if ll.tracking_status in (None, "staged"):
        ll.tracking_status = "en_route"
    db.commit()
    broadcast("tracking.changed", {"action": "ping", "id": ll.id,
                                   "lat": payload.lat, "lng": payload.lng})


@router.get("/{ll_id}/trail", response_model=list[TrailPoint], dependencies=[Depends(require_auth)])
def get_trail(ll_id: int, db: Session = Depends(get_db), limit: int = 300):
    """Recent GPS trail for the map, oldest→newest."""
    pings = (db.query(TrackingPing)
             .filter(TrackingPing.loading_list_id == ll_id)
             .order_by(TrackingPing.recorded_at.desc())
             .limit(min(max(limit, 1), 1000)).all())
    return list(reversed(pings))
