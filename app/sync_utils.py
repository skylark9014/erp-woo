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
import logging
import json
import os

from bs4 import BeautifulSoup
from urllib.parse import urlparse, quote
from app.erpnext import get_erpnext_categories
from app.woocommerce import (
    get_wc_categories, 
    create_wc_category, 
    update_wc_product, 
    create_wc_product,
)
from app.field_mapping import (
    get_wc_sync_fields, 
    map_erp_to_wc_product,
)
from app.config import settings

ERP_URL = settings.ERP_URL
ERP_API_KEY = settings.ERP_API_KEY
ERP_API_SECRET = settings.ERP_API_SECRET
WC_BASE_URL = settings.WC_BASE_URL
WC_API_KEY = settings.WC_API_KEY
WC_API_SECRET = settings.WC_API_SECRET
WP_USERNAME =settings.WP_USERNAME
WP_PASSWORD = settings.WP_PASSWORD

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
    base = WC_BASE_URL.rstrip("/")
    parsed = urlparse(src)
    return base + parsed.path

async def get_image_size_with_fallback(erp_url):
    """
    Get image size from ERPNext via HEAD request (auth required for private files).
    Returns (size:int, full_url:str, headers:dict) or (None, None, None) if failed.
    """
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
    url = f"{WC_BASE_URL}/wp-json/wc/v3/products/attributes?per_page=100"
    auth = (WC_API_KEY, WC_API_SECRET)
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.get(url, auth=auth)
        if resp.status_code == 200:
            return {a["name"]: a["id"] for a in resp.json()}
    return {}

async def create_attribute(name):
    url = f"{WC_BASE_URL}/wp-json/wc/v3/products/attributes"
    auth = (WC_API_KEY, WC_API_SECRET)
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.post(url, auth=auth, json={"name": name})
        if resp.status_code in (200, 201):
            logger.info(f"Created Woo attribute '{name}' (id={resp.json()['id']})")
            return resp.json()["id"]
        else:
            logger.error(f"Failed to create attribute '{name}': {resp.text}")
    return None

async def get_attribute_term_id_map(attr_id):
    url = f"{WC_BASE_URL}/wp-json/wc/v3/products/attributes/{attr_id}/terms?per_page=100"
    auth = (WC_API_KEY, WC_API_SECRET)
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.get(url, auth=auth)
        if resp.status_code == 200:
            return {t["name"]: t["id"] for t in resp.json()}
    return {}

async def create_attribute_term(attr_id, value):
    url = f"{WC_BASE_URL}/wp-json/wc/v3/products/attributes/{attr_id}/terms"
    auth = (WC_API_KEY, WC_API_SECRET)
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
    url = f"{WC_BASE_URL}/wp-json/wp/v2/product_brand?per_page=100"
    auth = (WP_USERNAME, WP_PASSWORD)
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.get(url, auth=auth)
        if resp.status_code == 200:
            return {b["name"]: b["id"] for b in resp.json()}
    return {}

async def create_brand(name):
    url = f"{WC_BASE_URL}/wp-json/wp/v2/product_brand"
    auth = (WP_USERNAME, WP_PASSWORD)
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
    url = f"{WC_BASE_URL}/wp-json/wp/v2/product/{product_id}"
    auth = (WP_USERNAME, WP_PASSWORD)
    payload = {"product_brand": [brand_id]}
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        resp = await client.post(url, auth=auth, json=payload)
        if not resp.status_code in (200, 201):
            logger.error(f"Failed to assign brand {brand_id} to product {product_id}: {resp.status_code} {resp.text}")

def get_gallery_images(item, template=None, get_erp_images=None):
    """
    Returns the gallery images for an ERPNext item.
    If item has no gallery, and template is provided, use template's gallery.
    get_erp_images is the fetch function (if you need to re-fetch).
    """
    # Prefer variant's own images
    imgs = []
    if get_erp_images:
        imgs = get_erp_image_list(item, get_erp_images)
    else:
        imgs = item.get("gallery_images", []) or []
    if not imgs and template:
        if get_erp_images:
            imgs = get_erp_image_list(template, get_erp_images)
        else:
            imgs = template.get("gallery_images", []) or []
    return imgs

