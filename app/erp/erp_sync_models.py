from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class ERPAddress(BaseModel):
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    class Config:
        extra = "allow"

class ERPCustomer(BaseModel):
    customer_name: str
    email: Optional[str]
    phone: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    class Config:
        extra = "allow"

class ERPOrderItem(BaseModel):
    item_code: str
    qty: float
    rate: float
    amount: float
    class Config:
        extra = "allow"

class ERPOrderSyncPayload(BaseModel):
    order_id: int
    customer: ERPCustomer
    billing: Optional[ERPAddress]
    shipping: Optional[ERPAddress]
    items: List[ERPOrderItem]
    class Config:
        extra = "allow"
