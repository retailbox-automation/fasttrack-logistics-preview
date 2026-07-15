"""CrunchTime outbound export — 009 Vendor Invoice (Stage 3.1, first cut).

Generates the CrunchTime "009 Vendor Invoice" inbound-interface file (the flat-file
layout Andrés sent 2026-07-15: docs/sample-files/Crunchtime-Inbound-Interface.xlsx)
from a typed Invoice. Record structure: one **H** (header) line + one **D** (detail)
line per invoice line. No footer on 009 (014 Consolidation has the F footer).

⚠️ ASSUMPTIONS — pending Andrés / CrunchTime confirmation (see
knowledge/crunchtime-inbound-interface-2026-07-15.md). Isolated in EXPORT_CONFIG /
the mapping below so they're easy to change once we get a real sample + answers:
  1. FORMAT: pipe-delimited (descriptions contain commas). Fixed-width (per the spec's
     column lengths) is the alternative — swap in _render() when a sample confirms it.
  2. SEMANTICS: we treat a Fast Track → MSC invoice AS a "vendor invoice" (FT = vendor
     to MSC). Line ADS service codes → Vendor Product Number; qty/rate → Invoice Qty/Price.
     If CrunchTime's 009 is meant for GOODS vendor invoices (food/beverage suppliers),
     the source entity is wrong and this maps from the WR/inventory side instead.
  3. Vendor Code / Location Code: FT constants below — real CrunchTime codes TBD.
  4. Fuel surcharge → Freight/Shipping value (Tax not modelled → 0). So H Invoice Total
     = Σ(D extended) + Freight = server `total`.
  5. Date format YYYY-MM-DD — CrunchTime's expected format unconfirmed.
  6. TRANSPORT (SFTP / portal / API) out of scope here — this endpoint just RENDERS the
     file; delivery is wired once Andrés confirms the channel.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Invoice
from app.auth import require_auth

router = APIRouter(prefix="/api/crunchtime", tags=["crunchtime"])

EXPORT_CONFIG = {
    "vendor_code": "FASTTRACK",   # TODO confirm FT's CrunchTime vendor code
    "location_code": "",          # TODO confirm per-port CrunchTime location code
    "freight_gl_desc": "FUEL SURCHARGE",
    "delimiter": "|",
}


def _amt(v, dp=2) -> str:
    try:
        return f"{float(v or 0):.{dp}f}"
    except (TypeError, ValueError):
        return f"{0:.{dp}f}"


def _line_subtotal(lines: list) -> float:
    s = 0.0
    for ln in (lines or []):
        s += float(ln.get("qty") or 0) * float(ln.get("rate") or 0)
    return round(s, 2)


def _009_records(inv: Invoice) -> list[list[str]]:
    """Return the 009 Vendor Invoice records as lists of string fields (H then D*)."""
    cfg = EXPORT_CONFIG
    lines = inv.lines or []
    subtotal = _line_subtotal(lines)
    fuel_amt = round(subtotal * (float(inv.fuel or 0) / 100.0), 2)
    total = round(subtotal + fuel_amt, 2)
    inv_date = inv.invoice_date.isoformat() if inv.invoice_date else ""

    # H — header (field order per the 009 spec)
    header = [
        "H",
        cfg["vendor_code"],                 # Vendor Code (6 or 30)
        cfg["location_code"],               # Location Code (32)
        inv.pon or "",                      # Purchase Order Number (40)
        "",                                 # Expected Delivery Date
        inv.public_id,                      # Vendor Invoice Number (40)
        inv_date,                           # Invoice Date
        _amt(total),                        # Invoice Total (10,2)
        "",                                 # Tax GL Description (not modelled)
        _amt(0),                            # Tax Value
        cfg["freight_gl_desc"] if fuel_amt else "",  # Freight/Shipping GL Description
        _amt(fuel_amt),                     # Freight/Shipping Value
        "", _amt(0),                        # Misc. 1 GL Desc / Value
        "", _amt(0),                        # Misc. 2 GL Desc / Value
    ]

    records = [header]
    for ln in lines:
        qty = float(ln.get("qty") or 0)
        rate = float(ln.get("rate") or 0)
        records.append([
            "D",
            str(ln.get("code") or ""),      # Vendor Product Number (40) ← ADS service code
            "N",                            # Catch Weight Indicator (Y/N)
            _amt(qty),                      # Invoice Qty (10,2)
            _amt(rate, 8),                  # Invoice Price (16,8)
            _amt(qty * rate),               # Extended Value (10,2)
            _amt(0),                        # Tax Value
            "N",                            # Substitution Indicator
            "", "", "", _amt(0, 4),         # Substituted Product #/Name/Package/Conversion
            "",                             # Lot Number
            "N",                            # Split Indicator
            _amt(0),                        # Credit Value
            "",                             # Product Brand
            "",                             # Manufacturer #
        ])
    return records


def _render(records: list[list[str]], delimiter: str) -> str:
    # Delimited (default). Fixed-width would pad each field to the spec length here.
    return "\n".join(delimiter.join(f for f in rec) for rec in records) + "\n"


@router.get("/vendor-invoice/{inv_id}", response_class=PlainTextResponse,
            dependencies=[Depends(require_auth)])
def vendor_invoice_009(inv_id: int, delimiter: str = Query(default=None, max_length=3),
                       db: Session = Depends(get_db)):
    """Render invoice <inv_id> as a CrunchTime 009 Vendor Invoice flat file (delimited)."""
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    delim = delimiter or EXPORT_CONFIG["delimiter"]
    body = _render(_009_records(inv), delim)
    return PlainTextResponse(
        body,
        headers={"Content-Disposition": f'attachment; filename="crunchtime-009-{inv.public_id}.txt"'},
    )
