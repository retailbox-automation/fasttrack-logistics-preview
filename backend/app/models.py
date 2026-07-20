"""
SQLAlchemy models mirroring the prototype data model.
Phase 1A: Inventory + Loading List + SDR + Invoice + Credit Memo + Discrepancy.
"""
from datetime import datetime, date
from typing import Optional, List as TypingList
from sqlalchemy import String, Integer, Float, Boolean, ForeignKey, DateTime, Date, JSON, Text, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_receipt: Mapped[str] = mapped_column(String(32), index=True)
    part_number: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text)
    department: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    vessel: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    package_unit: Mapped[str] = mapped_column(String(16))  # "Box" or "Pallet"
    pieces: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    location_code: Mapped[str] = mapped_column(String(32), index=True)
    entry_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    po_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    weight_lb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    received_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    received_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="in_stock", index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    movements: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # audit log
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LoadingList(Base):
    __tablename__ = "loading_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # LL-2026-NNNN
    vessel: Mapped[str] = mapped_column(String(64))
    truck: Mapped[str] = mapped_column(String(32))
    seal: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    driver: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    port: Mapped[str] = mapped_column(String(64))
    departure: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    cruise: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    po_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    vendor: Mapped[str] = mapped_column(String(128), default="FAST TRACK WORLDWIDE LOGISTIC")
    customs_docs: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    item_ids: Mapped[list] = mapped_column(JSON, default=list)  # inventory_item ids
    cross_dock_item_ids: Mapped[list] = mapped_column(JSON, default=list)
    truck_dimensions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    delivery_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Stage 1.4: round-trip extras (createdAt display string, inv backend ids) + server totals
    meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    totals: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Live truck tracking (client priority): manual location source now; AirTag/GPS pluggable later
    current_location: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    tracking_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)  # staged|en_route|at_port|delivered
    located_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    tracking_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ShipmentDetailReport(Base):
    __tablename__ = "shipment_detail_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # SDR-2026-NNNN
    gwx: Mapped[str] = mapped_column(String(32), index=True)
    loading_list_id: Mapped[Optional[int]] = mapped_column(ForeignKey("loading_lists.id"), nullable=True)
    loading_list_public_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    bill_to: Mapped[str] = mapped_column(String(128))
    ms_contact: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    prepared_by: Mapped[str] = mapped_column(String(64))
    period_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    period_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    variables_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    po_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    pon: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    variables: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    invoice_public_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    qtys: Mapped[dict] = mapped_column(JSON, default=dict)  # ADS code → qty
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # FT-INV-NNNNN
    sdr_id: Mapped[Optional[int]] = mapped_column(ForeignKey("shipment_detail_reports.id"), nullable=True)
    loading_list_id: Mapped[Optional[int]] = mapped_column(ForeignKey("loading_lists.id"), nullable=True)
    # Business links are by public id (like CustomsRecord.shipment_public_id); the int FKs above are legacy stubs.
    sdr_public_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    loading_list_public_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    pon: Mapped[str] = mapped_column(String(64), index=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    bill_to: Mapped[str] = mapped_column(String(128))
    attn: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    terms: Mapped[str] = mapped_column(String(32), default="NET 30")
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    fuel: Mapped[float] = mapped_column(Float, default=15.0)
    gwx: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    cruise_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    issued_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    out_of_scope: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lines: Mapped[list] = mapped_column(JSON, default=list)  # [{code, desc, qty, rate}]
    issue_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CreditMemo(Base):
    __tablename__ = "credit_memos"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # FT-CM-NNNN
    applied_to_invoice_public_id: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[float] = mapped_column(Float)
    issue_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    issued_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    soa_position: Mapped[str] = mapped_column(String(8), default="below")  # above or below
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DiscrepancyReport(Base):
    __tablename__ = "discrepancy_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # DR-2026-NNNN
    loading_list_id: Mapped[Optional[int]] = mapped_column(ForeignKey("loading_lists.id"), nullable=True)
    loading_list_public_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    reported_at: Mapped[date] = mapped_column(Date)
    reported_by: Mapped[str] = mapped_column(String(128))
    ft_respondent: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    severity: Mapped[str] = mapped_column(String(16), default="minor")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lines: Mapped[list] = mapped_column(JSON, default=list)  # variance line items
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Document(Base):
    """Generic document storage for Phase 1A modules.

    Each module (loading_lists, sdrs, invoices, credit_memos, discrepancy_reports)
    stores its array as Documents keyed by kind. PUT /api/docs/{kind} replaces
    all documents of that kind in order. Avoids per-module schema overhead.
    """
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # ordering within kind
    data: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic locking
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Attachment(Base):
    """File / photo attached to a shipment (Loading List).

    Linked by shipment_public_id (the frontend LOADING_LISTS id, e.g. "LL-2026-0048")
    so it survives shipment bulk re-saves (public_id is stable; backend int ids are not).
    MVP stores bytes in Postgres with a per-file size cap; migrate to a volume / object
    store if attachment volume grows.
    """
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_public_id: Mapped[str] = mapped_column(String(32), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), default="file")  # "photo" | "document" | "file"
    data: Mapped[bytes] = mapped_column(LargeBinary)
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Approval(Base):
    """Generic approval / routing record for reports & billing.

    kind: weekly_report | invoice | sdr | discrepancy | report
    ref:  identifier of the item being approved (e.g. "2026-W27", an invoice id)
    status: submitted | approved | rejected | needs_correction
    Submit = ops/manager/admin; decide (approve/reject) = manager/admin.
    """
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    ref: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="submitted", index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    submitted_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    decided_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PickTask(Base):
    """Pick & Pack task — controlled picking with per-line qty validation + audit.

    `lines` is a list of dicts:
      {inventory_item_id, wr, part, description, location, package_unit,
       required, picked, scanned (bool), status ["pending"|"picked"|"short"]}
    status: open | in_progress | completed | short. On complete, inventory pieces
    are decremented by picked qty (traceable via inventory movements + audit).
    Photos attach via the attachments API using shipment_public_id = "PICK-<ref>".
    """
    __tablename__ = "pick_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(32), index=True)
    shipment_public_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    lines: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


