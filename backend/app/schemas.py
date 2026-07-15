"""Pydantic schemas for API request/response."""
from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, ConfigDict


class InventoryItemBase(BaseModel):
    warehouse_receipt: str
    part_number: str
    description: str
    department: Optional[str] = None
    vessel: Optional[str] = None
    package_unit: str
    pieces: int
    quantity: int
    location_code: str
    entry_number: Optional[str] = None
    po_number: Optional[str] = None
    weight_lb: Optional[float] = None
    received_date: Optional[date] = None
    received_by: Optional[str] = None
    status: str = "in_stock"
    notes: Optional[str] = None


class InventoryItemCreate(InventoryItemBase):
    pass


class InventoryItemUpdate(BaseModel):
    warehouse_receipt: Optional[str] = None
    part_number: Optional[str] = None
    description: Optional[str] = None
    department: Optional[str] = None
    vessel: Optional[str] = None
    package_unit: Optional[str] = None
    pieces: Optional[int] = None
    quantity: Optional[int] = None
    location_code: Optional[str] = None
    entry_number: Optional[str] = None
    po_number: Optional[str] = None
    weight_lb: Optional[float] = None
    received_date: Optional[date] = None
    received_by: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class InventoryItemMove(BaseModel):
    new_location: str
    reason: Optional[str] = None
    by: Optional[str] = None


class InventoryItemOut(InventoryItemBase):
    id: int
    movements: Optional[list] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EmailMessageOut(BaseModel):
    id: int
    mailbox: str
    subject: Optional[str] = None
    from_name: Optional[str] = None
    from_email: Optional[str] = None
    to_recipients: Optional[list] = None
    received_at: Optional[datetime] = None
    body_preview: Optional[str] = None
    body_content: Optional[str] = None
    importance: Optional[str] = None
    is_read: bool
    has_attachments: bool
    web_link: Optional[str] = None
    conversation_id: Optional[str] = None
    category: Optional[str] = None
    ai_summary: Optional[str] = None
    urgency: Optional[str] = None
    language: Optional[str] = None
    ai_processed: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TimeEntryOut(BaseModel):
    id: int
    user_id: int
    user_name: str
    clock_in_at: datetime
    clock_out_at: Optional[datetime] = None
    note: Optional[str] = None
    source: str
    edited_by: Optional[str] = None
    duration_minutes: int
    is_open: bool

    model_config = ConfigDict(from_attributes=True)


class ClockInOut(BaseModel):
    note: Optional[str] = None


class TimeEntryUpdate(BaseModel):
    clock_in_at: Optional[datetime] = None
    clock_out_at: Optional[datetime] = None
    note: Optional[str] = None


class ShipmentBase(BaseModel):
    public_id: str
    vessel: str = ""
    truck: str = ""
    seal: Optional[str] = None
    driver: Optional[str] = None
    port: str = ""
    departure: Optional[date] = None
    cruise: Optional[str] = None
    status: str = "draft"
    po_number: Optional[str] = None
    invoice_number: Optional[str] = None
    vendor: str = "FAST TRACK WORLDWIDE LOGISTIC"
    customs_docs: Optional[str] = None
    notes: Optional[str] = None
    item_ids: list = []                 # frontend inventory array indexes (round-tripped)
    cross_dock_item_ids: list = []
    truck_dimensions: Optional[dict] = None
    delivery_address: Optional[str] = None
    created_by: Optional[str] = None
    meta: Optional[dict] = None          # {createdAt, inv_backend_ids}


class ShipmentCreate(ShipmentBase):
    inventory_item_ids: list = []        # real inventory backend ids → server totals + marking


class ShipmentUpdate(BaseModel):
    vessel: Optional[str] = None
    truck: Optional[str] = None
    seal: Optional[str] = None
    driver: Optional[str] = None
    port: Optional[str] = None
    departure: Optional[date] = None
    cruise: Optional[str] = None
    status: Optional[str] = None
    po_number: Optional[str] = None
    invoice_number: Optional[str] = None
    customs_docs: Optional[str] = None
    notes: Optional[str] = None


class ShipmentOut(ShipmentBase):
    id: int
    totals: Optional[dict] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ShipmentBulk(BaseModel):
    items: list[ShipmentCreate]


# ── Warehouse Receipt (intake side, Stage 1.5) ──
class WRLineIn(BaseModel):
    part_number: str
    description: str
    department: Optional[str] = None
    package_unit: str = "Box"      # "Box" | "Pallet"
    pieces: int = 1
    quantity: int = 1
    weight_lb: Optional[float] = None
    location_code: str = ""


