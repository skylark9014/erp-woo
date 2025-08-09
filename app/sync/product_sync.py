# app/sync/product_sync.py
# =======================================================
# ERPNext → WooCommerce Product/Variant Sync Orchestrator
# - Category sync
# - Price list resolution (single log source-of-truth)
# - Variant matrix build + fallbacks
# - Attribute & Brand ensure (preview vs live)
# - Product preview w/ diffs (desc, images, price, brand)
# =======================================================
from __future__ import annotations

import logging
import inspect
import httpx

from urllib.parse import urlparse, quote
from typing import List, Dict, Any, Optional
from collections import defaultdict
from app.config import settings
from app.erpnext import (
    get_erpnext_items,
    get_erp_images,
    get_erpnext_categories,
    get_price_map,
    get_stock_map,
)
from app.woocommerce import (
    get_wc_products,
    get_wc_categories,
    ensure_wc_attributes_and_terms,
    ensure_wc_brand_attribute_and_terms,
    ensure_wp_image_uploaded,
    purge_wc_bin_products,
)
from app.sync_utils import (
    build_wc_cat_map,
    normalize_category_name,
    sync_categories,
)
from app.mapping_store import save_mapping_file  # keep import path as in your project
from app.erp.erp_variant_matrix import build_variant_matrix
from app.erp.erp_attribute_loader import (
    get_erpnext_attribute_order,
    get_erpnext_attribute_map,
    AttributeValueMapping,
)
from app.sync.components.util import maybe_await, strip_html, basename
from app.sync.components.price import resolve_price_map
from app.sync.components.gallery import (
    normalize_gallery_from_wc_product,
    gallery_images_equal,
)
from app.sync.components.images import (
    safe_get_erp_gallery_for_sku,
    get_wc_gallery_sizes_for_product,
    normalize_gallery_from_wc_product,
    gallery_images_equal,
)
from app.sync.components.matrix import (
    merge_simple_items_into_matrix,
    filter_variant_matrix_by_sku,
    build_fallback_variant_matrix,
    build_fallback_variant_matrix_by_base,
    infer_global_attribute_order_from_skus,
)
from app.sync.components.attributes import (
    collect_used_attribute_values,
)
from app.sync.components.brands import (
    extract_brand,
    collect_erp_brands_from_items,
)

ERP_URL = settings.ERP_URL
ERP_API_KEY = settings.ERP_API_KEY
ERP_API_SECRET = settings.ERP_API_SECRET

logger = logging.getLogger("uvicorn.error")

# ---- minimal async/sync bridge + gallery helper ----

async def _maybe_await(x):
    if inspect.isawaitable(x):
        return await x
    return x

def _extract_image_urls_from_item(item: dict) -> list[dict]:
    urls = []
    for key in ("website_image", "image", "thumbnail", "image_url", "img"):
        v = (item or {}).get(key)
        if isinstance(v, str) and v.strip():
            urls.append({"url": v.strip(), "size": 0})
    for i in range(1, 6):
        v = (item or {}).get(f"image_{i}")
        if isinstance(v, str) and v.strip():
            urls.append({"url": v.strip(), "size": 0})
    # dedupe
    seen, out = set(), []
    for d in urls:
        if d["url"] not in seen:
            seen.add(d["url"])
            out.append(d)
    return out

def _abs_erp_file_url(file_url: str) -> str:
    """Turn '/files/…' into a fully-qualified URL; leave absolute URLs alone."""
    if not file_url:
        return ""
    p = urlparse(file_url)
    if p.scheme and p.netloc:
        return file_url
    return ERP_URL.rstrip("/") + quote(file_url, safe="/:%()[]&=+,-._")

async def _erp_get_featured(item_code: str) -> Optional[str]:
    """Item.image for a given item_code (uses the exact API pattern you tested)."""
    if not item_code:
        return None
    headers = {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}
    filters = quote('{"name":"%s"}' % item_code, safe="/:%()[]&=+,-._{}\"")
    url = f"{ERP_URL}/api/method/frappe.client.get_value?doctype=Item&fieldname=image&filters={filters}"
    try:
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            r = await client.get(url, headers=headers)
            if r.status_code == 200:
                return (r.json().get("message") or {}).get("image") or None
    except Exception as e:
        logger.error(f"Failed to fetch featured image for {item_code}: {e}")
    return None

