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

from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
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

def _mapping_dir() -> Path:
    # Prefer container canonical path; fall back to repo layout when running locally
    prefer = Path("/app/mapping")
    try:
        prefer.mkdir(parents=True, exist_ok=True)
        return prefer
    except Exception:
        pass
    fallback = Path(__file__).resolve().parent / "mapping"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback

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

def format_wc_price(value) -> str:
    try:
        d = Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        # avoid scientific notation and guarantee 2 decimals
        return f"{d:.2f}"
    except Exception:
        return "0.00"

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

# --- BRAND UTILS (updated) ---

def _norm_brand(s: str) -> str:
    return (s or "").strip()

def _norm_key(s: str) -> str:
    return _norm_brand(s).lower()

async def get_brand_id_map():
    """
    Returns {brand_name: term_id} for ALL brand terms (paginated).
    Keys are the exact names from WP; compare case-insensitively in callers.
    """
    base = f"{WC_BASE_URL}/wp-json/wp/v2/product_brand"
    auth = (WP_USERNAME, WP_PASSWORD)
    out = {}
    page = 1
    async with httpx.AsyncClient(timeout=20.0, verify=False, auth=auth) as client:
        while True:
            resp = await client.get(f"{base}?per_page=100&page={page}")
            if resp.status_code != 200:
                logger.error("[Brand] list failed: %s %s", resp.status_code, resp.text)
                break
            batch = resp.json() or []
            if not batch:
                break
            for b in batch:
                name = _norm_brand(b.get("name"))
                bid = b.get("id")
                if name and bid:
                    out[name] = bid
            if len(batch) < 100:
                break
            page += 1
    return out

async def create_brand(name: str):
    """
    Creates a product_brand term via WP REST. If it already exists,
    returns the existing term_id from the error body when available.
    """
    url = f"{WC_BASE_URL}/wp-json/wp/v2/product_brand"
    auth = (WP_USERNAME, WP_PASSWORD)
    payload = {"name": _norm_brand(name)}
    async with httpx.AsyncClient(timeout=20.0, verify=False, auth=auth) as client:
        resp = await client.post(url, json=payload)

        # Happy path
        if resp.status_code in (200, 201):
            data = resp.json()
            bid = data.get("id")
            logger.info("Created Woo brand %r (id=%s)", payload["name"], bid)
            return bid

        # Many WP installs return {"code":"term_exists", ..., "data":{"term_id": <id>}}
        term_id = None
        try:
            data = resp.json()
            term_id = (data or {}).get("data", {}).get("term_id")
        except Exception:
            pass
        if term_id:
            logger.info("Brand %r already exists (id=%s) — using existing.", payload["name"], term_id)
            return term_id

        logger.error("Failed to create brand %r (status %s): %s",
                     payload["name"], resp.status_code, resp.text)
        return None

async def ensure_all_erp_brands_exist(erp_items):
    """
    Collect unique ERP brands (brand/Brand), ensure terms exist,
    and return {original_brand_name: term_id}.
    """
    # Gather unique, normalized non-empty brand names
    all_brands = {
        _norm_brand(item.get("brand") or item.get("Brand"))
        for item in erp_items
        if (item.get("brand") or item.get("Brand"))
    }
    all_brands = {b for b in all_brands if b}

    existing = await get_brand_id_map()
    existing_lc = {_norm_key(k): v for k, v in existing.items()}

    brand_id_map = {}
    for b in sorted(all_brands):
        key = _norm_key(b)
        if key in existing_lc:
            brand_id_map[b] = existing_lc[key]
            continue
        bid = await create_brand(b)
        if bid:
            brand_id_map[b] = bid
        else:
            logger.error("Could not create or map Woo brand for %r", b)
    return brand_id_map

