#===========================================================================
# app/erpnext.py
# ERPNext API interface module.
# Provides functions to interact with ERPNext for products and categories.
#===========================================================================

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

ERP_URL = os.getenv("ERP_URL")
ERP_API_KEY = os.getenv("ERP_API_KEY")
ERP_API_SECRET = os.getenv("ERP_API_SECRET")
ERP_SELLING_PRICE_LIST = os.getenv("ERP_SELLING_PRICE_LIST", "Standard Selling")


async def get_price_map(price_list=None):
    """
    Fetch latest price for each item in the given price list.
    Skips price lists that are disabled.
    Returns: dict {item_code: price}
    """
    import httpx
    from app.erpnext import ERP_URL, ERP_API_KEY, ERP_API_SECRET
    price_list = price_list or ERP_SELLING_PRICE_LIST

    # First, check if the price list is enabled
    check_url = f"{ERP_URL}/api/resource/Price List/{price_list}?fields=[\"name\",\"enabled\"]"
    headers = {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        check_resp = await client.get(check_url, headers=headers)
        if check_resp.status_code != 200 or not check_resp.json().get("data", {}).get("enabled", 1):
            return {}  # Disabled price list: return empty map

        url = (
            f"{ERP_URL}/api/resource/Item Price"
            f"?fields=[\"item_code\",\"price_list_rate\"]"
            f"&filters=[[\"price_list\",\"=\",\"{price_list}\"],[\"selling\",\"=\",1]]"
            f"&limit_page_length=1000"
        )
        resp = await client.get(url, headers=headers)
        price_map = {}
        if resp.status_code == 200 and resp.json().get("data"):
            for row in resp.json()["data"]:
                price_map[row["item_code"]] = row["price_list_rate"]
        return price_map


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
        "?fields=[\"item_code\",\"item_name\",\"description\",\"stock_uom\",\"standard_rate\",\"image\",\"item_group\"]"
        "&limit_page_length=1000"  # or higher if needed
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