async def _erp_get_file_rows_for_items(item_codes: list[str]) -> list[dict]:
    """
    All File rows for given Item codes, ordered by creation asc.
    Returns [{file_url, attached_to_field, attached_to_name, creation}, ...]
    """
    if not item_codes:
        return []
    headers = {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}
    fields = quote('["file_url","attached_to_field","attached_to_name","creation"]')
    # [["attached_to_doctype","=","Item"],["attached_to_name","in",[...]]]
    filt = quote(
        '[["attached_to_doctype","=","Item"],["attached_to_name","in",%s]]'
        % str(item_codes).replace("'", '"')
    )
    url = f"{ERP_URL}/api/resource/File?fields={fields}&filters={filt}&order_by=creation%20asc&limit_page_length=1000"
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            r = await client.get(url, headers=headers)
            if r.status_code == 200:
                return r.json().get("data", []) or []
    except Exception as e:
        logger.error(f"Failed to fetch File rows for {item_codes}: {e}")
    return []

async def _head_sizes_for_urls(urls: list[str]) -> list[int]:
    """Return Content-Length for each URL (0 if missing)."""
    out: list[int] = []
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False, follow_redirects=True) as client:
            for u in urls:
                clen = 0
                try:
                    r = await client.head(u)
                    if r.status_code >= 400:
                        # some servers block HEAD; try ranged GET
                        r = await client.get(u, headers={"Range": "bytes=0-0"})
                    h = r.headers
                    val = h.get("Content-Length") or h.get("content-length")
                    if val:
                        clen = int(val)
                except Exception as e:
                    logger.debug(f"HEAD size failed for {u}: {e}")
                out.append(clen)
    except Exception as e:
        logger.debug(f"HEAD client error: {e}")
        out = [0 for _ in urls]
    return out

def _style_key_from_entry(entry: dict) -> tuple:
    """
    Build a 'style' identity from an attributes_entry row:
    exclude size-ish attributes (e.g., 'Sheet Size', anything with 'size').
    """
    pairs = []
    for aname, rec in (entry or {}).items():
        if "size" in aname.lower():
            continue
        val = (rec or {}).get("value")
        if val is not None and str(val).strip():
            pairs.append((aname, str(val).strip()))
    pairs.sort()
    return tuple(pairs)

# =========================
# 0. Purge Woo BIN (option)
# =========================
async def purge_woo_bin_if_needed(auto_purge: bool = True):
    if not auto_purge:
        return
    logger.info("🗑️ Purging WooCommerce bin/trash before sync...")
    try:
        await purge_wc_bin_products()
    except Exception as e:
        logger.error(f"Failed to purge Woo bin: {e}")


