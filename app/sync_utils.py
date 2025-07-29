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

async def get_erp_image_list(item, get_erp_images_func):
    """
    Wraps your get_erp_images to always return a deduped, non-empty list.
    """
    images = await get_erp_images_func(item)
    return list(dict.fromkeys(images or []))

async def ensure_all_erp_attributes_exist_global():
    """
    Ensures ALL Item Attribute names/values from ERPNext exist as Woo global attributes and terms.
    Returns {attr_name: attr_id}, {attr_name: {option_name: term_id}}
    """
    attr_map = await get_erpnext_item_attributes()
    attr_id_map = await get_attribute_id_map()
    # Step 1: create/check attributes
    for attr in attr_map:
        if attr not in attr_id_map:
            attr_id = await create_attribute(attr)
            if attr_id:
                attr_id_map[attr] = attr_id
            else:
                logger.error(f"Could not create attribute '{attr}'")
    # Step 2: create/check terms for each attribute
    attr_term_id_map = {}
    for attr, options in attr_map.items():
        attr_id = attr_id_map.get(attr)
        if not attr_id:
            continue
        term_id_map = await get_attribute_term_id_map(attr_id)
        for option in options:
            if option not in term_id_map:
                term_id = await create_attribute_term(attr_id, option)
                if term_id:
                    term_id_map[option] = term_id
        attr_term_id_map[attr] = term_id_map
    logger.info(f"Attributes ensured: {list(attr_id_map.keys())}")
    return attr_id_map, attr_term_id_map

async def get_erpnext_item_attributes():
    """
    Fetch all global Item Attributes and their possible values from ERPNext.
    Returns dict {attr_name: set([value1, value2, ...])}
    """
    import httpx
    ERP_URL = os.getenv("ERP_URL")
    ERP_API_KEY = os.getenv("ERP_API_KEY")
    ERP_API_SECRET = os.getenv("ERP_API_SECRET")
    headers = {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}

    # Step 1: Get all attribute names
    url = f"{ERP_URL}/api/resource/Item Attribute?fields=[\"name\"]&limit_page_length=100"
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.get(url, headers=headers)
        data = resp.json().get("data", []) if resp.status_code == 200 else []
        attr_names = [row["name"] for row in data]

    # Step 2: For each attribute, get all values
    attr_map = {}
    for attr in attr_names:
        url = f"{ERP_URL}/api/resource/Item Attribute/{attr}"
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            resp = await client.get(url, headers=headers)
            doc = resp.json().get("data", {}) if resp.status_code == 200 else {}
            values = set()
            for row in doc.get("item_attribute_values", []):
                v = row.get("attribute_value")
                if v:
                    values.add(v)
            attr_map[attr] = values
    return attr_map

# --- ATTRIBUTE UTILS ---

async def get_attribute_id_map():
    base_url = os.getenv("WC_BASE_URL", "")
    wc_user = os.getenv("WC_API_KEY")
    wc_pass = os.getenv("WC_API_SECRET")
    url = f"{base_url}/wp-json/wc/v3/products/attributes?per_page=100"
    auth = (wc_user, wc_pass)
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.get(url, auth=auth)
        if resp.status_code == 200:
            return {a["name"]: a["id"] for a in resp.json()}
    return {}

async def create_attribute(name):
    base_url = os.getenv("WC_BASE_URL", "")
    wc_user = os.getenv("WC_API_KEY")
    wc_pass = os.getenv("WC_API_SECRET")
    url = f"{base_url}/wp-json/wc/v3/products/attributes"
    auth = (wc_user, wc_pass)
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.post(url, auth=auth, json={"name": name})
        if resp.status_code in (200, 201):
            logger.info(f"Created Woo attribute '{name}' (id={resp.json()['id']})")
            return resp.json()["id"]
        else:
            logger.error(f"Failed to create attribute '{name}': {resp.text}")
    return None

async def get_attribute_term_id_map(attr_id):
    base_url = os.getenv("WC_BASE_URL", "")
    wc_user = os.getenv("WC_API_KEY")
    wc_pass = os.getenv("WC_API_SECRET")
    url = f"{base_url}/wp-json/wc/v3/products/attributes/{attr_id}/terms?per_page=100"
    auth = (wc_user, wc_pass)
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.get(url, auth=auth)
        if resp.status_code == 200:
            return {t["name"]: t["id"] for t in resp.json()}
    return {}

async def create_attribute_term(attr_id, value):
    base_url = os.getenv("WC_BASE_URL", "")
    wc_user = os.getenv("WC_API_KEY")
    wc_pass = os.getenv("WC_API_SECRET")
    url = f"{base_url}/wp-json/wc/v3/products/attributes/{attr_id}/terms"
    auth = (wc_user, wc_pass)
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.post(url, auth=auth, json={"name": value})
        if resp.status_code in (200, 201):
            logger.info(f"Created term '{value}' for attribute {attr_id}")
            return resp.json()["id"]
        else:
            logger.error(f"Failed to create term '{value}' for attribute {attr_id}: {resp.text}")
    return None

# --- BRAND UTILS (unchanged) ---

async def get_brand_id_map():
    base_url = os.getenv("WC_BASE_URL", "")
    wp_user = os.getenv("WP_USERNAME")
    wp_pass = os.getenv("WP_APP_PASSWORD")
    url = f"{base_url}/wp-json/wp/v2/product_brand?per_page=100"
    auth = (wp_user, wp_pass)
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.get(url, auth=auth)
        if resp.status_code == 200:
            return {b["name"]: b["id"] for b in resp.json()}
    return {}

async def create_brand(name):
    base_url = os.getenv("WC_BASE_URL", "")
    wp_user = os.getenv("WP_USERNAME")
    wp_pass = os.getenv("WP_APP_PASSWORD")
    url = f"{base_url}/wp-json/wp/v2/product_brand"
    auth = (wp_user, wp_pass)
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.post(url, auth=auth, json={"name": name})
        if resp.status_code in (200, 201):
            logger.info(f"Created Woo brand '{name}' (id={resp.json()['id']})")
            return resp.json()["id"]
        else:
            logger.error(f"Failed to create brand '{name}' (status {resp.status_code}): {resp.text}")
    return None

async def ensure_all_erp_brands_exist(erp_items):
    all_brands = set()
    for item in erp_items:
        brand = item.get("brand") or item.get("Brand")
        if brand:
            all_brands.add(brand)
    brand_id_map = await get_brand_id_map()
    for brand in all_brands:
        if brand not in brand_id_map:
            brand_id = await create_brand(brand)
            if brand_id:
                brand_id_map[brand] = brand_id
            else:
                logger.error(f"Could not create or map Woo brand for '{brand}'")
    return brand_id_map

async def assign_brand_to_product(product_id, brand_id):
    base_url = os.getenv("WC_BASE_URL", "")
    wp_user = os.getenv("WP_USERNAME")
    wp_pass = os.getenv("WP_APP_PASSWORD")
    url = f"{base_url}/wp-json/wp/v2/product/{product_id}"
    auth = (wp_user, wp_pass)
    payload = {"product_brand": [brand_id]}
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.post(url, auth=auth, json=payload)
        if not resp.status_code in (200, 201):
            logger.error(f"Failed to assign brand {brand_id} to product {product_id}: {resp.status_code} {resp.text}")

