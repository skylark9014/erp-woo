#=======================================================================================
# app/admin_routes.py
# Admin endpoints. These are protected via Basic Auth in main_app.py.
# and mounted under /admin, so final paths are /admin/api/*.
#
# NOTE:
# - We intentionally DO NOT define /api/sync/* here to avoid collisions with public
#   endpoints from app.routes (new pipeline).
# - Admin UI should call the public /api/sync/* endpoints directly for sync actions.
# - This module focuses on admin-only operations: mapping edit, preview file management.
#=======================================================================================

import logging
import httpx
import json, os, time

from typing import Any
from pydantic import BaseModel
from pathlib import Path
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse
from app.mapping_store import build_or_load_mapping, save_mapping_file
from app.sync.sync import (
    sync_products as legacy_sync_products,
    sync_products_preview as legacy_sync_products_preview,
    sync_products_partial as legacy_sync_products_partial,
)
from app.config import settings

logger = logging.getLogger("uvicorn.error")

# NOTE: this router already has prefix="/api". In main_app we mount it with prefix="/admin",
# so final paths are /admin/api/*
router = APIRouter(prefix="/api", tags=["Admin API"])

# ------------------------------------------------------------------------------
# Health for Admin UI — ✅ NEW
# ------------------------------------------------------------------------------

@router.get("/integration/health")
async def admin_integration_health():
    """
    Admin-only health check used by the UI. Verifies reachability of ERPNext and WP/Woo.
    Returns 200 with per-target status, does NOT require secrets to succeed.
    """
    erp_url = (settings.ERP_URL or "").rstrip("/")
    wc_base = (settings.WC_BASE_URL or "").rstrip("/")
    wp_url = (getattr(settings, "WP_API_URL", "") or f"{wc_base}/wp-json").rstrip("/")

    checks = {}

    async def _ok(resp: httpx.Response | None, *, allow_status: set[int]) -> dict:
        if resp is None:
            return {"ok": False, "status": None}
        return {"ok": resp.status_code in allow_status, "status": resp.status_code}

    timeout = httpx.Timeout(10.0, connect=10.0, read=10.0)
    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
        # ERPNext ping endpoint (exists on ERPNext)
        erp_resp = None
        try:
            if erp_url:
                erp_resp = await client.get(f"{erp_url}/api/method/ping")
        except Exception as e:
            logger.debug("ERPNext ping failed: %s", e)
        checks["erpnext"] = await _ok(erp_resp, allow_status={200})

        # WordPress REST root
        wp_resp = None
        try:
            if wp_url:
                wp_resp = await client.get(wp_url)
        except Exception as e:
            logger.debug("WordPress REST root failed: %s", e)
        # 200 is typical; some setups may 401 with auth, both prove reachability
        checks["wordpress"] = await _ok(wp_resp, allow_status={200, 401})

        # WooCommerce REST namespace (presence implies plugin active; 200/401/403/404 are acceptable)
        wc_resp = None
        try:
            if wp_url:
                wc_resp = await client.get(f"{wp_url}/wc/v3")
        except Exception as e:
            logger.debug("Woo REST check failed: %s", e)
        checks["woocommerce"] = await _ok(wc_resp, allow_status={200, 401, 403, 404})

    ok = all(v.get("ok") for v in checks.values())
    return {"ok": ok, "checks": checks, "base": {"erp": erp_url, "wp": wp_url, "wc": wc_base}}

# ------------------------------------------------------------------------------
# Mapping file — ✅ KEEP
# ------------------------------------------------------------------------------

@router.get("/mapping")
def get_mapping():
    """Admin: Load mapping file."""
    try:
        mapping = build_or_load_mapping()
        return {"mapping": mapping}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load mapping: {str(e)}")

@router.post("/mapping")
def post_mapping(payload: dict = Body(...)):
    """Admin: Save mapping file."""
    try:
        mapping = payload.get("mapping")
        if not isinstance(mapping, list):
            raise ValueError("Mapping must be a list of dicts")
        save_mapping_file(mapping)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save mapping: {str(e)}")