# =========================
# 1. Sync Entry Points
# =========================
async def sync_products_full(dry_run: bool = False, purge_bin: bool = True) -> Dict[str, Any]:
    logger.info("🔁 [SYNC] Starting full ERPNext → Woo sync (dry_run=%s)", dry_run)

    if not dry_run and purge_bin:
        await purge_woo_bin_if_needed(True)

    # 1) Categories first (so cat IDs are available for mapping)
    await get_erpnext_categories()
    await get_wc_categories()
    category_report = await sync_categories(dry_run=dry_run)
    wc_categories = await get_wc_categories()  # refresh after potential creation

    # 2) ERP items, prices, stock
    erp_items = await get_erpnext_items()

    # Price list — single source of truth + single log
    price_map, price_list_name, price_count = await resolve_price_map(get_price_map, settings.ERP_SELLING_PRICE_LIST)
    if price_list_name:
        logger.info("Using price list: %s with %d prices", price_list_name, price_count)
    else:
        logger.info("Using price list with %d prices", price_count)

    stock_map = await get_stock_map()

    # 3) Attributes & variant matrix (live ERP attribute order/map)
    erp_attr_order = await _maybe_await(get_erpnext_attribute_order())
    attribute_map = await _maybe_await(get_erpnext_attribute_map(erp_attr_order))

    template_variant_matrix = build_variant_matrix(erp_items, attribute_map, erp_attr_order)

    # Fallbacks if ERP matrix yields no multi-variant templates
    if not any(len(v.get("variants", [])) > 1 for v in (template_variant_matrix or {}).values()):
        fb = build_fallback_variant_matrix(erp_items)
        for k, v in fb.items():
            template_variant_matrix.setdefault(k, v)

    fb_base = build_fallback_variant_matrix_by_base(erp_items, erp_attr_order, attribute_map)
    base_or_template = fb_base if fb_base else template_variant_matrix

    unified_matrix = merge_simple_items_into_matrix(erp_items, base_or_template)

    # Attribute order for preview (based on real SKUs)
    attribute_order_for_preview = infer_global_attribute_order_from_skus(
        erp_items, attribute_map, erp_attr_order
    )

    # 3b) Ensure **attributes & terms** and **brand** (preview-only vs live)
    used_attr_vals = collect_used_attribute_values(unified_matrix)

    if dry_run:
        attribute_report = {
            "count": len(used_attr_vals),
            "attributes": [
                {"attribute": {"name": name}, "terms_preview": sorted(vals)}
                for name, vals in sorted(used_attr_vals.items())
            ],
            "dry_run": True,
        }
        brands = collect_erp_brands_from_items(erp_items)
        brand_report = {
            "attribute": {"name": "Brand"},
            "terms_preview": sorted(brands),
            "dry_run": True,
        }
    else:
        attribute_report = await ensure_wc_attributes_and_terms(used_attr_vals)
        brands = collect_erp_brands_from_items(erp_items)
        brand_report = await ensure_wc_brand_attribute_and_terms(brands)

    # 4) Woo state + cat map
    wc_products = await get_wc_products()
    wc_cat_map = build_wc_cat_map(wc_categories)

    # 5) Core preview/sync
    sync_report = await sync_all_templates_and_variants(
        variant_matrix=unified_matrix,
        wc_products=wc_products,
        wc_cat_map=wc_cat_map,
        price_map=price_map,
        attribute_map=attribute_map,
        stock_map=stock_map,
        attribute_order=attribute_order_for_preview,
        dry_run=dry_run
    )

    # 6) Save mapping
    try:
        rows = []
        for sku, m in (sync_report.get("mapping") or {}).items():
            rows.append({
                "erp_item_code": m.get("template") or sku,
                "sku": sku,
                "woo_product_id": None,
                "woo_status": None,
                "brand": m.get("brand"),
                "categories": ", ".join(m.get("categories") or []),
            })
        save_mapping_file(rows)
    except Exception as e:
        logger.error(f"Failed to save mapping file: {e}")

    logger.info("✅ [SYNC] Full sync complete (dry_run=%s)", dry_run)
    return {
        "category_report": category_report,
        "attribute_report": attribute_report,
        "brand_report": brand_report,
        "sync_report": sync_report,
        "price_list_used": price_list_name or (settings.ERP_SELLING_PRICE_LIST or "Standard Selling"),
        "attribute_order": attribute_order_for_preview,
        "dry_run": dry_run,
    }