class WarehouseReceiptCreate(BaseModel):
    public_id: Optional[str] = None        # auto WR-2026-NNNN if omitted
    received_date: Optional[date] = None
    received_by: Optional[str] = None       # defaults to signed-in user
    vessel: Optional[str] = None
    department: Optional[str] = None
    vendor: Optional[str] = "FAST TRACK WORLDWIDE LOGISTIC"
    po_number: Optional[str] = None
    carrier: Optional[str] = None
    tracking: Optional[str] = None
    notes: Optional[str] = None
    lines: list[WRLineIn] = []


class WarehouseReceiptOut(BaseModel):
    id: int
    public_id: str
    received_date: Optional[date] = None
    received_by: Optional[str] = None
    vessel: Optional[str] = None
    department: Optional[str] = None
    vendor: Optional[str] = None
    po_number: Optional[str] = None
    carrier: Optional[str] = None
    tracking: Optional[str] = None
    status: str
    notes: Optional[str] = None
    lines: list = []
    item_ids: list = []
    totals: Optional[dict] = None
    created_by: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Customs status & document tracking (Stage 1.9) ──
_CUSTOMS_FILING_STATES = {"na", "pending", "requested", "filed", "cleared"}


class CustomsDoc(BaseModel):
    type: str
    status: str = "pending"          # pending | requested | received | filed | cleared | na
    requested_at: Optional[date] = None
    received_at: Optional[date] = None
    note: Optional[str] = None


class CustomsRecordCreate(BaseModel):
    public_id: Optional[str] = None          # auto CE-YYYY-NNNN
    shipment_public_id: Optional[str] = None
    entry_number: Optional[str] = None
    vessel: Optional[str] = None
    broker: Optional[str] = None
    firms_code: str = "LCS5"
    isf_status: str = "pending"
    doc_7512_status: str = "pending"
    aes_sed_status: str = "na"
    bonded: bool = False
    bonded_release_due: Optional[date] = None
    sailing_date: Optional[date] = None
    notes: Optional[str] = None
    docs: list[CustomsDoc] = []


class CustomsRecordUpdate(BaseModel):
    shipment_public_id: Optional[str] = None
    entry_number: Optional[str] = None
    vessel: Optional[str] = None
    broker: Optional[str] = None
    firms_code: Optional[str] = None
    isf_status: Optional[str] = None
    doc_7512_status: Optional[str] = None
    aes_sed_status: Optional[str] = None
    bonded: Optional[bool] = None
    bonded_release_due: Optional[date] = None
    sailing_date: Optional[date] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    docs: Optional[list[CustomsDoc]] = None


class CustomsRecordOut(BaseModel):
    id: int
    public_id: str
    shipment_public_id: Optional[str] = None
    entry_number: Optional[str] = None
    vessel: Optional[str] = None
    broker: Optional[str] = None
    firms_code: str
    isf_status: str
    doc_7512_status: str
    aes_sed_status: str
    bonded: bool
    bonded_release_due: Optional[date] = None
    sailing_date: Optional[date] = None
    status: str
    notes: Optional[str] = None
    docs: list = []
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # computed
    cleared: bool = False
    blockers: list[str] = []

    model_config = ConfigDict(from_attributes=True)


# ── Billing: typed Invoices (Stage 2.1) ──
INVOICE_STATES = {"draft", "pending_approval", "sent", "paid", "void"}


class InvoiceLine(BaseModel):
    code: str
    desc: str = ""
    qty: float = 0
    rate: float = 0


class InvoiceCreate(BaseModel):
    public_id: Optional[str] = None              # auto FT-INV-NNNNN if omitted
    sdr_public_id: Optional[str] = None          # links by public id (SDR-YYYY-NNNN)
    loading_list_public_id: Optional[str] = None  # LL-YYYY-NNNN
    pon: str
    invoice_date: Optional[date] = None          # defaults to today
    bill_to: str
    attn: Optional[str] = None
    terms: str = "NET 30"
    currency: str = "USD"
    fuel: float = 15.0                            # fuel surcharge, percent
    gwx: Optional[str] = None
    cruise_id: Optional[str] = None
    issued_by: Optional[str] = None              # defaults to signed-in user
    out_of_scope: bool = False
    status: str = "draft"
    notes: Optional[str] = None
    lines: list[InvoiceLine] = []