# --- GALLERY LOGIC FOR VARIANTS ---
async def get_variant_gallery_images(variant, template, get_erp_images):
    """Return [variant item image] + variant attached images + template attached images (deduped), NOT template item image."""
    images = []
    # Variant's own Item Image as featured
    variant_item_img = variant.get("image")
    if variant_item_img:
        images.append(variant_item_img)
    # Variant's own attached images
    var_attached_imgs = await get_erp_image_list(variant, get_erp_images)
    for img in var_attached_imgs:
        if img not in images:
            images.append(img)
    # Template's attached images (excluding template Item Image)
    if template:
        template_attached_imgs = await get_erp_image_list(template, get_erp_images)
        for img in template_attached_imgs:
            if img not in images and img != template.get("image"):
                images.append(img)
    return images

async def sync_categories(dry_run=False):
    erp_cats = await get_erpnext_categories()
    wc_cats = await get_wc_categories()
    wc_cat_map = {normalize_category_name(cat["name"]): cat for cat in wc_cats}
    created = []
    for erp_cat in erp_cats:
        name = erp_cat["name"]
        name_normalized = normalize_category_name(name)
        if name_normalized not in wc_cat_map:
            logger.info(f"🟢 Creating Woo category: {name}")
            if not dry_run:
                resp = await create_wc_category(name)
            else:
                resp = {"dry_run": True}
            created.append({"erp_category": name, "wc_response": resp})
    if not dry_run and created:
        wc_cats = await get_wc_categories()
    return {
        "created": created,
        "total_erp_categories": len(erp_cats),
        "total_wc_categories": len(wc_cats)
    }

# --- Partial sync - recording sync_products_preview output for partial sync in JSON file ---
import os
import json

def save_preview_to_file(preview: dict, filename: str = "products_to_sync.json"):
    """
    Save sync preview output to app/mapping/products_to_sync.json for partial sync.
    Uses atomic write (temp file + replace), utf-8 encoding, and ensures the mapping directory exists.
    """
    base_dir = os.path.dirname(__file__)
    mapping_dir = os.path.join(base_dir, "mapping")
    os.makedirs(mapping_dir, exist_ok=True)
    target_path = os.path.join(mapping_dir, filename)
    tmp_file = target_path + ".tmp"

    #print(f"*** Writing sync preview to: {target_path}")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(preview, f, indent=2, ensure_ascii=False)
    os.replace(tmp_file, target_path)


def load_preview_from_file(filename: str = "products_to_sync.json"):
    # Always load from app/mapping/
    base_dir = os.path.dirname(__file__)
    mapping_dir = os.path.join(base_dir, "mapping")
    file_path = os.path.join(mapping_dir, filename)
    #print(f"*** Loading sync preview from: {file_path}")
    with open(file_path, "r") as f:
        return json.load(f)


