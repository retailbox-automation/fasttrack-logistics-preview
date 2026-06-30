"""Server-side PDF generation for dispatch documents (Stage 1.7).

Pure-Python (reportlab — no system deps). Generates the three dispatch docs
from a LoadingList + its linked inventory rows:
  - Loading List (manifest)        doc="ll"
  - Cargo Release (by department)  doc="cr"
  - Delivery Order (driver)        doc="do"

TODO: validate layout against MSC's official Loading List template once the
client sends it (built to ops manual §4.4 meanwhile).
"""
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable,
)

FT_NAME = "FAST TRACK WORLDWIDE LOGISTIC"
FT_ADDR = "1674 NW 215th Street, Miami Gardens, FL 33056"

_styles = getSampleStyleSheet()
_H = ParagraphStyle("h", parent=_styles["Title"], fontSize=16, spaceAfter=2)
_SUB = ParagraphStyle("sub", parent=_styles["Normal"], fontSize=9, textColor=colors.HexColor("#555555"))
_LBL = ParagraphStyle("lbl", parent=_styles["Normal"], fontSize=7.5, textColor=colors.HexColor("#777777"))
_VAL = ParagraphStyle("val", parent=_styles["Normal"], fontSize=10, leading=12)
_CELL = ParagraphStyle("cell", parent=_styles["Normal"], fontSize=7.5, leading=9)
_CELLH = ParagraphStyle("cellh", parent=_styles["Normal"], fontSize=7.5, leading=9, textColor=colors.white)
_SECT = ParagraphStyle("sect", parent=_styles["Normal"], fontSize=10, spaceBefore=8, spaceAfter=2,
                       textColor=colors.HexColor("#0a3d91"), fontName="Helvetica-Bold")


def _fmt_date(d):
    if not d:
        return "—"
    try:
        return d.strftime("%b %d, %Y")
    except Exception:
        return str(d)


def _item_dict(r):
    return {
        "wr": r.warehouse_receipt or "", "part": r.part_number or "",
        "desc": r.description or "", "dept": r.department or "",
        "pkg": r.package_unit or "", "pieces": int(r.pieces or 0),
        "qty": int(r.quantity or 0), "loc": r.location_code or "",
        "wt": float(r.weight_lb or 0),
    }


def _info_grid(pairs):
    """2-col label/value grid (4 columns: label,val,label,val)."""
    data = []
    row = []
    for i, (lbl, val) in enumerate(pairs):
        row.append(Paragraph(lbl.upper(), _LBL))
        row.append(Paragraph(str(val if val not in (None, "") else "—"), _VAL))
        if len(row) == 4:
            data.append(row)
            row = []
    if row:
        while len(row) < 4:
            row.append("")
        data.append(row)
    t = Table(data, colWidths=[0.9 * inch, 2.0 * inch, 0.9 * inch, 2.0 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def _header(title, accent, ll):
    els = [
        Paragraph(title, ParagraphStyle("th", parent=_H, textColor=colors.HexColor(accent))),
        Paragraph(f"{FT_NAME} · {FT_ADDR}", _SUB),
        HRFlowable(width="100%", thickness=2, color=colors.HexColor(accent), spaceBefore=4, spaceAfter=8),
    ]
    return els


def _signatures(cols):
    cells = [Paragraph("", _CELL) for _ in cols]
    line = Table([cells], colWidths=[(7.0 / len(cols)) * inch] * len(cols))
    labels = Table([[Paragraph("_______________________<br/>" + c, _CELL) for c in cols]],
                   colWidths=[(7.0 / len(cols)) * inch] * len(cols))
    return labels


def _doc(buf, story):
    SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        title="Fast Track dispatch document",
    ).build(story)


def _totals_line(ll, items):
    t = ll.totals or {}
    return (f"Items: {t.get('items', len(items))}   ·   Pieces: {t.get('pieces', sum(i['pieces'] for i in items))}"
            f"   ·   Pallets: {t.get('pallets', 0)}   ·   Weight: {t.get('weight_lb', 0)} lb")


def loading_list_pdf(ll, item_rows) -> bytes:
    items = [_item_dict(r) for r in item_rows]
    buf = io.BytesIO()
    story = _header("LOADING LIST", "#0a3d91", ll)
    story.append(_info_grid([
        ("Loading List #", ll.public_id), ("Status", (ll.status or "draft").upper()),
        ("Vessel", ll.vessel), ("Cruise/Trip", ll.cruise),
        ("Port", ll.port), ("Departure", _fmt_date(ll.departure)),
        ("Truck #", ll.truck), ("Seal #", ll.seal),
        ("Driver", ll.driver), ("PO #", ll.po_number),
        ("Vendor", ll.vendor), ("Customs docs", ll.customs_docs),
    ]))
    story.append(Spacer(1, 8))
    head = ["WR", "Part #", "Description", "Dept", "Pkg", "Pcs", "Qty", "Location", "Wt(lb)"]
    data = [[Paragraph(h, _CELLH) for h in head]]
    for it in items:
        data.append([
            Paragraph(it["wr"], _CELL), Paragraph(it["part"], _CELL),
            Paragraph(it["desc"], _CELL), Paragraph(it["dept"], _CELL),
            Paragraph(it["pkg"], _CELL), Paragraph(str(it["pieces"]), _CELL),
            Paragraph(str(it["qty"]), _CELL), Paragraph(it["loc"], _CELL),
            Paragraph(f"{it['wt']:.0f}", _CELL),
        ])
    if not items:
        data.append([Paragraph("No items linked to this loading list.", _CELL)] + [""] * 8)
    tbl = Table(data, repeatRows=1, colWidths=[0.7, 0.8, 2.2, 0.8, 0.5, 0.4, 0.4, 0.9, 0.5],
                hAlign="LEFT")
    tbl._argW = [w * inch for w in [0.7, 0.8, 2.2, 0.8, 0.5, 0.4, 0.4, 0.9, 0.5]]
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0a3d91")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7fb")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 6))
    story.append(Paragraph(_totals_line(ll, items), ParagraphStyle("tot", parent=_VAL, fontName="Helvetica-Bold")))
    story.append(Spacer(1, 24))
    story.append(_signatures(["FT Dispatcher", "MSC Gangway"]))
    _doc(buf, story)
    return buf.getvalue()