async def sync_products_partial(skus_to_sync: List[str], dry_run: bool = False) -> Dict[str, Any]:
    logger.info("🔁 [SYNC] Partial ERPNext → Woo sync (dry_run=%s)", dry_run)

    await get_erpnext_categories()
    wc_categories = await get_wc_categories()
    await sync_categories(dry_run=dry_run)
    wc_categories = await get_wc_categories()

    erp_items = await get_erpnext_items()

    price_map, price_list_name, price_count = await resolve_price_map(get_price_map, settings.ERP_SELLING_PRICE_LIST)
    stock_map = await get_stock_map()

    erp_attr_order = await _maybe_await(get_erpnext_attribute_order())
    attribute_map = await _maybe_await(get_erpnext_attribute_map(erp_attr_order))

    template_variant_matrix = build_variant_matrix(erp_items, attribute_map, erp_attr_order)
    if not any(len(v.get("variants", [])) > 1 for v in (template_variant_matrix or {}).values()):
        fb = build_fallback_variant_matrix(erp_items)
        for k, v in fb.items():
            template_variant_matrix.setdefault(k, v)

    fb_base = build_fallback_variant_matrix_by_base(erp_items, erp_attr_order, attribute_map)
    base_or_template = fb_base if fb_base else template_variant_matrix

    unified_matrix = merge_simple_items_into_matrix(erp_items, base_or_template)
    filtered_matrix = filter_variant_matrix_by_sku(unified_matrix, skus_to_sync)

    wc_products = await get_wc_products()
    wc_cat_map = build_wc_cat_map(wc_categories)

    attribute_order_for_preview = infer_global_attribute_order_from_skus(
        erp_items, attribute_map, erp_attr_order
    )

    used_attr_vals = collect_used_attribute_values(filtered_matrix)
    if dry_run:
        attribute_report = {
            "count": len(used_attr_vals),
            "attributes": [
                {"attribute": {"name": name}, "terms_preview": sorted(vals)}
                for name, vals in sorted(used_attr_vals.items())
            ],
            "dry_run": True,
        }
        brands = collect_erp_brands_from_items(erp_items)
        brand_report = {
            "attribute": {"name": "Brand"},
            "terms_preview": sorted(brands),
            "dry_run": True,
        }
    else:
        attribute_report = await ensure_wc_attributes_and_terms(used_attr_vals)
        brands = collect_erp_brands_from_items(erp_items)
        brand_report = await ensure_wc_brand_attribute_and_terms(brands)

    sync_report = await sync_all_templates_and_variants(
        variant_matrix=filtered_matrix,
        wc_products=wc_products,
        wc_cat_map=wc_cat_map,
        price_map=price_map,
        attribute_map=attribute_map,
        stock_map=stock_map,
        attribute_order=attribute_order_for_preview,
        dry_run=dry_run
    )

    return {
        "brand_report": brand_report,
        "attribute_report": attribute_report,
        "sync_report": sync_report,
        "attribute_order": attribute_order_for_preview,
        "dry_run": dry_run
    }


async def sync_preview() -> Dict[str, Any]:
    return await sync_products_full(dry_run=True, purge_bin=False)


# =========================
# 2. Core preview/sync
# =========================

