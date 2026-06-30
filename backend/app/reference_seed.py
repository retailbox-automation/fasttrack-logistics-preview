"""Canonical reference data (vessels / ports / departments / service codes) + idempotent seed.

Values pulled from the existing prototype (index.html: SIDEBAR_DATA.vessels, INVENTORY
departments, SDR ADS codes) and knowledge docs (msc-cruises-domain.md, phase1-data-model-draft.md).
Not invented. Seeds a kind only if it has zero rows (like seed_users_if_empty)."""
import logging

from app.database import SessionLocal
from app.models import ReferenceItem

log = logging.getLogger("ft.reference")

VESSELS = [
    "MSC Seascape", "MSC Meraviglia", "MSC Seashore", "MSC Seaside", "MSC World America",
    "MSC Divina", "MSC Grandiosa", "MSC Virtuosa", "MSC Bellissima", "MSC Armonia",
    "MSC Poesia", "MSC Euribia", "MSC Magnifica", "Ocean Cay (destination)",
]

# Practical inventory departments + the canonical MSC department set.
DEPARTMENTS = [
    "MSC Foundation", "Shop", "Ocean Cay", "Hotel",
    "Beverage and Food", "Engine", "Shops", "Medical", "Casino", "Hotel Purchasing",
    "Deck and Safety", "Entertainment", "Miscellaneous", "Quality / Refurbishment",
    "Show Technology", "Spa", "Technical",
]

PORTS = [
    ("MIA", "Miami"), ("PCV", "Port Canaveral"), ("OCY", "Ocean Cay"),
    ("PHI", "Philipsburg (St Maarten)"), ("GAL", "Galveston"), ("SEA", "Seattle"),
    ("SSZ", "Santos"), ("DXB", "Dubai"),
]

# ADS service codes from the SDR module (code → description).
SERVICE_CODES = [
    ("ADS-000022", "PO handling (per PO)"),
    ("ADS-000065", "Offload return — Miami"),
    ("ADS-000144", "Port terminal import fee"),
    ("ADS-000157", "ISF filing"),
    ("ADS-000328", "Container storage at yard (per day)"),
    ("ADS-000422", "Container storage at yard (per day, alt)"),
    ("ADS-001456", "Fuel adjustment 15% (trucking)"),
    ("ADS-001479", "Container land transport — round trip"),
    ("ADS-001886", "Fuel surcharge 15% (containers)"),
    ("ADS-002580", "7512 issuing & validation"),
    ("ADS-002639", "Chassis charge"),
    ("ADS-002655", "Truck delivery at port 53ft (dry)"),
    ("ADS-002656", "Truck delivery at port 53ft (reefer)"),
    ("ADS-002845", "FDA filing"),
    ("ADS-002846", "Bond fee per container"),
    ("ADS-002856", "Bonded fee (per shipment)"),
    ("ADS-002858", "Weekend delivery fee"),
    ("ADS-002897", "Pick-up — Miami / Galveston"),
    ("ADS-002954", "Loading / unloading"),
    ("ADS-003082", "Demurrage (per container)"),
]


def _norm(kind: str):
    if kind == "vessel":
        return [(v, v, None) for v in VESSELS]
    if kind == "department":
        return [(d, d, None) for d in DEPARTMENTS]
    if kind == "port":
        return [(c, n, None) for c, n in PORTS]
    if kind == "service_code":
        return [(c, n, None) for c, n in SERVICE_CODES]
    return []


def _seed_kind(db, kind: str) -> int:
    if db.query(ReferenceItem).filter(ReferenceItem.kind == kind).count() > 0:
        return 0
    items = _norm(kind)
    for i, (code, name, meta) in enumerate(items):
        db.add(ReferenceItem(kind=kind, code=code, name=name, meta=meta, sort_order=i, active=True))
    db.commit()
    return len(items)


def seed_reference_if_empty():
    """First-boot seed per kind (idempotent — skips a kind that already has rows)."""
    db = SessionLocal()
    try:
        counts = {k: _seed_kind(db, k) for k in ("vessel", "port", "department", "service_code")}
        if any(counts.values()):
            log.info("reference_seeded", extra={"counts": counts})
        else:
            log.info("reference_seed_skip")
    except Exception as e:
        log.exception("reference_seed_failed: %s", e)
        db.rollback()
    finally:
        db.close()
