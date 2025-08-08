#=======================================================================================
# app/admin_routes.py
# Admin endpoints. These are protected via Basic Auth in main_app.py.
#
# NOTE:
# - We intentionally DO NOT define /api/sync/* here to avoid collisions with public
#   endpoints from app.routes (new pipeline).
# - Admin UI should call the public /api/sync/* endpoints directly.
#=======================================================================================

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse
import logging

# Mapping store (KEEP)
from app.mapping_store import build_or_load_mapping, save_mapping_file

# LEGACY pipeline admin shims (KEEP for comparison; remove later)
from app.sync.sync import (
    sync_products as legacy_sync_products,
    sync_products_preview as legacy_sync_products_preview,
    sync_products_partial as legacy_sync_products_partial,
)

logger = logging.getLogger("uvicorn.error")

# Keep these under /api so they get Basic-Auth protected by main_app.secure_router()
router = APIRouter(prefix="/api", tags=["Admin API"])

# ------------------------------------------------------------------------------
# Mapping file — ✅ KEEP (Basic-Auth protected)
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
#   Keep these for now if your Admin UI still calls them directly.
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
