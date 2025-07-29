#===========================================================================
# app/erpnext.py
# ERPNext API interface module.
# Provides functions to interact with ERPNext for products and categories.
#===========================================================================

import os
import httpx
import logging
logger = logging.getLogger("uvicorn.error")

from dotenv import load_dotenv
load_dotenv()

ERP_URL = os.getenv("ERP_URL")
ERP_API_KEY = os.getenv("ERP_API_KEY")
ERP_API_SECRET = os.getenv("ERP_API_SECRET")
ERP_SELLING_PRICE_LIST = os.getenv("ERP_SELLING_PRICE_LIST", "Standard Selling")


async def get_price_map(price_list=None):
    """
    Auto-detect the current selling price list (enabled and has prices).
    Fallback: "Standard Selling".
    Returns: dict {item_code: price}
    """
    import httpx
    from app.erpnext import ERP_URL, ERP_API_KEY, ERP_API_SECRET
    headers = {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}
    
    # 1. If price_list is given, check it first
    candidate_lists = []
    if price_list:
        candidate_lists.append(price_list)
    
    # 2. Fetch all enabled selling price lists, most recent first
    url = f"{ERP_URL}/api/resource/Price List?fields=[\"name\",\"enabled\",\"selling\"]&filters=[[\"enabled\",\"=\",1],[\"selling\",\"=\",1]]&order_by=creation desc"
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.get(url, headers=headers)
        data = resp.json().get("data", []) if resp.status_code == 200 else []
        candidate_lists += [pl["name"] for pl in data]
    
    # 3. Always add Standard Selling as final fallback
    if "Standard Selling" not in candidate_lists:
        candidate_lists.append("Standard Selling")

    # 4. Try each price list until we find one with Item Price rows
    for pl_name in candidate_lists:
        # Confirm enabled
        check_url = f"{ERP_URL}/api/resource/Price List/{pl_name}?fields=[\"name\",\"enabled\"]"
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            check_resp = await client.get(check_url, headers=headers)
            enabled = check_resp.status_code == 200 and check_resp.json().get("data", {}).get("enabled", 1)
            if not enabled:
                continue
            price_url = (
                f"{ERP_URL}/api/resource/Item Price"
                f"?fields=[\"item_code\",\"price_list_rate\"]"
                f"&filters=[[\"price_list\",\"=\",\"{pl_name}\"],[\"selling\",\"=\",1]]"
                f"&limit_page_length=1000"
            )
            price_resp = await client.get(price_url, headers=headers)
            price_map = {}
            if price_resp.status_code == 200 and price_resp.json().get("data"):
                for row in price_resp.json()["data"]:
                    price_map[row["item_code"]] = row["price_list_rate"]
                if price_map:
                    logger.info(f"Using price list: {pl_name} with {len(price_map)} prices")
                    return price_map
    # No prices found anywhere
    logger.warning("No prices found in any price list, all products will have price=0")
    return {}


async def get_erpnext_items():
    """
    Fetch all items (products) from ERPNext using the REST API.
    Returns: list of ERPNext item dicts, or empty list on error.
    """
    ERP_URL = os.getenv("ERP_URL")
    ERP_API_KEY = os.getenv("ERP_API_KEY")
    ERP_API_SECRET = os.getenv("ERP_API_SECRET")
    url = (
        f"{ERP_URL}/api/resource/Item"
        "?fields=[\"item_code\",\"item_name\",\"description\",\"stock_uom\",\"standard_rate\",\"image\",\"item_group\",\"brand\"]"
        "&limit_page_length=5000"
    )
    headers = {
        "Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"
    }
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code == 200 and resp.json().get("data"):
        return resp.json()["data"]
    else:
        return []


async def get_erpnext_categories():
    """
    Fetch all item groups (categories) from ERPNext.
    Returns:
        list: List of ERPNext item group dicts.
    """
    try:
        url = f"{ERP_URL}/api/resource/Item Group?fields=[\"name\",\"parent_item_group\"]"
        headers = {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            resp = await client.get(url, headers=headers)
        return resp.json().get("data", [])
    except Exception as e:
        print("Error fetching ERPNext categories:", e)
        return []

async def erpnext_ping():
    """
    Checks if the ERPNext server is reachable and credentials work.
    Returns:
        dict: { "success": bool, "status_code": int, "data": ... }
    """
    try:
        url = f"{ERP_URL}/api/method/ping"
        headers = {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            resp = await client.get(url, headers=headers)
        return {
            "success": resp.status_code == 200,
            "status_code": resp.status_code,
            "data": resp.json() if resp.content else None,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def get_erp_images(item):
    """
    Return a list of all ERPNext image URLs for a product.
    Supports 'image' and, optionally, 'gallery_images' fields.
    """
    image_urls = []

    # Main image
    main_image = item.get("image")
    if main_image:
        image_urls.append(
            main_image if main_image.startswith("http") else f"{ERP_URL}{main_image}"
        )

    # Additional images (add here if you have them; placeholder for example)
    # For example, if you have item['gallery_images'] as a list of URLs:
    gallery_images = item.get("gallery_images", [])
    
    if isinstance(gallery_images, str):
        gallery_images = [img.strip() for img in gallery_images.split(",") if img.strip()]

    for gimg in gallery_images:
        if gimg:
            image_urls.append(gimg if gimg.startswith("http") else f"{ERP_URL}{gimg}")

    # Deduplicate
    return list(dict.fromkeys(image_urls))
