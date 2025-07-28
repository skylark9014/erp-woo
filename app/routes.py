"""
routes.py
---------
FastAPI route definitions for middleware operations.
Provides endpoints to trigger and monitor sync actions between ERPNext and WooCommerce.
"""

from fastapi import APIRouter
from .sync import sync_products

router = APIRouter()

@router.post("/sync")
async def trigger_sync():
    """
    API endpoint to manually trigger a product synchronization.

    Returns:
        dict: Status/result of the synchronization process.
    """
    result = await sync_products()
    return result
