# app/webhooks/woo.py
import base64, hmac, hashlib, logging
from typing import Tuple
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from app.config import settings
from app.workers.jobs_worker import enqueue_job  # <- queue the work
from app.webhooks.archive import archive_ingress


logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/webhooks/woo", tags=["Woo Webhooks"])


def _redact(headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        out[k] = "<redacted>" if k.lower() == "x-wc-webhook-signature" else v
    return out


def _b64_hmac_sha256(secret: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(mac).decode("utf-8")


def _get_hdr(headers, key: str) -> str | None:
    v = headers.get(key)
    if v is None:
        v = headers.get(key.lower())
    return v


def _split_topic(topic: str) -> tuple[str | None, str | None]:
    """Split 'order.created' → ('order','created'), tolerate weird inputs."""
    if not topic or "." not in topic:
        return None, None
    try:
        a, b = topic.split(".", 1)
        return a or None, b or None
    except Exception:
        return None, None


async def _verify_signature(request: Request, body: bytes) -> Tuple[bool, str, str]:
    """
    Returns (ok, received_sig, expected_sig). If no secret is configured → (False, recv, '<no-secret-configured>').
    """
    received = _get_hdr(request.headers, "X-WC-Webhook-Signature") or ""
    secret = getattr(settings, "WOO_WEBHOOK_SECRET", "") or ""
    if not secret:
        return False, received, "<no-secret-configured>"
    expected = _b64_hmac_sha256(secret, body)
    ok = hmac.compare_digest(received or "", expected)
    return ok, received, expected


@router.post("")
@router.post("/")
async def woo_webhook(request: Request) -> Response:
    # 0) Optional debug: log headers/body peek
    if getattr(settings, "WOO_WEBHOOK_DEBUG", False):
        hdrs = {k: v for k, v in request.headers.items()}
        logger.info("[WOO-HOOK][DEBUG] incoming headers=%s", _redact(hdrs))

    # 1) Read body ONCE
    body = await request.body()

    hdrs = {k: v for k, v in request.headers.items()}
    topic_peek = _get_hdr(request.headers, "X-WC-Webhook-Topic")
    delivery_peek = _get_hdr(request.headers, "X-WC-Webhook-Delivery-ID")
    try:
        archive_ingress("woo", hdrs, body, delivery_id=delivery_peek, topic=topic_peek)
    except Exception:
        # archival is best-effort; never fail the hook for this
        pass

    if getattr(settings, "WOO_WEBHOOK_DEBUG", False):
        logger.info("[WOO-HOOK][DEBUG] first_256_bytes=%r", body[:256])

    # 2) Handle Woo "ping" (unsigned, form-encoded `webhook_id=...`)
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("application/x-www-form-urlencoded") and body.startswith(b"webhook_id="):
        logger.info("[WOO-HOOK] ping accepted (unsigned) body_len=%d", len(body))
        return JSONResponse({"ok": True, "ping": True})

    # 3) Verify HMAC signature for real events
    ok, received_sig, expected_sig = await _verify_signature(request, body)
    topic = _get_hdr(request.headers, "X-WC-Webhook-Topic") or ""
    resource = _get_hdr(request.headers, "X-WC-Webhook-Resource")
    event = _get_hdr(request.headers, "X-WC-Webhook-Event")

    if (not resource or not event) and topic:
        a, b = _split_topic(topic)
        resource = resource or a
        event = event or b

    if getattr(settings, "WOO_WEBHOOK_DEBUG", False):
        logger.info(
            "[WOO-HOOK] topic=%s resource=%s event=%s sig_ok=%s body_len=%d",
            topic, resource, event, ok, len(body)
        )

    if not ok:
        logger.warning("[WOO-HOOK] signature mismatch; returning 401")
        return JSONResponse(status_code=401, content={"ok": False, "reason": "invalid_signature"})

    # 4) Parse and validate JSON payload (most Woo core topics send JSON)
    from app.webhooks.woo_models import WooWebhookPayload
    try:
        raw_payload = await request.json()
        payload = WooWebhookPayload.parse_obj(raw_payload)
    except Exception as e:
        logger.warning(f"[WOO-HOOK] payload validation error: {e}")
        return JSONResponse(status_code=422, content={"ok": False, "reason": "invalid_payload", "error": str(e)})

    # Delivery IDs for idempotency
    delivery_id = _get_hdr(request.headers, "X-WC-Webhook-Delivery-ID") or None
    webhook_id = _get_hdr(request.headers, "X-WC-Webhook-ID") or None

    # 5) Enqueue background job (fast ACK)
    job_type = f"woo.{resource}.{event}" if (resource and event) else "woo.unknown"
    await enqueue_job({
        "type": job_type,               # e.g., "woo.order.created", "woo.customer.updated"
        "topic": topic or None,
        "resource": resource,
        "event": event,
        "delivery_id": delivery_id,
        "webhook_id": webhook_id,
        "payload": payload,
        "raw_len": len(body),
    })

    # 6) ACK quickly
    return JSONResponse({"ok": True, "topic": topic, "resource": resource, "event": event})
