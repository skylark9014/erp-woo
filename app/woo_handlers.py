# app/woo_handlers.py
from __future__ import annotations
from typing import Any

# Phase 1: just log/trace. Later we’ll upsert ERPNext Customer/Order/Payment.
async def handle_woo_webhook(payload: Any):
    import logging, base64, json
    logger = logging.getLogger("uvicorn.error")
    from pprint import pformat
    resource = payload.get("resource") or None
    event = payload.get("event") or None
    body = None
    status = {"success": False, "resource": resource, "event": event, "error": None}
    if "body_b64" in payload:
        try:
            decoded = base64.b64decode(payload["body_b64"])
            body = json.loads(decoded)
        except Exception as e:
            logger.error(f"[WOO][WEBHOOK] Failed to decode body_b64: {e}")
            status["error"] = f"decode body_b64 failed: {e}"
            return status
    # Trigger ERPNext upsert for customer, order, refund
    try:
        if resource == "customer" and event in ("created", "updated") and body:
            from app.erp.erp_customers import upsert_customer_from_woo
            await upsert_customer_from_woo(body)
            logger.info(f"[WOO][WEBHOOK] ERPNext customer upsert complete for id={body.get('id')}")
            status["success"] = True
            status["id"] = body.get("id")
        elif resource == "order" and event in ("created", "updated") and body:
            logger.info(f"[WOO][WEBHOOK] Triggering ERPNext order upsert for id={body.get('id')}")
            from app.erp.erp_orders import upsert_sales_order_from_woo
            await upsert_sales_order_from_woo(body)
            logger.info(f"[WOO][WEBHOOK] ERPNext order upsert complete for id={body.get('id')}")
            status["success"] = True
            status["id"] = body.get("id")
        elif resource == "refund" and event in ("created", "updated") and body:
            logger.info(f"[WOO][WEBHOOK] Triggering ERPNext refund upsert for id={body.get('id')}")
            from app.erp.erp_orders import create_refund_payment_entry
            await create_refund_payment_entry(body)
            logger.info(f"[WOO][WEBHOOK] ERPNext refund upsert complete for id={body.get('id')}")
            status["success"] = True
            status["id"] = body.get("id")
        elif not resource or not event:
            logger.warning("[WOO][WEBHOOK] No resource/event found in payload. ERPNext creation will not be triggered. Ensure webhook archiving includes resource/event fields.")
            status["error"] = "No resource/event found in payload"
    except Exception as e:
        logger.error(f"[WOO][WEBHOOK] ERPNext upsert failed: {e}")
        status["error"] = f"ERPNext upsert failed: {e}"
    return status