def cargo_release_pdf(ll, item_rows) -> bytes:
    items = [_item_dict(r) for r in item_rows]
    buf = io.BytesIO()
    story = _header("CARGO RELEASE", "#15803d", ll)
    story.append(_info_grid([
        ("Cargo Release #", ll.public_id), ("Vessel", ll.vessel),
        ("Port", ll.port), ("Departure", _fmt_date(ll.departure)),
        ("Truck #", ll.truck), ("Seal #", ll.seal),
    ]))
    by_dept = {}
    for it in items:
        by_dept.setdefault(it["dept"] or "UNASSIGNED", []).append(it)
    for dept, rows in by_dept.items():
        story.append(Paragraph(dept, _SECT))
        head = ["WR", "Part #", "Description", "Pcs", "Qty", "Wt(lb)"]
        data = [[Paragraph(h, _CELLH) for h in head]]
        for it in rows:
            data.append([Paragraph(it["wr"], _CELL), Paragraph(it["part"], _CELL),
                         Paragraph(it["desc"], _CELL), Paragraph(str(it["pieces"]), _CELL),
                         Paragraph(str(it["qty"]), _CELL), Paragraph(f"{it['wt']:.0f}", _CELL)])
        sub_pcs = sum(it["pieces"] for it in rows)
        data.append([Paragraph("<b>Subtotal</b>", _CELL), "", "", Paragraph(f"<b>{sub_pcs}</b>", _CELL), "", ""])
        t = Table(data, repeatRows=1, colWidths=[w * inch for w in [0.8, 0.9, 3.0, 0.5, 0.5, 0.6]])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#15803d")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
        story.append(Spacer(1, 4))
    if not items:
        story.append(Paragraph("No items linked.", _CELL))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Customs release: FIRMS LCS5 · CBP retention applies.", _SUB))
    story.append(Spacer(1, 20))
    story.append(_signatures(["FT Warehouse", "Customs", "MSC Receiving"]))
    _doc(buf, story)
    return buf.getvalue()


def delivery_order_pdf(ll, item_rows) -> bytes:
    items = [_item_dict(r) for r in item_rows]
    buf = io.BytesIO()
    story = _header("DELIVERY ORDER", "#c2410c", ll)
    story.append(_info_grid([
        ("Delivery Order #", ll.public_id), ("Vessel", ll.vessel),
        ("Pickup", FT_ADDR), ("Deliver to", ll.delivery_address or (ll.port + " — vessel terminal")),
        ("Truck #", ll.truck), ("Seal #", ll.seal),
        ("Driver", ll.driver), ("Departure", _fmt_date(ll.departure)),
    ]))
    story.append(Spacer(1, 8))
    story.append(Paragraph("CARGO SUMMARY", _SECT))
    story.append(Paragraph(_totals_line(ll, items), _VAL))
    story.append(Spacer(1, 8))
    story.append(Paragraph("DRIVER PROTOCOL", _SECT))
    steps = [
        "1. Confirm seal # matches this order before departure.",
        "2. Carry all customs documents listed on the Loading List.",
        "3. Proceed directly to the delivery terminal; no unscheduled stops.",
        "4. Present this Delivery Order + Cargo Release at the gangway.",
        "5. Obtain MSC gangway signature on receipt.",
        "6. Return signed copy to Fast Track dispatch.",
        "7. Report any discrepancy immediately to FT operations.",
    ]
    for s in steps:
        story.append(Paragraph(s, _CELL))
    story.append(Spacer(1, 24))
    story.append(_signatures(["FT Dispatcher", "Driver", "MSC Gangway"]))
    _doc(buf, story)
    return buf.getvalue()


GENERATORS = {"ll": loading_list_pdf, "cr": cargo_release_pdf, "do": delivery_order_pdf}
FILENAMES = {"ll": "loading-list", "cr": "cargo-release", "do": "delivery-order"}