async def sync_all_templates_and_variants(
    variant_matrix: Dict[str, Dict[str, Any]],
    wc_products,
    wc_cat_map,
    price_map,
    attribute_map: Optional[Dict[str, AttributeValueMapping]] = None,
    stock_map: Optional[Dict[str, float]] = None,
    attribute_order: Optional[List[str]] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    report = {
        "created": [],
        "updated": [],
        "skipped": [],
        "errors": [],
        "mapping": {},
        "to_create": [],
        "to_update": [],
        "already_synced": [],
        "variant_parents": [],
        "variant_to_create": [],
        "variant_to_update": [],
        "variant_synced": [],
    }

    wc_product_index = {p.get("sku"): p for p in (wc_products or []) if p.get("sku")}
    seen_skus = set()

    for template_code, data in (variant_matrix or {}).items():
        template_item = data["template_item"]
        variants = data["variants"]
        attr_matrix = data.get("attribute_matrix") or [{} for _ in variants]
        is_variable = len(variants) > 1

        # Parent options (from child matrix)
        parent_attributes_payload: List[dict] = []
        if is_variable and attr_matrix:
            options_by_attr: Dict[str, set] = defaultdict(set)
            for rec in attr_matrix:
                if not isinstance(rec, dict):
                    continue
                for aname, v in rec.items():
                    if isinstance(v, dict):
                        val = v.get("value")
                        if val is not None and str(val).strip():
                            options_by_attr[aname].add(str(val).strip())
            for aname, opts in options_by_attr.items():
                opts_sorted = sorted(opts)
                if opts_sorted:
                    parent_attributes_payload.append({
                        "name": aname,
                        "options": opts_sorted,
                        "variation": True,
                        "visible": True,
                    })

        # Parent preview row
        if is_variable:
            parent_sku = template_code
            parent_wc = wc_product_index.get(parent_sku)
            report["variant_parents"].append({
                "sku": parent_sku,
                "name": template_item.get("item_name") or template_code,
                "has_variants": 1,
                "action": "Create" if not parent_wc else "Sync",
                "fields_to_update": "ALL" if not parent_wc else "None",
                "attributes": parent_attributes_payload,
            })

        for i, variant in enumerate(variants):
            sku = variant.get("item_code") or variant.get("sku") or template_code
            if sku in seen_skus:
                continue
            seen_skus.add(sku)

            attributes_entry = attr_matrix[i] if i < len(attr_matrix) else {}

            # Flatten attributes (values + abbr)
            attributes_values = {}
            attributes_abbrs = {}
            if isinstance(attributes_entry, dict):
                for attr_name, rec in attributes_entry.items():
                    if not isinstance(rec, dict):
                        continue
                    abbr = rec.get("abbr")
                    val = rec.get("value")
                    if abbr is not None:
                        attributes_abbrs[attr_name] = abbr
                    if val is not None:
                        attributes_values[attr_name] = val

            wc_prod = wc_product_index.get(sku)

            # PRICE
            price = None
            try:
                v = price_map.get(sku) if isinstance(price_map, dict) else None
                if v is None:
                    v = price_map.get(template_code) if isinstance(price_map, dict) else None
                if isinstance(v, (int, float)):
                    price = float(v)
                elif isinstance(v, str) and v.strip():
                    try:
                        price = float(v)
                    except Exception:
                        price = None
            except Exception:
                price = None

            # BRAND
            brand = extract_brand(variant, template_item, attributes_entry)

            # CATEGORY
            categories = [
                normalize_category_name(
                    variant.get("item_group") or template_item.get("item_group") or "Products"
                )
            ]
            _ = [wc_cat_map.get(cat) for cat in categories if cat in wc_cat_map]  # keep mapping warm

            # DESCRIPTION DIFF
            erp_desc = variant.get("description") or template_item.get("description") or ""
            wc_desc = wc_prod.get("description") if wc_prod else ""
            erp_desc_plain = strip_html(erp_desc)
            wc_desc_plain = strip_html(wc_desc)
            desc_diff = erp_desc_plain.strip() != wc_desc_plain.strip()

            # ============================
            # IMAGES (Featured + Gallery)
            # ============================
            erp_urls_abs: list[str] = []
            featured_rel: Optional[str] = None
            gallery_rel: list[str] = []

            if is_variable:
                # Find sibling family with same non-size attributes (style-key)
                style_key = _style_key_from_entry(attributes_entry)
                family_indices = [
                    j for j, ae in enumerate(attr_matrix)
                    if _style_key_from_entry(ae) == style_key
                ]
                family_skus = []
                for j in family_indices:
                    code = variants[j].get("item_code") or variants[j].get("sku") or template_code
                    if code:
                        family_skus.append(code)

                # Featured = this variant's Item.image
                featured_rel = await _erp_get_featured(sku)

                # Gallery = intersection of File rows across family (excluding image/website_image)
                rows = await _erp_get_file_rows_for_items(family_skus)
                per_file: dict[str, set] = {}
                created_at: dict[str, str] = {}
                for row in rows:
                    fu = row.get("file_url")
                    fld = (row.get("attached_to_field") or "").lower()
                    name = row.get("attached_to_name")
                    crt = row.get("creation")
                    if not fu or fld in {"image", "website_image"}:
                        continue
                    per_file.setdefault(fu, set()).add(name)
                    if fu not in created_at or (crt and str(crt) < str(created_at[fu])):
                        created_at[fu] = crt or ""

                for fu, names in per_file.items():
                    if len(names) == len(set(family_skus)):
                        if not featured_rel or fu != featured_rel:
                            gallery_rel.append(fu)

                # Stable order by earliest creation
                gallery_rel.sort(key=lambda fu: created_at.get(fu, "") or fu)
            else:
                # Simple item: featured = Item.image; gallery = other File rows on the same item
                featured_rel = await _erp_get_featured(sku)
                rows = await _erp_get_file_rows_for_items([sku])
                for row in rows:
                    fu = row.get("file_url")
                    fld = (row.get("attached_to_field") or "").lower()
                    if not fu or fld in {"image", "website_image"}:
                        continue
                    if featured_rel and fu == featured_rel:
                        continue
                    gallery_rel.append(fu)

            # Compose absolute URLs (featured first)
            if featured_rel:
                erp_urls_abs.append(_abs_erp_file_url(featured_rel))
            for fu in gallery_rel:
                absu = _abs_erp_file_url(fu)
                if absu and absu not in erp_urls_abs:
                    erp_urls_abs.append(absu)

            # Sizes for preview
            erp_sizes = await _head_sizes_for_urls(erp_urls_abs) if erp_urls_abs else []
            erp_gallery = [{"url": u, "size": (erp_sizes[idx] if idx < len(erp_sizes) else 0)} for idx, u in enumerate(erp_urls_abs)]

            # Woo state
            wc_gallery = normalize_gallery_from_wc_product(wc_prod or {})
            gallery_diff = not gallery_images_equal(erp_gallery, wc_gallery)

            # ============================
            # STOCK (sum all warehouses)
            # ============================
            stock_q = None
            try:
                total = 0.0
                found = False
                if isinstance(stock_map, dict):
                    for (code, _wh), q in stock_map.items():
                        if code == sku:
                            try:
                                total += float(q or 0)
                            except Exception:
                                pass
                            found = True
                stock_q = total if found else None
            except Exception:
                stock_q = None

            if stock_q is None:
                for key in ("stock_qty", "actual_qty", "available_qty", "qty", "quantity"):
                    v = variant.get(key) or template_item.get(key)
                    if v is not None:
                        try:
                            stock_q = float(v)
                            break
                        except Exception:
                            pass

            # Decide preview action
            update_fields = []
            if desc_diff:
                update_fields.append("description")
            if gallery_diff:
                update_fields.append("gallery_images")
            if (price is not None) and wc_prod and (str(price) != str(wc_prod.get("regular_price"))):
                update_fields.append("price")

            needs_create = wc_prod is None
            needs_update = bool(update_fields) and not needs_create

            preview_entry = {
                "sku": sku,
                "name": variant.get("item_name") or template_item.get("item_name") or sku,
                "regular_price": price,
                "stock_quantity": stock_q,
                "categories": categories,
                "brand": brand,
                "attributes": attributes_values,
                "attr_abbr": attributes_abbrs,
                "erp_img_sizes": [img["size"] for img in erp_gallery],
                "wc_img_sizes": [img["size"] for img in wc_gallery],
                "gallery_diff": gallery_diff,
                "description_diff": desc_diff,
                "has_variants": int(is_variable),
                "action": "Create" if needs_create else ("Update" if needs_update else "Synced"),
                "fields_to_update": "ALL" if needs_create else (update_fields or []),
            }

            if needs_create:
                report["to_create"].append(preview_entry)
                if is_variable:
                    report["variant_to_create"].append(preview_entry)
            elif needs_update:
                report["to_update"].append(preview_entry)
                if is_variable:
                    report["variant_to_update"].append(preview_entry)
            else:
                report["already_synced"].append(preview_entry)
                if is_variable:
                    report["variant_synced"].append(preview_entry)

            # Real side effects (upload images to WP media) — only when not dry_run
            if not dry_run and erp_gallery:
                media_ids = []
                for img in erp_gallery:
                    try:
                        fname = basename(img["url"])
                        media_id = await ensure_wp_image_uploaded(img["url"], fname)
                        if media_id:
                            media_ids.append(media_id)
                    except Exception as e:
                        logger.error(f"Image upload failed for {sku}: {e}")
                # TODO: create/update Woo product with media_ids + attribute terms

            # Mapping
            report["mapping"][sku] = {
                "template": template_code,
                "attributes": attributes_values,
                "brand": brand,
                "categories": categories
            }

    return report

