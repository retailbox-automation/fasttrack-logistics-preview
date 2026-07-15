"""Rate matrix (Stage 2.2) — /api/rates CRUD + lookup resolver.

Lookup returns the most specific active, currently-effective row for (ads_code, ship, port):
exact ship+port > ship-only (port NULL) > port-only (ship NULL) > baseline (both NULL).
This is what the invoice builder will call to auto-fill line rates once MSC's ship×port
rate sheet is loaded (baseline rows already give today's flat rates).
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import RateCard
from app.schemas import RateCardCreate, RateCardUpdate, RateCardOut, RateLookupOut, RATE_OPS
from app.auth import require_auth, require_roles
from app.audit import log_audit

router = APIRouter(prefix="/api/rates", tags=["rates"])


def _validate_op(op) -> None:
    if op is not None and op not in RATE_OPS:
        raise HTTPException(status_code=400, detail=f"op must be one of {sorted(RATE_OPS)}")


def _effective(r: RateCard, today: date) -> bool:
    if r.effective_from and r.effective_from > today:
        return False
    if r.effective_to and r.effective_to < today:
        return False
    return True


def _resolve(db: Session, ads_code: str, ship: str | None, port: str | None):
    today = date.today()
    rows = [r for r in db.query(RateCard).filter(
        RateCard.ads_code == ads_code, RateCard.active.is_(True)).all() if _effective(r, today)]
    best, best_rank, best_match = None, 0, "none"
    for r in rows:
        if ship and port and r.ship == ship and r.port == port:
            rank, m = 4, "exact"
        elif ship and r.ship == ship and r.port is None:
            rank, m = 3, "ship"
        elif port and r.port == port and r.ship is None:
            rank, m = 2, "port"
        elif r.ship is None and r.port is None:
            rank, m = 1, "baseline"
        else:
            continue
        if rank > best_rank:
            best, best_rank, best_match = r, rank, m
    return best, best_match


# NB: /lookup is declared before /{rate_id} so it isn't captured by the dynamic route.
@router.get("/lookup", response_model=RateLookupOut, dependencies=[Depends(require_auth)])
def lookup(ads_code: str, ship: str | None = None, port: str | None = None, db: Session = Depends(get_db)):
    r, m = _resolve(db, ads_code, ship, port)
    if not r:
        return RateLookupOut(ads_code=ads_code, ship=ship, port=port, found=False, match="none")
    return RateLookupOut(ads_code=ads_code, ship=ship, port=port, found=True, rate=r.rate,
                         op=r.op, percent_surcharge=r.percent_surcharge, match=m, rate_card_id=r.id)


@router.get("", response_model=list[RateCardOut], dependencies=[Depends(require_auth)])
def list_rates(ads_code: str | None = None, ship: str | None = None, port: str | None = None,
               active: bool | None = None, db: Session = Depends(get_db)):
    q = db.query(RateCard)
    if ads_code:
        q = q.filter(RateCard.ads_code == ads_code)
    if ship:
        q = q.filter(RateCard.ship == ship)
    if port:
        q = q.filter(RateCard.port == port)
    if active is not None:
        q = q.filter(RateCard.active.is_(active))
    return q.order_by(RateCard.ads_code, RateCard.ship, RateCard.port).all()


@router.get("/{rate_id}", response_model=RateCardOut, dependencies=[Depends(require_auth)])
def get_rate(rate_id: int, db: Session = Depends(get_db)):
    r = db.get(RateCard, rate_id)
    if not r:
        raise HTTPException(status_code=404, detail="Rate not found")
    return r


@router.post("", response_model=RateCardOut)
def create_rate(payload: RateCardCreate, request: Request, db: Session = Depends(get_db),
                claims: dict = Depends(require_roles("admin", "manager"))):
    _validate_op(payload.op)
    r = RateCard(**payload.model_dump())
    db.add(r)
    db.commit()
    db.refresh(r)
    log_audit(db, claims, "create", "rate_card", entity_id=str(r.id),
              summary=f"Rate {r.ads_code} {r.ship or '*'}/{r.port or '*'} = {r.rate}",
              ip=request.client.host if request.client else None)
    return r


@router.patch("/{rate_id}", response_model=RateCardOut)
def update_rate(rate_id: int, payload: RateCardUpdate, request: Request, db: Session = Depends(get_db),
                claims: dict = Depends(require_roles("admin", "manager"))):
    r = db.get(RateCard, rate_id)
    if not r:
        raise HTTPException(status_code=404, detail="Rate not found")
    data = payload.model_dump(exclude_unset=True)
    _validate_op(data.get("op"))
    for k, v in data.items():
        setattr(r, k, v)
    db.commit()
    db.refresh(r)
    log_audit(db, claims, "update", "rate_card", entity_id=str(r.id),
              summary=f"Updated rate {r.ads_code}: {list(data.keys())}",
              ip=request.client.host if request.client else None)
    return r


@router.delete("/{rate_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_rate(rate_id: int, request: Request, db: Session = Depends(get_db),
                claims: dict = Depends(require_roles("admin"))):
    r = db.get(RateCard, rate_id)
    if not r:
        raise HTTPException(status_code=404, detail="Rate not found")
    code = r.ads_code
    db.delete(r)
    db.commit()
    log_audit(db, claims, "delete", "rate_card", entity_id=str(rate_id), summary=f"Deleted rate {code}",
              ip=request.client.host if request.client else None)
