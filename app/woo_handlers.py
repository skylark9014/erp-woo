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
        from app.workers.jobs_worker import enqueue_job
        if resource == "customer" and event in ("created", "updated") and body:
            logger.info(f"[WOO][WEBHOOK] Enqueuing ERPNext customer job for id={body.get('id')}")
            await enqueue_job({
                "type": f"woo.customer.{event}",
                "resource": "customer",
                "event": event,
                "payload": body,
                "delivery_id": payload.get("delivery_id"),
            })
            status["success"] = True
            status["id"] = body.get("id")
        elif resource == "order" and event in ("created", "updated", "cancelled") and body:
            logger.info(f"[WOO][WEBHOOK] Enqueuing ERPNext order job for event '{event}' id={body.get('id')}")
            await enqueue_job({
                "type": f"woo.order.{event}",
                "resource": "order",
                "event": event,
                "payload": body,
                "delivery_id": payload.get("delivery_id"),
            })
            status["success"] = True
            status["id"] = body.get("id")
        elif resource == "refund" and event in ("created", "updated", "cancelled") and body:
            logger.info(f"[WOO][WEBHOOK] Enqueuing ERPNext refund job for event '{event}' id={body.get('id')}")
            await enqueue_job({
                "type": f"woo.refund.{event}",
                "resource": "refund",
                "event": event,
                "payload": body,
                "delivery_id": payload.get("delivery_id"),
            })
            status["success"] = True
            status["id"] = body.get("id")
        elif not resource or not event:
            logger.warning("[WOO][WEBHOOK] No resource/event found in payload. ERPNext creation will not be triggered. Ensure webhook archiving includes resource/event fields.")
            status["error"] = "No resource/event found in payload"
    except Exception as e:
        logger.error(f"[WOO][WEBHOOK] ERPNext upsert failed: {e}")
        status["error"] = f"ERPNext upsert failed: {e}"
    return status
