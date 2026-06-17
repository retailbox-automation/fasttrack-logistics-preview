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


class HealthOut(BaseModel):
    status: str
    db: str
    version: str
