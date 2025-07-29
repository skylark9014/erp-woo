#=======================================================================================
# app/routes.py
# FastAPI route definitions for middleware operations.
# Provides endpoints to trigger and monitor sync actions between ERPNext and WooCommerce
#=======================================================================================

from fastapi import APIRouter, Query

from app.sync import (
    sync_products,
    sync_products_preview,
    sync_categories,
    # sync_product_images,  # If you have a standalone image sync, otherwise remove/comment
)
from app.woocommerce import (
    purge_wc_bin_products,
    purge_all_wc_products,
    purge_wc_product_variations,
    list_wc_bin_products,
)
from app.erpnext import erpnext_ping

router = APIRouter()


@router.post("/sync/run")
async def run_sync():
    """Run a one-way ERPNext → WooCommerce sync (creates/updates Woo products)."""
    return await sync_products()


@router.get("/sync/preview")
async def preview_sync():
    """Preview ERPNext → WooCommerce product sync."""
    return await sync_products_preview()


@router.post("/sync/categories")
async def run_category_sync():
    """Sync ERPNext item groups (categories) to WooCommerce."""
    return await sync_categories()


@router.post("/woocommerce/purge-bin")
async def purge_woocommerce_bin():
    """Purges (force-deletes) all WooCommerce products in the BIN (Trash)."""
    return await purge_wc_bin_products()


@router.post("/woocommerce/purge-all")
async def purge_woocommerce_all():
    """Permanently deletes ALL WooCommerce products. Use with caution!"""
    return await purge_all_wc_products()


@router.post("/woocommerce/purge-variations")
async def purge_woocommerce_variations(product_id: int = Query(...)):
    """Delete all variations for a given WooCommerce product (by product_id)."""
    return await purge_wc_product_variations(product_id)


@router.get("/woocommerce/list-bin")
async def list_woocommerce_bin():
    """Lists all WooCommerce products currently in the BIN (Trash)."""
    return await list_wc_bin_products()


@router.get("/erpnext/ping")
async def ping_erpnext():
    """ERPNext healthcheck (validates credentials and API reachability)."""
    return await erpnext_ping()


# REMOVE this if sync_product_images is not used anymore.
# @router.post("/sync/images")
# async def run_image_sync():
#     """Sync ERPNext product images to WooCommerce."""
#     return await sync_product_images()


@router.post("/sync/full")
async def full_sync():
    """One-step full ERPNext → WooCommerce sync: - Syncs products (create/update/price/category) - Returns summary."""
    prod_result = await sync_products()
    # img_result = await sync_product_images()  # Remove or uncomment as needed
    return {
        "products": prod_result,
        # "images": img_result
    }
