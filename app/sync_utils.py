# app/sync_utils.py
# ============================
# Utilities for ERPNext <-> WooCommerce Sync
# - Category & name normalization
# - Category map building
# - Image size/comparison and upload helpers
# - Attribute parsing (robust to single or multiple forms)
# ============================

import html
import httpx
import os
from urllib.parse import urlparse, quote

import logging
logger = logging.getLogger("uvicorn.error")

# --- Category & Name Utilities ---

def normalize_category_name(name):
    if not name:
        return ""
    name = html.unescape(name)
    name = name.replace('\xa0', ' ')
    return name.strip().lower()

def build_wc_cat_map(wc_categories):
    """Map normalized Woo category names to their IDs."""
    return {normalize_category_name(cat["name"]): cat["id"] for cat in wc_categories}

# --- Image Utilities ---

def normalize_woo_image_url(src):
    """Ensure Woo image URLs are absolute, regardless of how they are stored."""
    base = os.getenv("WC_BASE_URL", "").rstrip("/")
    parsed = urlparse(src)
    return base + parsed.path

async def get_image_size_with_fallback(erp_url):
    """
    Get image size from ERPNext via HEAD request (auth required for private files).
    Returns (size:int, full_url:str, headers:dict) or (None, None, None) if failed.
    """
    ERP_URL = os.getenv("ERP_URL", "").rstrip("/")
    ERP_API_KEY = os.getenv("ERP_API_KEY", "")
    ERP_API_SECRET = os.getenv("ERP_API_SECRET", "")
    parsed = urlparse(erp_url.strip())
    encoded_path = quote(parsed.path, safe="/:")
    url = ERP_URL + encoded_path

    headers = {
        "Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"
    }

    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.head(url, headers=headers)
            if resp.status_code == 200 and "content-length" in resp.headers:
                return int(resp.headers["content-length"]), url, headers
    except Exception as e:
        logger.warning(f"[ERP IMG FETCH] Exception: {e} for {url}")

    return None, None, None

async def get_image_size(url, headers=None):
    """
    Get image size via HEAD or GET (WooCommerce/public image).
    """
    url = normalize_woo_image_url(url)
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.head(url, headers=headers)
            if resp.status_code == 200 and "content-length" in resp.headers:
                return int(resp.headers["content-length"])
            elif resp.status_code == 200:
                get_resp = await client.get(url, headers=headers)
                if get_resp.status_code == 200:
                    return len(get_resp.content)
    except Exception:
        pass
    return None

# --- Attribute/Variant Utilities ---

def parse_variant_attributes(item):
    """
    Robustly parse variant attributes from ERPNext item data.
    Handles both:
      - Column pairs (Attribute (Variant Attributes), Attribute Value (Variant Attributes))
      - List or array field (future-proof)
    Returns: dict of attribute_name -> value
    """
    attributes = {}

    # Check for column pair (legacy/frappe standard)
    attr_col = "Attribute (Variant Attributes)"
    val_col = "Attribute Value (Variant Attributes)"
    if attr_col in item and val_col in item:
        attr = item[attr_col]
        val = item[val_col]
        if attr and val:
            attributes[attr] = val

    # Optionally, check for a list (future-proofing)
    if "variant_attributes" in item and isinstance(item["variant_attributes"], list):
        for entry in item["variant_attributes"]:
            name = entry.get("attribute")
            value = entry.get("attribute_value")
            if name and value:
                attributes[name] = value

    return attributes

def get_variant_key(attributes: dict):
    """
    Generates a tuple key for grouping variants with the same parent.
    e.g., ("Stone Veneer", "Andes") or ("Peel and Stick", None)
    """
    # You can adapt this if you need deeper grouping
    # For now, just use all attributes as a tuple (sorted for consistency)
    return tuple(sorted(attributes.items()))

def is_variant_row(item):
    """
    Returns True if the item appears to be a variant row.
    (i.e., has a parent or has variant attributes set)
    """
    return bool(item.get("Variant Of")) or bool(parse_variant_attributes(item))

def get_variant_parent_code(item):
    """
    Returns the parent template code for a variant row (if any).
    """
    return item.get("Variant Of", "")

def get_erp_image_list(item, get_erp_images_func):
    """
    Wraps your get_erp_images to always return a deduped, non-empty list.
    """
    return list(dict.fromkeys(get_erp_images_func(item) or []))