def diff_fields(wc, erp, include=None, ignore=None):
    import re
    from app.field_mapping import get_wc_sync_fields
    from bs4 import BeautifulSoup
    import logging
    logger = logging.getLogger("uvicorn.error")

    def strip_html(s):
        """Robustly strips HTML, preserving spaces at block boundaries."""
        return BeautifulSoup(s or "", "html.parser").get_text(separator=" ", strip=True)

    ignore = set(ignore or [])
    ignore.update({"erp_img_sizes", "wc_img_sizes", "image_diff", "images", "has_variants"})
    if include is None:
        include = get_wc_sync_fields()
    diffs = {}
    for k in include:
        if k in ignore:
            continue
        v1 = wc.get(k)
        v2 = erp.get(k)
        # Normalize
        if k == "regular_price":
            v1 = str(v1 or "").strip()
            v2 = str(v2 or "").strip()
        if k == "categories":
            v1 = [c["id"] for c in v1] if isinstance(v1, list) else []
            v2 = [c["id"] for c in v2] if isinstance(v2, list) else []
        if k == "description":
            v1s = strip_html(v1)
            v2s = strip_html(v2)

            #logger.info(f"[DIFF DEBUG] DESC ({k}) ERP:'{v2s}' WOO:'{v1s}' RAW ERP:'{v2}' RAW WOO:'{v1}'")
            
            if v1s != v2s:
                diffs[k] = [v1, v2]
            continue
        # You could even add this for all string fields
        if isinstance(v1, str): v1 = v1.strip()
        if isinstance(v2, str): v2 = v2.strip()
        if v1 != v2:
            diffs[k] = [v1, v2]
    return diffs

# This helper could be your main sync logic with erp_items and wc_products as parameters:
async def sync_products_filtered(erp_items, wc_products, dry_run=False):
    """
    Run the real sync logic for a filtered set of items (for partial sync).
    """
    logger.info(f"Starting filtered sync with {len(erp_items)} ERP and {len(wc_products)} Woo products (dry_run={dry_run})")

    wc_map = {p.get("sku"): p for p in wc_products if p.get("sku")}
    stats = {"updated": 0, "created": 0, "skipped": 0, "errors": []}

    # Re-fetch price and stock for accuracy
    from app.erpnext import get_price_map, get_stock_map
    price_map = await get_price_map()
    stock_map = await get_stock_map()

    # NOTE: In partial sync, we assume *no* new product creation (if you want, keep that logic!)
    for item in erp_items:
        sku = item.get("item_code") or item.get("Item Code")
        wc = wc_map.get(sku)
        price = price_map.get(sku, item.get("standard_rate", 0))
        default_wh = item.get("default_warehouse")
        stock_qty = (
            stock_map.get((sku, default_wh), 0)
            if sku and default_wh
            else sum(qty for (code, wh), qty in stock_map.items() if code == sku)
        )

        wc_payload = map_erp_to_wc_product(item, category_map=None, brand_map=None, image_list=None)
        wc_payload["regular_price"] = str(price)
        wc_payload["stock_quantity"] = stock_qty

        # Create new products in Woo
        if wc is None:
            logger.info(f"SKU {sku}: Creating new WooCommerce product in partial sync.")
            if not dry_run:
                try:
                    resp = await create_wc_product(wc_payload)
                    if resp.get("status_code", 0) not in (200, 201):
                        logger.error(f"SKU {sku}: Woo creation failed: {resp}")
                        stats["errors"].append({"sku": sku, "error": resp})
                    else:
                        stats["created"] += 1
                except Exception as e:
                    logger.error(f"SKU {sku}: Error creating Woo product: {e}")
                    stats["errors"].append({"sku": sku, "error": str(e)})
            continue

        fields_changed = diff_fields(wc, wc_payload, include=get_wc_sync_fields())
        if fields_changed:
            logger.info(f"SKU {sku}: Updating Woo fields {fields_changed}")
            if not dry_run:
                try:
                    resp = await update_wc_product(wc["id"], wc_payload)
                    if resp.get("status_code", 0) not in (200, 201):
                        logger.error(f"SKU {sku}: Woo update failed: {resp}")
                        stats["errors"].append({"sku": sku, "error": resp})
                    else:
                        stats["updated"] += 1
                except Exception as e:
                    logger.error(f"SKU {sku}: Error updating Woo: {e}")
                    stats["errors"].append({"sku": sku, "error": str(e)})
        else:
            logger.info(f"SKU {sku}: No fields need update.")
            stats["skipped"] += 1

    logger.info(f"Partial sync: {stats['updated']} updated, {stats['skipped']} skipped, {len(stats['errors'])} errors.")
    return stats