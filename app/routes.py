#=======================================================================================
# app/routes.py
# FastAPI routes for ERPNext ↔ WooCommerce sync, utilities, and legacy compatibility.
#
# ✅ Canonical public API lives under /api/*
# ✅ NEW PIPELINE endpoints (product_sync.py) are admin-only now (HTTP Basic)
# 🧩 LEGACY PIPELINE endpoints (sync.py) kept under /api/legacy/* for comparison
#
# IMPORTANT: In main_app.py, include with NO extra prefix to avoid /api/api duplication:
#   from app.routes import router as api_router
#   app.include_router(api_router)   # <-- no prefix here
#=======================================================================================

import httpx
import secrets
from fastapi import APIRouter, Query, Request, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import JSONResponse

from app.config import settings

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

# ---------------------------
# HTTP Basic for NEW pipeline
# ---------------------------
security = HTTPBasic()

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username or "", settings.ADMIN_USER or "")
    ok_pass = secrets.compare_digest(credentials.password or "", settings.ADMIN_PASS or "")
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )

# ----------------------------------------------------------------------
# NEW PIPELINE (product_sync.py) — ✅ KEEP (now requires HTTP Basic)
# ----------------------------------------------------------------------

@router.api_route("/sync/preview", methods=["GET", "POST"], dependencies=[Depends(verify_admin)])
async def api_sync_preview():
    """
    Dry-run preview of ERPNext → Woo sync (admin-only).
    """
    result = await sync_preview()
    return JSONResponse(content=result)

@router.post("/sync/full", dependencies=[Depends(verify_admin)])
async def api_sync_full(request: Request):
    """
    Full ERPNext → Woo sync (admin-only).
    Body: { "dry_run": bool, "purge_bin": bool (default True) }
    """
    payload = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
    dry_run = bool(payload.get("dry_run", False))
    purge_bin = bool(payload.get("purge_bin", True))
    result = await sync_products_full(dry_run=dry_run, purge_bin=purge_bin)
    return JSONResponse(content=result)

@router.post("/sync/partial", dependencies=[Depends(verify_admin)])
async def api_sync_partial(request: Request):
    """
    Partial sync by SKUs (admin-only).
    Body: { "skus": [ "SKU1", ... ], "dry_run": bool }
    """
    payload = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
    skus = payload.get("skus", [])
    dry_run = bool(payload.get("dry_run", False))
    result = await sync_products_partial(skus_to_sync=skus, dry_run=dry_run)
    return JSONResponse(content=result)

# ----------------------------------------------------------------------
# LEGACY PIPELINE (sync.py) — 🟠 DEPRECATE SOON (left open for now)
# ----------------------------------------------------------------------

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

# ----------------------------------------------------------------------
# WooCommerce utilities — ✅ KEEP
# ----------------------------------------------------------------------

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

# ----------------------------------------------------------------------
# ERPNext healthcheck — ✅ KEEP
# ----------------------------------------------------------------------

@router.get("/erpnext/ping")
async def ping_erpnext():
    """Healthcheck: ERPNext credentials + API reachability."""
    return await erpnext_ping()

@router.get("/health")
async def api_health():
    """
    Health check used by the Admin UI:
    - ERPNext: GET {ERP_URL}/api/method/ping   (expects 200)
    - WordPress: GET {WP_API_URL or WC_BASE_URL/wp-json}   (expects 200)
    - Optionally pings Woo REST root to confirm reachability (auth may not be required)
    """
    erp_url = (getattr(settings, "ERP_URL", "") or "").strip()
    wc_base = (getattr(settings, "WC_BASE_URL", "") or "").strip()
    wp_api = (getattr(settings, "WP_API_URL", None) or (wc_base.rstrip("/") + "/wp-json") if wc_base else None)

    result = {
        "ok": True,
        "integration": {"ok": True},
        "erpnext": {},
        "woocommerce": {},
    }

    async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
        # ---- ERPNext ping ----
        if not erp_url:
            result["erpnext"] = {"ok": False, "error": "ERP_URL not set"}
            result["ok"] = False
        else:
            erp_ping_url = erp_url.rstrip("/") + "/api/method/ping"
            try:
                r = await client.get(erp_ping_url)
                erp_ok = (r.status_code == 200)
                result["erpnext"] = {
                    "ok": erp_ok,
                    "status": r.status_code,
                    "url": erp_ping_url,
                }
                result["ok"] = result["ok"] and erp_ok
            except Exception as e:
                result["erpnext"] = {"ok": False, "error": str(e), "url": erp_ping_url}
                result["ok"] = False

        # ---- WordPress / Woo reachability ----
        if not wp_api:
            result["woocommerce"] = {"ok": False, "error": "WP_API_URL and WC_BASE_URL not set"}
            result["ok"] = False
        else:
            try:
                r = await client.get(wp_api.rstrip("/"))
                wp_ok = (r.status_code == 200)
                result["woocommerce"] = {
                    "ok": wp_ok,
                    "status": r.status_code,
                    "url": wp_api.rstrip("/"),
                }
                # Optional: ping Woo REST root (no auth required to just confirm route)
                if wc_base:
                    try:
                        r2 = await client.get(wc_base.rstrip("/") + "/wp-json/wc/v3")
                        result["woocommerce"]["rest_status"] = r2.status_code
                    except Exception as ee:
                        result["woocommerce"]["rest_status"] = None
                        result["woocommerce"]["rest_error"] = str(ee)
                result["ok"] = result["ok"] and wp_ok
            except Exception as e:
                result["woocommerce"] = {"ok": False, "error": str(e), "url": wp_api}
                result["ok"] = False

    return JSONResponse(content=result)