# ------------------------------------------------------------------------------
# LEGACY PIPELINE Admin shims — 🟠 DEPRECATE SOON
# ------------------------------------------------------------------------------

@router.get("/legacy/sync/preview")
async def admin_legacy_preview_sync():
    """Admin → LEGACY PIPELINE: Preview (old)."""
    try:
        preview = await legacy_sync_products_preview()
        return {"preview": preview}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview failed: {str(e)}")

@router.post("/legacy/sync/full")
async def admin_legacy_full_sync():
    """Admin → LEGACY PIPELINE: Full sync (old)."""
    try:
        result = await legacy_sync_products()
        return {"ok": True, "result": result}
    except Exception as e:
        logger.exception("Error in legacy full-sync")
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")

@router.post("/legacy/sync/partial")
async def admin_legacy_partial_sync():
    """Admin → LEGACY PIPELINE: Partial sync (old). Signature may differ."""
    result = await legacy_sync_products_partial()
    return {"ok": True, "result": result}

# ------------------------------------------------------------------------------
# Misc Admin — ✅ KEEP (if you still need it)
# ------------------------------------------------------------------------------

@router.get("/stock-adjustment")
def get_stock_adjustment():
    """Admin: Placeholder stock-adjustment endpoint (no-op)."""
    return JSONResponse(content={}, status_code=200)

# ------------------------------------------------------------------------------
# Misc Admin — ✅ Shipping Parameters Edit Functions
# ------------------------------------------------------------------------------

@router.get("/config/shipping/params")
async def get_shipping_params():
    """
    Return the current shipping_prams.json content + metadata.
    Always returns text content; also includes parsed JSON when valid.
    """
    p = Path(settings.SHIPPING_PARAMS_PATH)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")

    text = p.read_text(encoding="utf-8")
    valid = True
    parsed = None
    error = None
    try:
        parsed = json.loads(text)
    except Exception as e:
        valid = False
        error = str(e)

    st = p.stat()
    return {
        "ok": True,
        "path": str(p),
        "valid": valid,
        "error": error,
        "mtime": int(st.st_mtime),
        "size": st.st_size,
        "content": text,
        "json": parsed,
    }

class ShippingParamsUpsert(BaseModel):
    # You can send either raw string "content" OR already-parsed "data".
    content: str | None = None
    data: Any | None = None
    pretty: bool | None = True
    sort_keys: bool | None = True

def _atomic_write(path: Path, data: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)  # atomic on POSIX

@router.put("/config/shipping/params")
async def put_shipping_params(payload: ShippingParamsUpsert = Body(...)):
    """
    Save new shipping_prams.json. Validates JSON first, writes atomically,
    and creates a timestamped .bak of the previous file if present.
    """
    p = Path(settings.SHIPPING_PARAMS_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)

    # Determine the object to store
    if payload.content is None and payload.data is None:
        raise HTTPException(status_code=400, detail="Provide either 'content' (string) or 'data' (object).")

    if payload.data is None:
        # Parse the provided text as JSON
        try:
            obj = json.loads(payload.content or "")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    else:
        obj = payload.data

    # Serialize (pretty by default)
    indent = 2 if (payload.pretty is not False) else None
    try:
        new_text = json.dumps(obj, ensure_ascii=False, indent=indent, sort_keys=(payload.sort_keys is not False))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not serialize JSON: {e}")

    # Backup existing file
    if p.exists():
        ts = time.strftime("%Y%m%d-%H%M%S")
        bak = p.with_suffix(p.suffix + f".{ts}.bak")
        try:
            bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            # Non-fatal; continue save even if backup fails
            pass

    # Atomic write
    _atomic_write(p, new_text)

    st = p.stat()
    return {
        "ok": True,
        "path": str(p),
        "mtime": int(st.st_mtime),
        "size": st.st_size,
        "content": new_text,
    }