#=======================================================================================
# app/routes.py
# FastAPI routes for ERPNext ↔ WooCommerce sync, utilities, and legacy compatibility.
#
# ✅ Canonical public API lives under /api/*
# ✅ NEW PIPELINE endpoints (product_sync.py) are the ones to keep long-term
# 🧩 LEGACY PIPELINE endpoints (sync.py) are kept under /api/legacy/* for now
#
# IMPORTANT: In main_app.py, include with NO extra prefix to avoid /api/api duplication:
#   from app.routes import router as api_router
#   app.include_router(api_router)   # <-- no prefix here
#=======================================================================================

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

# NEW pipeline (keep)
from app.sync.product_sync import (
    sync_products_full,     # full sync with categories, prices, brand, variants, images
    sync_products_partial,  # partial sync by SKUs
    sync_preview,           # dry-run preview of new pipeline
)

# LEGACY pipeline (deprecate later)
from app.sync.sync import (
    sync_products,          # legacy "full" run
    sync_products_preview,  # legacy preview
    sync_categories as legacy_sync_categories,  # legacy category sync util
)

# Utilities
from app.woocommerce import (
    purge_wc_bin_products,
    purge_all_wc_products,
    purge_wc_product_variations,
    list_wc_bin_products,
)
from app.erpnext import erpnext_ping

router = APIRouter(prefix="/api", tags=["Sync API"])

# ------------------------------------------------------------------------------
# NEW PIPELINE (product_sync.py) — ✅ KEEP THESE
# ------------------------------------------------------------------------------

@router.api_route("/sync/preview", methods=["GET", "POST"])
async def api_sync_preview():
    """
    NEW PIPELINE (KEEP): Dry-run preview of ERPNext → Woo sync.
    - Applies all new rules: categories, brand, price list, variants, gallery images, etc.
    - Does NOT mutate Woo/ERP.
    """
    result = await sync_preview()
    return JSONResponse(content=result)

@router.post("/sync/full")
async def api_sync_full(request: Request):
    """
    NEW PIPELINE (KEEP): Full ERPNext → Woo sync.
    Body: { "dry_run": bool, "purge_bin": bool (optional, default True) }
    - If dry_run is true: no mutations (preview path); else executes changes.
    - Purges Woo bin ahead of run unless purge_bin=false.
    """
    payload = {}
    # Be tolerant of GETs or empty bodies; only parse JSON if provided
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
    dry_run = bool(payload.get("dry_run", False))
    purge_bin = bool(payload.get("purge_bin", True))
    result = await sync_products_full(dry_run=dry_run, purge_bin=purge_bin)
    return JSONResponse(content=result)

@router.post("/sync/partial")
async def api_sync_partial(request: Request):
    """
    NEW PIPELINE (KEEP): Partial sync by SKUs.
    Body: { "skus": [ "SKU1", "SKU2", ... ], "dry_run": bool }
    """
    payload = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
    skus = payload.get("skus", [])
    dry_run = bool(payload.get("dry_run", False))
    result = await sync_products_partial(skus_to_sync=skus, dry_run=dry_run)
    return JSONResponse(content=result)

# ------------------------------------------------------------------------------
# LEGACY PIPELINE (sync.py) — 🟠 DEPRECATE SOON
#   Keep these for now so you can compare outputs. Recommend removing later.
# ------------------------------------------------------------------------------

@router.post("/legacy/sync/run")
async def legacy_run_sync():
    """LEGACY (DEPRECATE): One-way ERPNext → Woo (create/update) using old pipeline."""
    return await sync_products()

@router.get("/legacy/sync/preview")
async def legacy_preview_sync():
    """LEGACY (DEPRECATE): Preview sync using old pipeline."""
    return await sync_products_preview()

@router.post("/legacy/sync/categories")
async def legacy_run_category_sync():
    """LEGACY UTILITY (DEPRECATE): Sync ERPNext Item Groups to Woo categories."""
    return await legacy_sync_categories()

# ------------------------------------------------------------------------------
# WooCommerce utilities — ✅ KEEP
# ------------------------------------------------------------------------------

@router.post("/woocommerce/purge-bin")
async def purge_woocommerce_bin():
    """Utility: Purge (force-delete) all WooCommerce products in the BIN (Trash)."""
    return await purge_wc_bin_products()

@router.post("/woocommerce/purge-all")
async def purge_woocommerce_all():
    """Utility: Permanently delete ALL WooCommerce products. ⚠️ Use with caution!"""
    return await purge_all_wc_products()

@router.post("/woocommerce/purge-variations")
async def purge_woocommerce_variations(product_id: int = Query(...)):
    """Utility: Delete all variations for a given WooCommerce product (by product_id)."""
    return await purge_wc_product_variations(product_id)

@router.get("/woocommerce/list-bin")
async def list_woocommerce_bin():
    """Utility: List all WooCommerce products currently in the BIN (Trash)."""
    return await list_wc_bin_products()

# ------------------------------------------------------------------------------
# ERPNext healthcheck — ✅ KEEP
# ------------------------------------------------------------------------------

@router.get("/erpnext/ping")
async def ping_erpnext():
    """Healthcheck: ERPNext credentials + API reachability."""
    return await erpnext_ping()
