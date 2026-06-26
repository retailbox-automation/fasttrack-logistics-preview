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


class HealthOut(BaseModel):
    status: str
    db: str
    version: str
