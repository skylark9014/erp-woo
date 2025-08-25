from pydantic import BaseModel, Field
from typing import Optional, Any

class WooWebhookPayload(BaseModel):
    # Example fields; adjust according to actual WooCommerce webhook payload structure
    id: Optional[int] = Field(None, description="WooCommerce object ID")
    parent_id: Optional[int] = Field(None, description="Parent object ID")
    status: Optional[str] = Field(None, description="Status of the object")
    date_created: Optional[str] = Field(None, description="Creation date")
    date_modified: Optional[str] = Field(None, description="Modification date")
    total: Optional[str] = Field(None, description="Total amount")
    customer_id: Optional[int] = Field(None, description="Customer ID")
    # Add more fields as needed for your use case
    # Accepts arbitrary extra fields
    class Config:
        extra = "allow"
