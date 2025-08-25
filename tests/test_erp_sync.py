import pytest
from app.erp.erp_orders import upsert_sales_order_from_woo

# Valid ERPNext sync payload
valid_payload = {
    "order_id": 1001,
    "customer": {
        "customer_name": "Test Customer",
        "email": "test@example.com",
        "phone": "1234567890",
        "first_name": "Test",
        "last_name": "Customer"
    },
    "billing": {
        "address_line1": "123 Main St",
        "city": "Testville",
        "country": "South Africa",
        "pincode": "12345"
    },
    "shipping": {
        "address_line1": "456 Side St",
        "city": "Shipville",
        "country": "South Africa",
        "pincode": "67890"
    },
    "items": [
        {"item_code": "SKU123", "qty": 2, "rate": 50.0, "amount": 100.0},
        {"item_code": "SKU456", "qty": 1, "rate": 75.0, "amount": 75.0}
    ]
}

# Invalid ERPNext sync payload (missing order_id, wrong item type)
invalid_payload = {
    "customer": {
        "customer_name": "Test Customer"
    },
    "items": [
        {"item_code": "SKU123", "qty": "not-a-float", "rate": "fifty", "amount": "hundred"}
    ]
}

def test_upsert_sales_order_valid():
    # Should not raise validation error
    try:
        # Use a dummy async runner for direct call
        import asyncio
        result = asyncio.run(upsert_sales_order_from_woo(valid_payload))
        assert result is not None
    except Exception as e:
        pytest.fail(f"Unexpected error for valid payload: {e}")

def test_upsert_sales_order_invalid():
    # Should raise validation error
    import asyncio
    with pytest.raises(AssertionError):
        asyncio.run(upsert_sales_order_from_woo(invalid_payload))
