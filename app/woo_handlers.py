# app/woo_handlers.py
from __future__ import annotations
from typing import Any

# Phase 1: just log/trace. Later we’ll upsert ERPNext Customer/Order/Payment.
async def handle_woo_webhook(payload: Any):
    # You can inspect 'resource'/'event' fields here and branch:
    # e.g. orders.create / orders.updated / customers.create / refunds.create
    # For now, just log to stdout (uvicorn logs).
    from pprint import pformat
    print("[WOO][WEBHOOK] payload:\n", pformat(payload)[:2000])
