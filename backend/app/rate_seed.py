"""Rate matrix baseline seed (Stage 2.2).

Seeds the current flat ADS rate card (mirrors the frontend ADS_RATES) as baseline rows
(ship=NULL, port=NULL → apply to any) if the rate_cards table is empty. Ship/port-specific
overrides come from MSC's rate sheet, added later via /api/rates. Idempotent (runs once).
"""
import logging

from app.database import SessionLocal
from app.models import RateCard

log = logging.getLogger("ft.rate_seed")

# (ads_code, description, rate, op, percent_surcharge)
_BASELINE = [
    ("ADS-002655", "Truck delivery at port 53ft (dry)", 612.00, "trucking", None),
    ("ADS-002656", "Truck delivery at port 53ft (reefer)", 680.00, "trucking", None),
    ("ADS-002954", "Loading / unloading", 480.00, "trucking", None),
    ("ADS-000065", "Offload return — Miami", 312.00, "trucking", None),
    ("ADS-002897", "Pick-up — Miami / Galveston", 250.00, "trucking", None),
    ("ADS-002856", "Bonded fee (per shipment)", 120.00, "trucking", None),
    ("ADS-000022", "PO handling (per PO)", 30.00, "trucking", None),
    ("ADS-03257", "Pallet in transit (per pallet)", 25.00, "trucking", None),
    ("ADS-002858", "Weekend delivery fee", 250.00, "trucking", None),
    ("ADS-001456", "Fuel adjustment 15% (trucking)", 0.0, "trucking", 0.15),
    ("ADS-001479", "Container land transport — round trip", 500.00, "import", None),
    ("ADS-000328", "Container storage at yard (per day)", 45.00, "import", None),
    ("ADS-000422", "Container storage at yard (per day, alt)", 45.00, "import", None),
    ("ADS-002639", "Chassis charge", 35.00, "import", None),
    ("ADS-003082", "Demurrage (per container)", 410.00, "import", None),
    ("ADS-002846", "Bond fee per container", 120.00, "import", None),
    ("ADS-000144", "Port terminal import fee", 137.25, "import", None),
    ("ADS-002580", "7512 issuing & validation", 90.00, "import", None),
    ("ADS-000157", "ISF filing", 45.00, "import", None),
    ("ADS-002845", "FDA filing", 200.00, "import", None),
    ("ADS-001886", "Fuel surcharge 15% (containers)", 0.0, "import", 0.15),
]


def seed_rates_if_empty():
    db = SessionLocal()
    try:
        if db.query(RateCard).count() > 0:
            return
        for code, desc, rate, op, pct in _BASELINE:
            db.add(RateCard(ads_code=code, description=desc, rate=rate, op=op,
                            percent_surcharge=pct, ship=None, port=None, active=True))
        db.commit()
        log.info("rate_seed: inserted %d baseline rate rows", len(_BASELINE))
    except Exception as e:
        db.rollback()
        log.warning("rate_seed failed: %s", e)
    finally:
        db.close()
