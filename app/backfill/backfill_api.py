# -------------------------------
# app/backfill/backfill_api.py:
# -------------------------------
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Body, Path, Query

from app.workers.jobs_worker import enqueue_job
from app.config import settings

logger = logging.getLogger("uvicorn.error")

# All endpoints live under /admin/integration/*
# Protect them in main_app.py by including this router with Depends(verify_admin)
router = APIRouter(prefix="/admin/integration", tags=["Admin • Backfill & Ops"])


# ------------------------
# Helpers
# ------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _parse_iso_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Accept both "Z" and "+00:00"
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _wc_base() -> str:
    base = (settings.WC_BASE_URL or "").rstrip("/")
    if not base:
        raise RuntimeError("WC_BASE_URL not configured")
    return base

def _wc_params(extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "consumer_key": settings.WC_API_KEY or "",
        "consumer_secret": settings.WC_API_SECRET or "",
        **(extra or {}),
    }

async def _wc_get_list(path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    url = f"{_wc_base()}/wp-json/wc/v3{path}"
    async with httpx.AsyncClient(timeout=45.0, verify=False) as client:
        r = await client.get(url, params=_wc_params(params))
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected Woo response for {path}: {data!r}")
        return data

async def _wc_get_one(path: str, params: Dict[str, Any] | None = None) -> Optional[Dict[str, Any]]:
    url = f"{_wc_base()}/wp-json/wc/v3{path}"
    async with httpx.AsyncClient(timeout=45.0, verify=False) as client:
        r = await client.get(url, params=_wc_params(params or {}))
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected Woo response for {path}: {data!r}")
        return data

# -------- Orders (supports 'after'/'before') --------

async def _fetch_orders_after(
    after: datetime,
    before: Optional[datetime],
    status: Optional[str],
    *,
    per_page: int = 50,
    max_pages: int = 20,
) -> List[Dict[str, Any]]:
    orders: List[Dict[str, Any]] = []
    page = 1
    params: Dict[str, Any] = {
        "per_page": min(max(per_page, 1), 100),
        "orderby": "date",
        "order": "asc",
        "after": _iso_z(after),
    }
    if before:
        params["before"] = _iso_z(before)
    if status and status != "any":
        params["status"] = status

    while page <= max_pages:
        batch = await _wc_get_list("/orders", {**params, "page": page})
        orders.extend(batch)
        logger.info("[BACKFILL] orders page=%s size=%s total=%s", page, len(batch), len(orders))
        if len(batch) < params["per_page"]:
            break
        page += 1
    return orders

# -------- Customers (Woo endpoint does NOT accept 'after'/'before') --------

def _parse_wc_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def _customer_is_in_range(c: Dict[str, Any], after: datetime, before: Optional[datetime]) -> bool:
    # Prefer created_gmt, fall back to created, then modified_gmt/modified
    for key in ("date_created_gmt", "date_created", "date_modified_gmt", "date_modified"):
        dt = _parse_wc_dt(c.get(key))
        if dt:
            if dt < after:
                return False
            if before and dt > before:
                return False
            return True
    # If no dates, keep it (can't decide)
    return True

async def _fetch_customers_after(
    after: datetime,
    before: Optional[datetime],
    *,
    per_page: int = 50,
    max_pages: int = 20,
) -> List[Dict[str, Any]]:
    """
    Woo /customers does not support 'after'/'before' filters (400).
    We paginate and filter client-side by date_created(_gmt)/date_modified(_gmt).
    """
    customers: List[Dict[str, Any]] = []
    page = 1
    params: Dict[str, Any] = {
        "per_page": min(max(per_page, 1), 100),
        # Keep ordering simple/compatible; not all installs support 'registered_date'
        "orderby": "id",
        "order": "asc",
    }

    while page <= max_pages:
        batch = await _wc_get_list("/customers", {**params, "page": page})
        logger.info("[BACKFILL] customers page=%s size=%s", page, len(batch))
        # Client-side time window filter
        for c in batch:
            if _customer_is_in_range(c, after, before):
                customers.append(c)
        if len(batch) < params["per_page"]:
            break
        page += 1
    return customers


# ------------------------
# Endpoints
# ------------------------

@router.post("/backfill/orders")
async def backfill_orders(
    body: Dict[str, Any] = Body(
        default={
            "since": None,     # ISO8601 string, defaults to last 30 days
            "until": None,     # ISO8601 string, optional upper bound
            "status": "any",   # "any", "processing", "completed", etc. (Woo status)
            "per_page": 50,
            "max_pages": 20,
            "enqueue_as": "updated",  # "created" or "updated" — choose job type
        }
    )
) -> Dict[str, Any]:
    since_dt = _parse_iso_dt(body.get("since")) or (_now_utc() - timedelta(days=30))
    until_dt = _parse_iso_dt(body.get("until")) if body.get("until") else None
    status = body.get("status") or "any"
    per_page = int(body.get("per_page") or 50)
    max_pages = int(body.get("max_pages") or 20)
    job_kind = str(body.get("enqueue_as") or "updated").strip().lower()
    if job_kind not in {"created", "updated"}:
        job_kind = "updated"

    orders = await _fetch_orders_after(since_dt, until_dt, status, per_page=per_page, max_pages=max_pages)

    enqueued = 0
    for o in orders:
        jtype = f"woo.order.{job_kind}"
        await enqueue_job({
            "type": jtype,               # e.g., "woo.order.updated"
            "topic": f"order.{job_kind}",
            "resource": "order",
            "event": job_kind,
            "delivery_id": None,
            "webhook_id": None,
            "payload": o,
            "raw_len": 0,
        })
        enqueued += 1

    return {
        "ok": True,
        "since": _iso_z(since_dt),
        "until": _iso_z(until_dt) if until_dt else None,
        "status_filter": status,
        "count": enqueued,
        "job_type": f"woo.order.{job_kind}",
    }


@router.post("/backfill/customers")
async def backfill_customers(
    body: Dict[str, Any] = Body(
        default={
            "since": None,      # ISO8601, defaults to last 30 days
            "until": None,      # ISO8601
            "per_page": 50,
            "max_pages": 20,
            "enqueue_as": "updated",  # "created" or "updated"
        }
    )
) -> Dict[str, Any]:
    since_dt = _parse_iso_dt(body.get("since")) or (_now_utc() - timedelta(days=30))
    until_dt = _parse_iso_dt(body.get("until")) if body.get("until") else None
    per_page = int(body.get("per_page") or 50)
    max_pages = int(body.get("max_pages") or 20)
    job_kind = str(body.get("enqueue_as") or "updated").strip().lower()
    if job_kind not in {"created", "updated"}:
        job_kind = "updated"

    customers = await _fetch_customers_after(since_dt, until_dt, per_page=per_page, max_pages=max_pages)

    enqueued = 0
    for c in customers:
        jtype = f"woo.customer.{job_kind}"
        await enqueue_job({
            "type": jtype,
            "topic": f"customer.{job_kind}",
            "resource": "customer",
            "event": job_kind,
            "delivery_id": None,
            "webhook_id": None,
            "payload": c,
            "raw_len": 0,
        })
        enqueued += 1

    return {
        "ok": True,
        "since": _iso_z(since_dt),
        "until": _iso_z(until_dt) if until_dt else None,
        "count": enqueued,
        "job_type": f"woo.customer.{job_kind}",
        "note": "Customer jobs enqueue successfully; ensure jobs_worker has handlers for customer.*",
    }


@router.post("/backfill/order/{order_id}")
async def backfill_single_order(
    order_id: int = Path(..., ge=1),
    as_kind: str = Query("updated", pattern="^(created|updated)$")
) -> Dict[str, Any]:
    # Fetch single order via /orders/{id}
    o = await _wc_get_one(f"/orders/{order_id}")
    if not o:
        return {"ok": False, "error": f"Order {order_id} not found"}

    jtype = f"woo.order.{as_kind}"
    await enqueue_job({
        "type": jtype,
        "topic": f"order.{as_kind}",
        "resource": "order",
        "event": as_kind,
        "delivery_id": None,
        "webhook_id": None,
        "payload": o,
        "raw_len": 0,
    })
    return {"ok": True, "enqueued": 1, "job_type": jtype, "order_id": order_id}


@router.post("/backfill/refunds")
async def backfill_refunds(
    body: Dict[str, Any] = Body(
        default={
            "since": None,      # ISO8601, defaults to last 30 days
            "until": None,      # ISO8601
            "status": "any",    # filter orders by status first (usually "any")
            "per_page": 50,
            "max_pages": 20,
        }
    )
) -> Dict[str, Any]:
    """
    Scans orders in a window and enqueues each refund as `woo.refund.created`.
    """
    since_dt = _parse_iso_dt(body.get("since")) or (_now_utc() - timedelta(days=30))
    until_dt = _parse_iso_dt(body.get("until")) if body.get("until") else None
    status = body.get("status") or "any"
    per_page = int(body.get("per_page") or 50)
    max_pages = int(body.get("max_pages") or 20)

    orders = await _fetch_orders_after(since_dt, until_dt, status, per_page=per_page, max_pages=max_pages)

    total_refunds = 0
    for o in orders:
        oid = int(o.get("id"))
        # GET /orders/{id}/refunds
        path = f"/orders/{oid}/refunds"
        try:
            refunds = await _wc_get_list(path, {"per_page": 100})
        except Exception as e:
            logger.warning("[BACKFILL] refunds fetch failed for order %s: %s", oid, e)
            continue
        for r in refunds or []:
            await enqueue_job({
                "type": "woo.refund.created",
                "topic": "refund.created",
                "resource": "refund",
                "event": "created",
                "delivery_id": None,
                "webhook_id": None,
                "payload": r,
                "order_id": oid,
                "raw_len": 0,
            })
            total_refunds += 1

    return {
        "ok": True,
        "since": _iso_z(since_dt),
        "until": _iso_z(until_dt) if until_dt else None,
        "orders_scanned": len(orders),
        "refunds_enqueued": total_refunds,
        "job_type": "woo.refund.created",
    }
