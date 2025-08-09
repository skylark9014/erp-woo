# app/field_mapping.py
# ===================================================
# Central ERPNext → WooCommerce product field mapping
# ===================================================
from __future__ import annotations
from typing import List
from urllib.parse import urlparse, quote
from app.config import settings

ERP_URL = settings.ERP_URL

def get_erp_sync_fields(*args, **kwargs) -> List[str]:
    """
    Fields to fetch from ERPNext Item for list calls.
    Kept intentionally broad so other parts of the sync have what they need.
    """
    return [
        "name",
        "item_code",
        "item_name",
        "item_group",
        "description",
        "brand",
        "image",
        "website_image",
        "has_variants",
        "variant_of",
        "stock_uom",
        "is_stock_item",
        "disabled",
        "end_of_life",
    ]

# ---- WooCommerce → Products/Categories/Media/Attributes ---------------------
def get_wc_sync_fields(*_, **__) -> List[str]:
    """
    Fields we care about when pulling Woo products.
    Keep wide to avoid future missing keys in code that inspects products.
    """
    return [
        "id",
        "sku",
        "name",
        "type",
        "status",
        "regular_price",
        "sale_price",
        "price",
        "description",
        "short_description",
        "manage_stock",
        "stock_status",
        "stock_quantity",
        "categories",
        "attributes",
        "default_attributes",
        "images",          # [{id, src, name, alt}]
        "meta_data",
        "variations",      # ids for variable products
    ]

# Optionally used elsewhere; harmless to include:
def get_erp_image_fields(*args, **kwargs) -> List[str]:
    return ["image", "website_image"]

def _abs_erp_file_url(file_url: str) -> str:
    """
    Turn a /files/… path into an absolute ERP URL, URL-encoding the path safely.
    """
    if not file_url:
        return ""
    # normalize any accidental absolute URLs
    p = urlparse(file_url)
    if p.scheme and p.netloc:
        # looks absolute already
        return file_url
    # encode the path portion to be safe with spaces, parentheses, etc.
    encoded_path = quote(file_url, safe="/:%()[]&=+,-._")
    return ERP_URL.rstrip("/") + encoded_path

def map_erp_to_wc_product(item: dict, category_map=None, brand_map=None, image_list=None) -> dict:
    """
    Build a WooCommerce product payload from an ERPNext item.
    - `image_list`: ordered list of ERP file_urls (first one is featured).
    """
    sku = item.get("item_code") or item.get("Item Code") or item.get("name")
    name = item.get("item_name") or item.get("Item Name") or sku
    description = item.get("description") or ""
    brand = item.get("brand") or item.get("Brand")
    item_group = item.get("item_group") or item.get("Item Group")

    # Categories mapping -> Woo category IDs
    wc_categories = []
    if category_map and item_group:
        norm = (item_group or "").strip().lower()
        wc_id = category_map.get(norm) if isinstance(category_map, dict) else None
        if wc_id:
            wc_categories.append({"id": wc_id})

    # Brand mapping -> taxonomy term (this is typically handled via custom meta/tax API;
    # leave to caller if they assign brand via a separate call)
    # If you encode brand as a product attribute in Woo, do it below in attributes.

    # Attributes (basic pass-through from parsed attributes if present)
    attributes = []
    # The new pipeline usually flattens variant attributes before calling this,
    # but we'll be tolerant and check common locations:
    var_attrs = item.get("attributes") or item.get("variant_attributes") or []
    if isinstance(var_attrs, list):
        for row in var_attrs:
            n = row.get("attribute")
            v = row.get("attribute_value")
            if n and v:
                attributes.append({
                    "name": n,
                    "options": [v],
                    "visible": True,
                    "variation": False,  # caller sets True for parents if needed
                })

    # Images
    images = []
    if image_list:
        for idx, furl in enumerate(image_list):
            abs_url = _abs_erp_file_url(furl)
            if not abs_url:
                continue
            images.append({
                "src": abs_url,
                "position": idx,  # 0 = featured, others = gallery
            })

    payload = {
        "sku": sku,
        "name": name,
        "description": description,
        "type": "simple",  # caller will override to "variable" for parents, etc.
        "regular_price": str(item.get("regular_price", "") or item.get("standard_rate", "") or ""),
        "stock_quantity": item.get("stock_quantity"),
        "manage_stock": True if item.get("stock_quantity") is not None else False,
        "categories": wc_categories,
        "attributes": attributes,
    }

    if images:
        payload["images"] = images

    # Optional brand injection (if you're using a product attribute for brand)
    if brand:
        payload.setdefault("attributes", []).append({
            "name": "Brand",
            "options": [brand],
            "visible": True,
            "variation": False,
        })

    return payload

def get_wc_category_fields(*_, **__) -> List[str]:
    """Fields for Woo product categories."""
    return ["id", "name", "slug", "parent", "menu_order", "count", "description"]

def get_wp_media_fields(*_, **__) -> List[str]:
    """Fields for WP media objects (when ensuring/uploads)."""
    return ["id", "source_url", "media_type", "alt_text", "media_details"]

def get_wc_attribute_fields(*_, **__) -> List[str]:
    """Fields for Woo attributes (used when ensuring attr/terms)."""
    return ["id", "name", "slug", "type", "order_by", "has_archives"]

def get_wc_attribute_term_fields(*_, **__) -> List[str]:
    """Fields for Woo attribute terms."""
    return ["id", "name", "slug", "description", "menu_order", "count"]