class WarehouseReceipt(Base):
    """Warehouse Receipt — the intake document (Stage 1.5, Magaya-mimic).

    Receiving goods creates a WR (header + line items). On creation each line
    generates a real InventoryItem tagged with this WR number, so received cargo
    flows straight into the inventory control center. Mirrors how Magaya turns a
    Warehouse Receipt into stock. `lines` is a snapshot of what was received
    (each carries its generated inventory_item_id); `item_ids` lists those ids.
    """
    __tablename__ = "warehouse_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # WR-2026-NNNN
    received_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    received_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    vessel: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    department: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    vendor: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    po_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    carrier: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    tracking: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="received", index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lines: Mapped[list] = mapped_column(JSON, default=list)      # received-line snapshot
    item_ids: Mapped[list] = mapped_column(JSON, default=list)   # generated inventory ids
    totals: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CustomsRecord(Base):
    """Customs status & document tracking for a shipment (Stage 1.9).

    Formalizes the loose `LoadingList.customs_docs` string into a tracked record:
    per-document status (ISF, 7512, AES/SED), entry number, bonded status +
    3-business-day release timer, FIRMS code (LCS5), and an auditable list of
    document requests/responses. Ties customs clearance to loading readiness —
    a shipment shouldn't dispatch with customs open. Also the groundwork for
    moving customs filing off Magaya (per the off-ramp direction).

    `docs` is a list of dicts:
      {type, status ["pending"|"requested"|"received"|"filed"|"cleared"|"na"],
       requested_at, received_at, note}
    Per-doc convenience columns (isf/7512/aes) mirror the common ISF/7512/AES-SED
    filings; anything else lives in `docs`.
    """
    __tablename__ = "customs_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # CE-2026-NNNN
    shipment_public_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)  # LoadingList public_id
    entry_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)  # 9Q4-XXXXXX-X
    vessel: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    broker: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    firms_code: Mapped[str] = mapped_column(String(16), default="LCS5")  # bonded warehouse FIRMS
    # Per-filing status: na | pending | requested | filed | cleared
    isf_status: Mapped[str] = mapped_column(String(16), default="pending")
    doc_7512_status: Mapped[str] = mapped_column(String(16), default="pending")
    aes_sed_status: Mapped[str] = mapped_column(String(16), default="na")
    bonded: Mapped[bool] = mapped_column(Boolean, default=False)
    bonded_release_due: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # 3 business days
    sailing_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # ISF due 48h before (EU)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | cleared | hold
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    docs: Mapped[list] = mapped_column(JSON, default=list)  # request/response tracking
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="viewer", index=True)
    # Roles: admin (Andrés/Sultan), manager (Gabriela/Luis), ops (Andrea/Yamisley),
    #        viewer (read-only). Permission gates check this.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)  # force change on first login
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class EmailMessage(Base):
    """Email ingested from Microsoft Graph (Outlook/M365), read-only.

    Slice 1 = ingestion + storage. AI fields (category/ai_summary/urgency)
    are nullable placeholders filled in Slice 2 (Claude classify/summarize).
    """
    __tablename__ = "email_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    graph_id: Mapped[str] = mapped_column(String(512), unique=True, index=True)  # Graph message id
    internet_message_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, index=True)
    mailbox: Mapped[str] = mapped_column(String(128), index=True)  # which FT mailbox
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    from_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    from_email: Mapped[Optional[str]] = mapped_column(String(256), nullable=True, index=True)
    to_recipients: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    body_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # full plain-text body
    importance: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    web_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, index=True)
    # AI projections (Slice 2) — nullable for now
    category: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    urgency: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    language: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    ai_processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TimeEntry(Base):
    """Employee clock-in/clock-out record (per Andrés 6/19 — time tracking).

    One row per shift. Open shift = clock_out_at is NULL. Stored in UTC;
    frontend renders in ET. Andrés (admin) + Gabriela (manager) see all;
    ops/viewer see only their own. Manager corrections recorded (edited_by)."""
    __tablename__ = "time_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    user_name: Mapped[str] = mapped_column(String(128))  # denorm for fast reporting
    clock_in_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    clock_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="web")
    edited_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)  # manager who corrected
    edited_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    @property
    def duration_minutes(self) -> int:
        end = self.clock_out_at or datetime.utcnow()
        return max(0, int((end - self.clock_in_at).total_seconds() // 60))

    @property
    def is_open(self) -> bool:
        return self.clock_out_at is None


class AuditLog(Base):
    """Append-only audit trail of mutations.

    Records who did what when. Captures the diff payload so we can answer
    'who changed invoice INV-001 from draft to approved'."""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    user_role: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(32), index=True)  # create, update, delete, move, replace_all
    entity_kind: Mapped[str] = mapped_column(String(48), index=True)  # inventory_item, loading_lists, ...
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ReferenceItem(Base):
    """Reference / lookup data — single source of truth for dropdowns + validation.

    kind ∈ {vessel, port, department, service_code}. Seeded idempotently from the
    canonical values that already live in the prototype + knowledge docs."""
    __tablename__ = "reference_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(256))
    meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RateCard(Base):
    """Rate matrix (Stage 2.2): ADS service code × ship × port → rate.

    Baseline rows have ship=NULL and port=NULL (apply to any). Ship/port-specific
    overrides (from MSC's rate sheet) take precedence — see the lookup resolver in
    routers/rates.py (exact ship+port > ship-only > port-only > baseline). Rows can be
    effective-dated (NULL = always). Pricing varies by ship×port — same service can be
    $612 at one port and $3,000 at another (memory: pricing-varies-by-ship-port)."""
    __tablename__ = "rate_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    ads_code: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    ship: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)   # NULL = any
    port: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)   # NULL = any
    rate: Mapped[float] = mapped_column(Float, default=0.0)
    op: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)                 # trucking/import/other
    percent_surcharge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)     # e.g. 0.15 fuel
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    effective_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
