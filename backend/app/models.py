"""
SQLAlchemy models mirroring the prototype data model.
Phase 1A: Inventory + Loading List + SDR + Invoice + Credit Memo + Discrepancy.
"""
from datetime import datetime, date
from typing import Optional, List as TypingList
from sqlalchemy import String, Integer, Float, Boolean, ForeignKey, DateTime, Date, JSON, Text
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


class ShipmentDetailReport(Base):
    __tablename__ = "shipment_detail_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # SDR-2026-NNNN
    gwx: Mapped[str] = mapped_column(String(32), index=True)
    loading_list_id: Mapped[Optional[int]] = mapped_column(ForeignKey("loading_lists.id"), nullable=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