async def assign_brand_to_product(product_id: int, brand_id: int) -> bool:
    """
    Attach brand term(s) to a product via WP REST (Basic Auth).
    Returns True on success. Uses POST per WP REST conventions.
    """
    url = f"{WC_BASE_URL}/wp-json/wp/v2/product/{product_id}"
    auth = (WP_USERNAME, WP_PASSWORD)
    payload = {"product_brand": [int(brand_id)]}
    async with httpx.AsyncClient(timeout=20.0, verify=False, auth=auth) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code in (200, 201):
            return True
        # Log full body to help debug plugin/permission issues
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        logger.error("Failed to assign brand %s to product %s: %s %s",
                     brand_id, product_id, resp.status_code, body)
        return False

# --- BRAND RECONCILIATION (new) ---

async def list_wp_brands_full():
    """
    Return a full list of product_brand terms with fields like:
    [{id, name, slug, count, ...}, ...]
    """
    base = f"{WC_BASE_URL}/wp-json/wp/v2/product_brand"
    auth = (WP_USERNAME, WP_PASSWORD)
    out, page = [], 1
    async with httpx.AsyncClient(timeout=20.0, verify=False, auth=auth) as client:
        while True:
            resp = await client.get(f"{base}?per_page=100&page={page}")
            if resp.status_code != 200:
                logger.error("[Brand] list failed: %s %s", resp.status_code, resp.text)
                break
            batch = resp.json() or []
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return out

async def update_brand(term_id, *, name=None, slug=None):
    """
    Update a product_brand term's name/slug.
    WordPress accepts POST for updates on term endpoints.
    """
    if not name and not slug:
        return False
    url = f"{WC_BASE_URL}/wp-json/wp/v2/product_brand/{int(term_id)}"
    auth = (WP_USERNAME, WP_PASSWORD)
    payload = {}
    if name is not None:
        payload["name"] = name
    if slug is not None:
        payload["slug"] = slug
    async with httpx.AsyncClient(timeout=20.0, verify=False, auth=auth) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code in (200, 201):
            return True
        logger.error("[Brand] update %s failed: %s %s", term_id, resp.status_code, resp.text)
        return False

async def delete_brand(term_id, *, force=True):
    """
    Delete a product_brand term. If force=True, permanently deletes.
    """
    url = f"{WC_BASE_URL}/wp-json/wp/v2/product_brand/{int(term_id)}?force={'true' if force else 'false'}"
    auth = (WP_USERNAME, WP_PASSWORD)
    async with httpx.AsyncClient(timeout=20.0, verify=False, auth=auth) as client:
        resp = await client.delete(url)
        if resp.status_code in (200, 410):  # 410 gone is ok
            return True
        logger.error("[Brand] delete %s failed: %s %s", term_id, resp.status_code, resp.text)
        return False

def _norm_brand(s):
    return (s or "").strip()

def _norm_key(s):
    return _norm_brand(s).lower()

