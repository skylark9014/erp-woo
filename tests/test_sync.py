import pytest
from fastapi.testclient import TestClient
from app.main_app import app

client = TestClient(app)

def test_full_sync():
    # Example: test POST to full sync endpoint
    payload = {"dry_run": True, "purge_bin": False}
    response = client.post("/api/sync/full", json=payload, auth=("admin", "adminpass"))
    assert response.status_code in (200, 202)
    # Add more assertions for job creation, status, etc.

# Add more tests for partial sync, job queue, retry, etc.