class InvoiceUpdate(BaseModel):
    sdr_public_id: Optional[str] = None
    loading_list_public_id: Optional[str] = None
    pon: Optional[str] = None
    invoice_date: Optional[date] = None
    bill_to: Optional[str] = None
    attn: Optional[str] = None
    terms: Optional[str] = None
    currency: Optional[str] = None
    fuel: Optional[float] = None
    gwx: Optional[str] = None
    cruise_id: Optional[str] = None
    issued_by: Optional[str] = None
    out_of_scope: Optional[bool] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    lines: Optional[list[InvoiceLine]] = None


class InvoiceOut(BaseModel):
    id: int
    public_id: str
    sdr_public_id: Optional[str] = None
    loading_list_public_id: Optional[str] = None
    pon: str
    invoice_date: Optional[date] = None
    bill_to: str
    attn: Optional[str] = None
    terms: str
    currency: str
    fuel: float
    gwx: Optional[str] = None
    cruise_id: Optional[str] = None
    issued_by: Optional[str] = None
    out_of_scope: bool
    status: str
    notes: Optional[str] = None
    lines: list = []
    issue_date: datetime
    # computed server-side (Σ qty×rate, + fuel%)
    subtotal: float = 0
    fuel_amt: float = 0
    total: float = 0

    model_config = ConfigDict(from_attributes=True)


# ── Billing: typed Shipment Detail Report (SDR) ──
SDR_STATES = {"draft", "sent_to_msc", "variables_added", "po_received", "closed"}


class SDRCreate(BaseModel):
    public_id: Optional[str] = None              # auto SDR-YYYY-NNNN
    gwx: str
    loading_list_public_id: Optional[str] = None
    bill_to: str
    ms_contact: Optional[str] = None
    prepared_by: Optional[str] = None            # defaults to signed-in user
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    pon: Optional[str] = None
    status: str = "draft"
    notes: Optional[str] = None
    variables: Optional[str] = None
    invoice_public_id: Optional[str] = None
    qtys: dict = {}                              # ADS code → qty
    sent_at: Optional[datetime] = None
    variables_at: Optional[datetime] = None
    po_received_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None


class SDRUpdate(BaseModel):
    gwx: Optional[str] = None
    loading_list_public_id: Optional[str] = None
    bill_to: Optional[str] = None
    ms_contact: Optional[str] = None
    prepared_by: Optional[str] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    pon: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    variables: Optional[str] = None
    invoice_public_id: Optional[str] = None
    qtys: Optional[dict] = None
    sent_at: Optional[datetime] = None
    variables_at: Optional[datetime] = None
    po_received_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None


class SDROut(BaseModel):
    id: int
    public_id: str
    gwx: str
    loading_list_public_id: Optional[str] = None
    bill_to: str
    ms_contact: Optional[str] = None
    prepared_by: str
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    sent_at: Optional[datetime] = None
    variables_at: Optional[datetime] = None
    po_received_at: Optional[datetime] = None
    pon: Optional[str] = None
    closed_at: Optional[datetime] = None
    status: str
    notes: Optional[str] = None
    variables: Optional[str] = None
    invoice_public_id: Optional[str] = None
    qtys: dict = {}
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Billing: typed Credit Memo ──
CM_STATES = {"open", "applied", "void"}
SOA_POSITIONS = {"above", "below"}


class CreditMemoCreate(BaseModel):
    public_id: Optional[str] = None              # auto FT-CM-NNNN
    applied_to_invoice_public_id: str
    amount: float
    issue_date: Optional[date] = None            # defaults today
    due_date: Optional[date] = None
    reason: str
    description: Optional[str] = None
    issued_by: Optional[str] = None              # defaults to signed-in user
    status: str = "open"
    soa_position: str = "below"


class CreditMemoUpdate(BaseModel):
    applied_to_invoice_public_id: Optional[str] = None
    amount: Optional[float] = None
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    reason: Optional[str] = None
    description: Optional[str] = None
    issued_by: Optional[str] = None
    status: Optional[str] = None
    soa_position: Optional[str] = None


class CreditMemoOut(BaseModel):
    id: int
    public_id: str
    applied_to_invoice_public_id: str
    amount: float
    issue_date: date
    due_date: Optional[date] = None
    reason: str
    description: Optional[str] = None
    issued_by: Optional[str] = None
    status: str
    soa_position: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HealthOut(BaseModel):
    status: str
    db: str
    version: str
    graph: str = "unknown"            # "configured" | "not_configured" — catches vanished MS_* creds
    email_newest_minutes: int | None = None  # age of newest ingested email; large/None = sync stalled
    auth_secret: str = "unknown"      # "ok" | "DEFAULT-INSECURE" — catches a wiped/unset JWT_SECRET