async def reconcile_woocommerce_brands(
    erp_brand_names,
    *,
    delete_missing=False,
    dry_run=False,
    skip_in_use=True
):
    """
    Compare ERP brand set with Woo product_brand terms and:
      - create missing
      - update case-only name differences
      - (optional) delete brands not present in ERP (skips terms with products by default)

    Returns a report dict with created/updated/deleted/skipped and totals.
    """
    # Normalize ERP set
    erp_set = {_norm_brand(b) for b in (erp_brand_names or []) if _norm_brand(b)}
    erp_lc = {_norm_key(b) for b in erp_set}

    # Current Woo terms (full + convenience maps)
    terms = await list_wp_brands_full()
    by_lc = {_norm_key(t.get("name")): t for t in terms if t.get("name")}
    by_id = {t.get("id"): t for t in terms if t.get("id")}

    report = {
        "created": [],
        "updated": [],
        "deleted": [],
        "skipped": [],
        "total_erp_brands": len(erp_set),
        "total_wc_brands": len(terms),
        "dry_run": dry_run,
        "delete_missing": delete_missing,
    }

    # ADD or UPDATE (case-only adjustments)
    for b in sorted(erp_set):
        lc = _norm_key(b)
        t = by_lc.get(lc)
        if not t:
            if dry_run:
                report["created"].append({"name": b})
            else:
                tid = await create_brand(b)
                if tid:
                    report["created"].append({"id": tid, "name": b})
                else:
                    report["skipped"].append({"name": b, "reason": "create_failed"})
            continue

        # If only case differs, update name to match ERP canonical case
        current_name = t.get("name") or ""
        if current_name != b and current_name.lower() == b.lower():
            if dry_run:
                report["updated"].append({"id": t["id"], "from": current_name, "to": b})
            else:
                ok = await update_brand(t["id"], name=b)
                (report["updated"] if ok else report["skipped"]).append(
                    {"id": t["id"], "from": current_name, "to": b, **({} if ok else {"reason": "update_failed"})}
                )
        else:
            report["skipped"].append({"id": t["id"], "name": current_name, "reason": "exists"})

    # DELETE extras not in ERP (optional)
    if delete_missing:
        for t in terms:
            name = t.get("name") or ""
            lc = _norm_key(name)
            if lc in erp_lc:
                continue
            if skip_in_use and int(t.get("count") or 0) > 0:
                report["skipped"].append({"id": t["id"], "name": name, "reason": "in_use"})
                continue
            if dry_run:
                report["deleted"].append({"id": t["id"], "name": name})
            else:
                ok = await delete_brand(t["id"], force=True)
                (report["deleted"] if ok else report["skipped"]).append(
                    {"id": t["id"], "name": name, **({} if ok else {"reason": "delete_failed"})}
                )

    return report

# --- GALLERY LOGIC FOR VARIANTS ---

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

# --- Partial sync - recording sync_products_preview output for partial sync in JSON file ---

def save_preview_to_file(preview: dict, filename: str = "products_to_sync.json"):
    """
    Save sync preview output to app/mapping/products_to_sync.json for partial sync.
    Uses atomic write (temp file + replace), utf-8 encoding, and ensures the mapping directory exists.
    """
    os.makedirs(_mapping_dir(), exist_ok=True)
    target_path = os.path.join(_mapping_dir(), filename)
    tmp_file = target_path + ".tmp"

    logger.info(f"Saving partial sync reference file '{target_path}'")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(preview, f, indent=2, ensure_ascii=False)
    os.replace(tmp_file, target_path)

def load_preview_from_file(filename: str = "products_to_sync.json"):
    # Always load from app/mapping/
    file_path = os.path.join(_mapping_dir, filename)
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

async def sync_products_filtered(erp_items, wc_products, dry_run=False):
    """
    Run the real sync logic for a filtered set of items (for partial sync).
    - Now builds correct featured + gallery images per the rules.
    """
    logger.info(f"Starting filtered sync with {len(erp_items)} ERP and {len(wc_products)} Woo products (dry_run={dry_run})")

    wc_map = {p.get("sku"): p for p in wc_products if p.get("sku")}
    stats = {"updated": 0, "created": 0, "skipped": 0, "errors": []}

    # Re-fetch price and stock for accuracy
    from app.erpnext import get_price_map, get_stock_map
    price_map = await get_price_map()
    stock_map = await get_stock_map()

    for item in erp_items:
        sku = item.get("item_code") or item.get("Item Code") or item.get("name")
        wc = wc_map.get(sku)
        price = price_map.get(sku, item.get("standard_rate", 0))
        default_wh = item.get("default_warehouse")
        stock_qty = (
            stock_map.get((sku, default_wh), 0)
            if sku and default_wh
            else sum(qty for (code, wh), qty in stock_map.items() if code == sku)
        )

        # === NEW: build featured + gallery according to type ===
        # Variant item?
        is_variant = bool(item.get("variant_of") or item.get("Variant Of"))
        if is_variant:
            featured, gallery = await erp_get_variant_family_media_from_list(item, erp_items)
        else:
            featured = await erp_get_item_featured(sku)
            gallery = await erp_get_item_gallery(sku)

        image_list = ([featured] if featured else []) + (gallery or [])

        # Build WC payload
        wc_payload = map_erp_to_wc_product(item, category_map=None, brand_map=None, image_list=image_list)
        wc_payload["regular_price"] = format_wc_price(price)
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

        # Update existing
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

