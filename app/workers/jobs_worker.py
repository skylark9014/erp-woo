# ---------------------------
# app/workers/jobs_worker.py
# ---------------------------
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.woo.woocommerce import fetch_order, fetch_order_refunds
from app.woo.order_normalizer import normalize_order
from app.erp.erp_orders import (
    upsert_sales_order_from_woo,
    create_sales_invoice_from_so,
    create_payment_entry,
    create_sales_invoice_return,
    create_refund_payment_entry,
    cancel_sales_invoice,
    cancel_sales_order,
    find_sales_invoice_by_po_no,
    find_sales_order_by_po_no,
    build_return_items_from_si,
)
from app.erp.erp_customers import upsert_customer_from_woo  # NEW

logger = logging.getLogger("uvicorn.error")

_QUEUE: "asyncio.Queue[dict]" = asyncio.Queue()

# Keep a single inbox folder; differentiate by key prefix (order-/customer-/refund-)
INBOX_DIR = Path("/code/data/inbox")


async def enqueue_job(job: Dict[str, Any]) -> None:
    try:
        _QUEUE.put_nowait(job)
    except asyncio.QueueFull:
        logger.error("Job queue full; dropping job: %s", job.get("type"))


async def worker_loop(stop_event: asyncio.Event) -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("[WORKER] started")

    from app.models.audit_log import add_audit_entry
    while not stop_event.is_set():
        try:
            job = await asyncio.wait_for(_QUEUE.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            logger.error("[WORKER] queue error: %s", e)
            await asyncio.sleep(0.5)
            continue

        try:
            jtype = (job.get("type") or "").strip()
            logger.info(f"[WORKER] Received job: type={jtype} resource={job.get('resource')} event={job.get('event')} delivery_id={job.get('delivery_id')}")
            add_audit_entry(
                action=f"Job Received: {jtype}",
                user="system",
                details=f"resource={job.get('resource')} event={job.get('event')} delivery_id={job.get('delivery_id')}"
            )
            if jtype == "woo.order.created":
                logger.info(f"[WORKER] Syncing order.created to ERPNext")
                add_audit_entry("ERPNext Sync", "system", f"Syncing order.created: {job}")
                await _handle_woo_order_created(job)
            elif jtype == "woo.order.updated":
                logger.info(f"[WORKER] Syncing order.updated to ERPNext")
                add_audit_entry("ERPNext Sync", "system", f"Syncing order.updated: {job}")
                await _handle_woo_order_updated(job)
            elif jtype in ("woo.customer.created", "woo.customer.updated"):
                logger.info(f"[WORKER] Syncing customer event to ERPNext")
                add_audit_entry("ERPNext Sync", "system", f"Syncing customer event: {job}")
                await _handle_woo_customer_event(job)
            elif jtype == "woo.refund.created":
                logger.info(f"[WORKER] Syncing refund.created to ERPNext")
                add_audit_entry("ERPNext Sync", "system", f"Syncing refund.created: {job}")
                await _handle_woo_refund_created(job)
            elif jtype.startswith("woo.order.") or jtype.startswith("woo.customer.") or jtype.startswith("woo.refund."):
                logger.info("[WORKER] ignoring unsupported job type=%s", jtype)
                add_audit_entry("Job Ignored", "system", f"Unsupported job type: {jtype}")
            else:
                logger.info("[WORKER] unknown job type=%s", jtype)
                add_audit_entry("Job Unknown", "system", f"Unknown job type: {jtype}")
        except Exception as e:
            logger.exception("[WORKER] failed job type=%s", job.get("type"), e)
            add_audit_entry("Job Failed", "system", f"Failed job type={job.get('type')} error={e}")
        finally:
            _QUEUE.task_done()

    logger.info("[WORKER] stopped")


# ---------------------------
# Helpers / Idempotency
# ---------------------------

def _base_key(job: Dict[str, Any]) -> str:
    """
    Stable key per Woo object id. Uses job.resource to prefix 'order-', 'customer-', or 'refund-'.
    Falls back to delivery_id or a hash when no id.
    """
    resource = (job.get("resource") or "").strip().lower() or "order"
    payload = job.get("payload") or {}
    obj_id = None
    try:
        obj_id = int(payload.get("id"))
    except Exception:
        try:
            obj_id = int(payload.get("resource_id"))
        except Exception:
            obj_id = None

    delivery_id = (job.get("delivery_id") or "").strip() or None
    if delivery_id:
        return delivery_id

    if resource == "customer":
        prefix = "customer"
    elif resource == "refund":
        prefix = "refund"
    else:
        prefix = "order"
    if obj_id is not None:
        return f"{prefix}-{obj_id}"

    return f"{prefix}-unknown-{abs(hash(json.dumps(job, default=str)))}"


def _marker_path(base: str, kind: str) -> Path:
    """
    kind in {"so","si","pe","cust","si_return","cancel_return","cancel_pe"}.
    Creates markers like:
      - order-<id>.so.done, .si.done, .pe.done
      - customer-<id>.cust.done
      - refund-<id>.si_return.done, .pe.done
      - order-<id>.cancel_return.done, .cancel_pe.done
    """
    return INBOX_DIR / f"{base}.{kind}.done"


def _read_marker(base: str, kind: str) -> str | None:
    p = _marker_path(base, kind)
    if p.exists():
        try:
            return p.read_text(encoding="utf-8").strip() or None
        except Exception:
            return "<done>"
    return None


def _write_marker(base: str, kind: str, value: str) -> None:
    p = _marker_path(base, kind)
    try:
        p.write_text(value or "done", encoding="utf-8")
    except Exception:
        logger.warning("[WORKER] failed writing marker %s", p)


def _refund_marker_exists(kind: str, refund_id: int) -> bool:
    return _read_marker(f"refund-{refund_id}", kind) is not None


def _write_refund_marker(kind: str, refund_id: int, value: str = "done") -> None:
    _write_marker(f"refund-{refund_id}", kind, value)


async def _load_order_from_job(job: Dict[str, Any]) -> Dict[str, Any] | None:
    """Prefer full payload; else fetch by resource_id."""
    payload = job.get("payload")
    if isinstance(payload, dict) and payload.get("id"):
        return payload

    rid = None
    try:
        rid = int(payload.get("resource_id")) if isinstance(payload, dict) else None
    except Exception:
        rid = None
    if rid is not None:
        try:
            return await fetch_order(rid)
        except httpx.HTTPError as e:
            logger.error("[WORKER] fetch_order(%s) failed: %s", rid, e)
    return None


def _audit_save(base: str, event: str, job: Dict[str, Any], payload_json: Dict[str, Any] | None) -> None:
    def _to_dict(obj):
        if hasattr(obj, "dict") and callable(obj.dict):
            return obj.dict()
        if hasattr(obj, "__dict__"):
            return dict(obj.__dict__)
        return obj

    # Convert payload_json if it's a custom object
    payload_serializable = _to_dict(payload_json) if payload_json is not None else None
    job_serializable = _to_dict(job) if job is not None else None
    (INBOX_DIR / f"{base}.{event}.json").write_text(
        json.dumps({"job": job_serializable, "payload": payload_serializable}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_mop_map() -> Dict[str, str]:
    raw = getattr(settings, "WOO_MODE_OF_PAYMENT_MAP", {}) or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            out: Dict[str, str] = {}
            for pair in raw.split(","):
                if ":" in pair:
                    k, v = pair.split(":", 1)
                    out[k.strip().strip('"\'{} ')] = v.strip().strip('"\'{} ')
            return out
    return {}


async def _ensure_sales_order(base: str, norm) -> str:
    cached = _read_marker(base, "so")
    if cached:
        return cached
    norm_dict = _to_dict_recursive(norm)
    #logger.info("[DEBUG] ERPNext sync payload: %r", norm_dict)
    so_name, bill_name, ship_name = await upsert_sales_order_from_woo(norm_dict)
    logger.info("[WORKER] SO ensured=%s (bill=%s ship=%s)", so_name, bill_name, ship_name)
    _write_marker(base, "so", so_name or "done")
    return so_name


async def _ensure_sales_invoice(base: str, norm, so_name: str) -> str:
    cached = _read_marker(base, "si")
    if cached:
        return cached
    si_name = await create_sales_invoice_from_so(norm, so_name)
    logger.info("[WORKER] SI ensured=%s for SO=%s", si_name, so_name)
    _write_marker(base, "si", si_name or "done")
    return si_name


async def _maybe_create_payment_entry(base: str, norm, si_name: str, *, status: str, set_paid: bool) -> str | None:
    """
    Only create PE when status == 'completed' AND set_paid == True.
    Idempotent via <base>.pe.done marker.
    """
    if status != "completed" or not set_paid:
        return None
    cached = _read_marker(base, "pe")
    if cached:
        return cached
    pe_name = await create_payment_entry(norm, si_name)
    logger.info("[WORKER] PE ensured=%s for SI=%s", pe_name, si_name)
    _write_marker(base, "pe", pe_name or "done")
    return pe_name


def _extract_paid_status(order_json: Dict[str, Any]) -> tuple[str, bool]:
    status = str(order_json.get("status") or "").lower()
    set_paid = bool(order_json.get("set_paid") is True)
    return status, set_paid


async def _find_si_name_for_order(order_id: int) -> Optional[str]:
    base = f"order-{order_id}"
    cached = _read_marker(base, "si")
    if cached:
        return cached
    return await find_sales_invoice_by_po_no(f"WOO-{order_id}")


async def _find_so_name_for_order(order_id: int) -> Optional[str]:
    base = f"order-{order_id}"
    cached = _read_marker(base, "so")
    if cached:
        return cached
    return await find_sales_order_by_po_no(f"WOO-{order_id}")


def _refund_items_to_si_items(refund: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert a Woo 'refund' object's line items to SI return item rows.
    Uses SKU for mapping; lines without SKU are skipped.
    qty is absolute; rate derived from |total|/qty.
    """
    items_out: List[Dict[str, Any]] = []
    for li in (refund.get("line_items") or []):
        sku = (li.get("sku") or "").strip()
        if not sku:
            continue
        # Woo refunds typically use negative totals and negative quantities; normalize
        try:
            qty = abs(float(li.get("quantity") or 0.0))
        except Exception:
            qty = 0.0
        total_raw = li.get("total") or "0"
        try:
            total = abs(float(str(total_raw)))
        except Exception:
            total = 0.0
        rate = (total / qty) if qty else 0.0
        items_out.append({"item_code": sku, "qty": qty, "rate": rate})
    return items_out


def _to_dict_recursive(obj):
    # Pydantic v2: model_dump()
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return obj.model_dump()
    # Pydantic v1: dict()
    if hasattr(obj, "dict") and callable(obj.dict):
        return obj.dict()
    # Fallback to __dict__
    if hasattr(obj, "__dict__"):
        out = {}
        for k, v in obj.__dict__.items():
            out[k] = _to_dict_recursive(v)
        return out
    if isinstance(obj, list):
        return [_to_dict_recursive(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _to_dict_recursive(v) for k, v in obj.items()}
    return obj


# ---------------------------
# Order Handlers
# ---------------------------

async def _handle_woo_order_created(job: Dict[str, Any]) -> None:
    """
    Creation path:
      - Normalize
      - Ensure SO (idempotent)
      - Ensure SI (idempotent)
      - Optionally ensure PE if (completed & set_paid=True)
    """
    base = _base_key(job)
    order_json = await _load_order_from_job(job)
    _audit_save(base, "order.created", job, order_json)

    if not isinstance(order_json, dict):
        logger.warning("[WORKER] no usable order payload; base=%s", base)
        return

    norm = normalize_order(order_json)

    # Warn about missing SKUs (we skipped in normalizer)
    missing_skus = sum(1 for li in (order_json.get("line_items") or []) if not (li.get("sku") or "").strip())
    if missing_skus:
        logger.warning("[WORKER] order id=%s: %d line(s) missing SKU; skipped", norm.order_id, missing_skus)

    so_name = await _ensure_sales_order(base, _to_dict_recursive(norm))
    si_name = await _ensure_sales_invoice(base, norm, so_name)

    status, set_paid = _extract_paid_status(order_json)
    await _maybe_create_payment_entry(base, norm, si_name, status=status, set_paid=set_paid)


async def _handle_woo_order_updated(job: Dict[str, Any]) -> None:
    """
    Update path:
      - Normalize (fresh payload)
      - Ensure SO & SI again (safe idempotent)
      - If now (completed & set_paid=True) → ensure PE
      - Handle cancellations:
         * cancelled & not paid → cancel SI and SO
         * cancelled & paid → create SI Return + refund PE
      - Discover refunds on any update and enqueue if not processed
    """
    # Debug log: print raw payload and parsed status for tracing
    #logger.info(f"[DEBUG] Raw payload: {job.get('payload')}")
    try:
        status_dbg = None
        # Try to extract status from payload
        if isinstance(job.get("payload"), dict):
            payload = job["payload"]
            # If payload has 'body_preview', log it
            if 'body_preview' in payload:
                logger.info(f"[DEBUG] body_preview: {payload['body_preview']}")
            # If payload has 'body_b64', decode and log status
            if 'body_b64' in payload:
                import base64, json
                try:
                    decoded = base64.b64decode(payload['body_b64']).decode('utf-8')
                    logger.info(f"[DEBUG] body_b64 decoded: {decoded[:200]}")
                    body_json = json.loads(decoded)
                    status_dbg = body_json.get('status')
                    logger.info(f"[DEBUG] Parsed status from body_b64: {status_dbg}")
                except Exception as e:
                    logger.error(f"[DEBUG] Failed to decode body_b64: {e}")
            # Fallback: try to get status directly
            if not status_dbg and 'status' in payload:
                status_dbg = payload['status']
                logger.info(f"[DEBUG] Parsed status from payload: {status_dbg}")
        else:
            logger.warning(f"[DEBUG] Payload is not a dict: {type(job.get('payload'))}")
        # If status is still None, log warning
        if not status_dbg:
            logger.warning("[DEBUG] Could not parse status from payload")
    except Exception as e:
        logger.error(f"[DEBUG] Exception during status parsing: {e}")

    base = _base_key(job)
    order_json = await _load_order_from_job(job)
    _audit_save(base, "order.updated", job, order_json)

    if not isinstance(order_json, dict):
        logger.warning("[WORKER] no usable order payload; base=%s", base)
        return

    norm = normalize_order(order_json)
    order_id = int(order_json.get("id") or 0)

    # --- ERPNext sync rules based on WooCommerce status ---
    status = (order_json.get("status") or "").lower()
    set_paid = bool(order_json.get("date_paid"))
    so_name = await _ensure_sales_order(base, _to_dict_recursive(norm))
    si_name = None
    pe_name = None

    logger.info(f"[ERPNEXT ACTION] Status call is: {status}")
    if status == "processing":
        # 1. Submit Sales Order only
        pass

    elif status == "completed":
        # 2. Submit SO, create SI, create PE
        si_name = await _ensure_sales_invoice(base, norm, so_name)
        pe_name = await _maybe_create_payment_entry(base, norm, si_name, status=status, set_paid=True)
        if pe_name:
            logger.info("[WORKER] Payment Entry ensured on completed: %s", pe_name)

    elif status == "on-hold":
        # 3. Keep SO in draft or submitted, do not invoice or mark as paid
        pass

    elif status == "cancelled":
        # 4. Cancel SO if submitted, cancel SI/PE if they exist
        # Add concise logging and ensure SO is submitted before cancellation
        #logger.info(f"[CANCEL] Cancellation branch triggered for order id={order_id}, status={status}")
        try:
            if si_name:
                logger.info(f"[CANCEL] Attempting to cancel Sales Invoice: {si_name}")
                await cancel_sales_invoice(si_name)
                logger.info(f"[CANCEL] Sales Invoice cancelled: {si_name}")
            else:
                logger.info("[CANCEL] No Sales Invoice found to cancel.")
        except Exception as e:
            logger.error(f"[CANCEL] SI cancel failed ({si_name}): {e}")
        try:
            if so_name:
                logger.info("[CANCEL] Attempting to cancel Sales Order: %s", so_name)
                # Ensure SO is submitted before cancellation
                from app.erp.erp_orders import get_sales_order_status, submit_sales_order
                so_status = await get_sales_order_status(so_name)
                if so_status == 0:
                    logger.info("[CANCEL] Sales Order %s is in draft. Submitting before cancellation.", so_name)
                    await submit_sales_order(so_name)
                await cancel_sales_order(so_name)
                logger.info("[CANCEL] Sales Order cancelled: %s", so_name)
            else:
                logger.info("[CANCEL] No Sales Order found to cancel.")
        except Exception as e:
            logger.error(f"[CANCEL] SO cancel failed ({so_name})")
        return

    # Also discover refunds on non-cancel updates (partial refunds)
    # Write status to file only if not cancelled
    if status != "cancelled":
        try:
            refunds = await fetch_order_refunds(order_id)
            for r in refunds or []:
                rid = int(r.get("id"))
                if not _refund_marker_exists("si_return", rid) or not _refund_marker_exists("pe", rid):
                    await enqueue_job({
                        "type": "woo.refund.created",
                        "topic": "refund.created",
                        "resource": "refund",
                        "event": "created",
                        "payload": r,
                        "order_id": order_id,
                        "raw_len": 0,
                    })
        except Exception as e:
            logger.debug("[WORKER] refund discovery on order.update failed for %s: %s", order_id, e)


# ---------------------------
# ---------------------------

async def _handle_woo_refund_created(job: Dict[str, Any]) -> None:
    """
    Process a Woo refund:
      - Create SI Return (credit note) against the original SI
      - Create refund Payment Entry (customer payment out)
    Idempotent via refund-<id>.si_return.done and refund-<id>.pe.done markers.
    """
    base = _base_key(job)  # refund-<id>
    payload = job.get("payload") or {}
    _audit_save(base, "refund.created", job, payload)

    if not isinstance(payload, dict) or not payload.get("id"):
        logger.warning("[WORKER] no usable refund payload; base=%s", base)
        return

    refund_id = int(payload.get("id"))
    order_id = int(payload.get("order_id") or job.get("order_id") or 0)
    if not order_id:
        logger.warning("[WORKER] refund %s missing order_id; skipping", refund_id)
        return

    # Discover SI for this order
    si_name = await _find_si_name_for_order(order_id)
    if not si_name:
        logger.warning("[WORKER] refund %s cannot find SI for order %s; skipping", refund_id, order_id)
        return

    # 1) SI Return
    ret_name: Optional[str] = _read_marker(f"refund-{refund_id}", "si_return")
    if not ret_name:
        items = _refund_items_to_si_items(payload)
        if not items:
            logger.warning("[WORKER] refund %s has no SKU items; skipping SI Return", refund_id)
        else:
            try:
                ret_name = await create_sales_invoice_return(
                    si_name=si_name,
                    return_items=items,
                    posting_date=(payload.get("date_created_gmt") or payload.get("date_created") or None),
                    update_stock=False,
                )
                _write_refund_marker("si_return", refund_id, ret_name or "done")
                logger.info("[WORKER] SI Return %s created for refund %s (order %s)", ret_name, refund_id, order_id)
            except Exception as e:
                logger.exception("[WORKER] create SI Return failed for refund %s: %s", refund_id, e)
                return

    # 2) Refund PE
    pe_done = _refund_marker_exists("pe", refund_id)
    if not pe_done and ret_name:
        # Map MoP: prefer refund.payload.payment_method, else order.payment_method
        mop_map = _parse_mop_map()
        gw = (payload.get("payment_method") or "").strip().lower()
        if not gw:
            try:
                order = await fetch_order(order_id)
                gw = (order.get("payment_method") or "").strip().lower()
            except Exception:
                gw = ""
        mop = mop_map.get(gw) or mop_map.get("default") or "Bank"
        ref_no = f"WOO-REFUND-{refund_id}"
        ref_date = (payload.get("date_created_gmt") or payload.get("date_created") or None)
        try:
            pe_name = await create_refund_payment_entry(
                si_return_name=ret_name,
                mode_of_payment=mop,
                reference_no=ref_no,
                reference_date=ref_date,
            )
            _write_refund_marker("pe", refund_id, pe_name or "done")
            logger.info("[WORKER] Refund PE %s created for refund %s (order %s)", pe_name, refund_id, order_id)
        except Exception as e:
            logger.exception("[WORKER] refund PE failed for refund %s: %s", refund_id, e)


# ---------------------------
# Customer Handlers
# ---------------------------

async def _handle_woo_customer_event(job: Dict[str, Any]) -> None:
    """
    Upsert ERPNext Customer (+ Contact + Address) from a Woo customer payload.
    Idempotent via lookup by email, then by name. Writes a 'cust' marker.
    """
    base = _base_key(job)  # will prefix 'customer-<id>'
    payload = job.get("payload") or {}
    _audit_save(base, "customer.event", job, payload)

    if not isinstance(payload, dict) or not payload.get("id"):
        logger.warning("[WORKER] no usable customer payload; base=%s", base)
        return

    cust_name, bill_addr, ship_addr = await upsert_customer_from_woo(payload)
    logger.info("[WORKER] Customer ensured=%s (bill=%s ship=%s)", cust_name, bill_addr, ship_addr)
    _write_marker(base, "cust", cust_name or "done")
