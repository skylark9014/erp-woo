import pytest
from fastapi.testclient import TestClient
from app.main_app import app

client = TestClient(app)

def test_webhook_payload_ingestion():
    # Valid payload (matches WooWebhookPayload)
    payload = {
        "id": 123,
        "status": "processing",
        "date_created": "2024-06-01T12:00:00",
        "total": "99.99",
        "customer_id": 456
    }
    response = client.post("/webhooks/woo/", json=payload)
    assert response.status_code == 200
    assert response.json()["ok"] is True

def test_webhook_payload_invalid():
    # Invalid payload (missing required structure, e.g. wrong type)
    payload = {
        "id": "not-an-int",  # should be int
        "total": 99.99,       # should be str
    }
    response = client.post("/webhooks/woo/", json=payload)
    assert response.status_code == 422
    assert response.json()["ok"] is False
    assert response.json()["reason"] == "invalid_payload"

# Add more tests for error cases, invalid payloads, etc.