def strip_html_tags(text):
    """
    Naive HTML tag stripper (for comparing descriptions). Replace with a more robust solution if needed.
    """
    import re
    return re.sub(r"<[^>]+>", "", text or "")

async def erp_get_variant_family_media(variant_codes: list[str]) -> tuple[str | None, list[str]]:
    """
    For a set of variant SKUs:
    - Featured: take image of the first variant (assume all equal).
    - Gallery: intersection of non-featured File attachments across ALL variants.
    Returns (featured_file_url, gallery_file_urls[])
    """
    if not variant_codes:
        return None, []

    # Featured from first variant
    featured = await erp_get_item_featured(variant_codes[0])

    # Gather per-variant galleries (excluding image/website_image & excluding featured)
    per_variant_lists = []
    for code in variant_codes:
        fields = quote(json.dumps(["file_url", "attached_to_field", "attached_to_name"]))
        filters = quote(json.dumps([
            ["attached_to_doctype", "=", "Item"],
            ["attached_to_name", "=", code],
        ]))
        url = f"{ERP_URL}/api/resource/File?fields={fields}&filters={filters}&order_by=creation%20asc&limit_page_length=1000"
        headers = {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            r = await client.get(url, headers=headers)
            data = r.json().get("data", []) if r.status_code == 200 else []
        # filter this variant’s list
        seen, this_list = set(), []
        for row in data:
            fu = row.get("file_url")
            fld = (row.get("attached_to_field") or "").lower()
            if not fu or fld in {"image", "website_image"}:
                continue
            if featured and fu == featured:
                continue
            if fu not in seen:
                seen.add(fu)
                this_list.append(fu)
        per_variant_lists.append(this_list)

    # Intersection, with order preserved from the first variant
    if not per_variant_lists:
        return featured, []
    common = set(per_variant_lists[0])
    for lst in per_variant_lists[1:]:
        common &= set(lst)
    gallery = [fu for fu in per_variant_lists[0] if fu in common]
    return featured, gallery

# --- NEW: ERP image fetch helpers ---

async def erp_get_item_featured(item_code: str) -> str | None:
    """
    ERPNext: return Item.image (file_url) for an item code.
    """
    headers = {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}
    filters = quote(json.dumps({"name": item_code}))
    url = f"{ERP_URL}/api/method/frappe.client.get_value?doctype=Item&fieldname=image&filters={filters}"
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        r = await client.get(url, headers=headers)
        if r.status_code == 200:
            return (r.json().get("message") or {}).get("image") or None
    return None

async def erp_get_item_gallery(item_code: str) -> list[str]:
    """
    ERPNext: for a simple item, return all File.file_url attached to that Item
    excluding rows attached to fields 'image' or 'website_image' and excluding duplicates.
    """
    headers = {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}
    fields = quote(json.dumps(["file_url", "attached_to_field"]))
    filters = quote(json.dumps([
        ["attached_to_doctype", "=", "Item"],
        ["attached_to_name", "=", item_code],
    ]))
    url = f"{ERP_URL}/api/resource/File?fields={fields}&filters={filters}&order_by=creation%20asc&limit_page_length=1000"
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        r = await client.get(url, headers=headers)
        data = r.json().get("data", []) if r.status_code == 200 else []
    seen, out = set(), []
    for row in data:
        fu = row.get("file_url")
        fld = (row.get("attached_to_field") or "").lower()
        if not fu or fld in {"image", "website_image"}:
            continue
        if fu not in seen:
            seen.add(fu)
            out.append(fu)
    return out

def _attrs_dict(item: dict) -> dict:
    """
    Get a dict of variant attributes from ERP item row (works with both legacy pair and list form).
    """
    d = {}
    # pair form
    a_col = "Attribute (Variant Attributes)"
    v_col = "Attribute Value (Variant Attributes)"
    if a_col in item and v_col in item and item.get(a_col) and item.get(v_col):
        d[item[a_col]] = item[v_col]
    # list form
    if "attributes" in item and isinstance(item["attributes"], list):
        for row in item["attributes"]:
            n = row.get("attribute")
            v = row.get("attribute_value")
            if n and v:
                d[n] = v
    if "variant_attributes" in item and isinstance(item["variant_attributes"], list):
        for row in item["variant_attributes"]:
            n = row.get("attribute")
            v = row.get("attribute_value")
            if n and v:
                d[n] = v
    return d

def _style_key(item: dict) -> tuple:
    """
    Build a 'style' key for a variant family (exclude size-ish attributes).
    We explicitly ignore 'Sheet Size' and any attribute whose name contains 'size' (case-insensitive).
    """
    attrs = _attrs_dict(item)
    style = []
    for k, v in attrs.items():
        if k.lower() in {"sheet size"} or "size" in k.lower():
            continue
        style.append((k, v))
    style.sort()
    return tuple(style)

async def erp_get_variant_family_media_from_list(item: dict, erp_items: list[dict]) -> tuple[str | None, list[str]]:
    """
    For a single variant item and the list of all ERP items:
      - featured = that variant's Item.image
      - gallery  = intersection of File.file_url across all sibling variants in the family
                   (same variant_of and same non-size attributes), excluding image/website_image and the featured.
    Returns (featured:str|None, gallery:list[str]).
    """
    headers = {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}

    # Collect family
    variant_of = item.get("variant_of") or item.get("Variant Of")
    if not variant_of:
        # Not a variant row
        return await erp_get_item_featured(item.get("item_code") or item.get("Item Code") or item.get("name")), []

    key = _style_key(item)
    family = []
    for it in erp_items:
        if (it.get("variant_of") or it.get("Variant Of")) != variant_of:
            continue
        if _style_key(it) == key:
            code = it.get("item_code") or it.get("Item Code") or it.get("name")
            if code:
                family.append(code)

    # Featured is the current variant's image (we expect it to be same across family)
    this_code = item.get("item_code") or item.get("Item Code") or item.get("name")
    featured = await erp_get_item_featured(this_code)

    if not family:
        return featured, []

    # Fetch all File rows for the family in one query
    fields = quote(json.dumps(["file_url", "attached_to_field", "attached_to_name", "creation"]))
    filters = quote(json.dumps([
        ["attached_to_doctype", "=", "Item"],
        ["attached_to_name", "in", family],
    ]))
    url = f"{ERP_URL}/api/resource/File?fields={fields}&filters={filters}&order_by=creation%20asc&limit_page_length=1000"
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        r = await client.get(url, headers=headers)
        data = r.json().get("data", []) if r.status_code == 200 else []

    # Count per file_url across distinct items; filter to those present for ALL family members
    per_file = {}
    order_hint = {}
    for row in data:
        fu = row.get("file_url")
        fld = (row.get("attached_to_field") or "").lower()
        name = row.get("attached_to_name")
        crt = row.get("creation")
        if not fu or fld in {"image", "website_image"}:
            continue
        per_file.setdefault(fu, set()).add(name)
        # remember earliest creation for ordering
        if fu not in order_hint or (crt and str(crt) < str(order_hint[fu])):
            order_hint[fu] = crt

    gallery = []
    for fu, names in per_file.items():
        if len(names) == len(set(family)):
            if not featured or fu != featured:
                gallery.append(fu)

    # Order by earliest creation for stability
    gallery.sort(key=lambda fu: str(order_hint.get(fu, "")) or fu)
    return featured, gallery

async def erp_head_sizes(file_urls: list[str]) -> list[int]:
    """
    HEAD each file URL (ERP private or public) and return content-lengths.
    """
    out = []
    for fu in file_urls or []:
        size, _, _ = await get_image_size_with_fallback(fu)  # returns (size, full_url, headers)
        if size is not None:
            out.append(size)
    return out

