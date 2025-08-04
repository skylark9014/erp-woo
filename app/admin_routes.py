# app/admin_routes.py
# ===========================
# FastAPI routes for Admin Panel AJAX
# ===========================

from fastapi import APIRouter, Request, Depends, Body, HTTPException, status
from fastapi.responses import JSONResponse
from app.mapping.mapping_store import build_or_load_mapping, save_mapping_file
from app.sync.sync import sync_products_preview, sync_products

import logging
logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/admin/api", tags=["Admin API"])


# --- GET Mapping File ---
@router.get("/mapping")
async def get_mapping():
    try:
        mapping = build_or_load_mapping()
        return {"mapping": mapping}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load mapping: {str(e)}")

# --- POST Mapping File (Save) ---
@router.post("/mapping")
async def save_mapping(payload: dict = Body(...)):
    try:
        mapping = payload.get("mapping")
        if not isinstance(mapping, list):
            raise ValueError("Mapping must be a list of dicts")
        save_mapping_file(mapping)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save mapping: {str(e)}")

# --- GET Sync Preview ---
@router.get("/preview-sync")
async def get_sync_preview():
    try:
        preview = await sync_products_preview()
        # The function should return a list of dicts:
        # [{erp_item_code, wc_sku, action, fields_to_update, images_changed}, ...]
        return {"preview": preview}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview failed: {str(e)}")

# --- POST Full Sync ---
@router.post("/full-sync")
async def post_full_sync():
    try:
        result = await sync_products()
        # Optionally: result = {summary, details, ...}
        return {"ok": True, "result": result}
    except Exception as e:
        # this will print the full stack to your logs
        logger.exception("Error in full-sync")
        # now still return the same HTTP error to the client
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")

from fastapi.responses import JSONResponse

# --- Stock Adjustment Function ---
@router.get("/stock-adjustment")
async def get_stock_adjustment():
    return JSONResponse(content={}, status_code=200)
