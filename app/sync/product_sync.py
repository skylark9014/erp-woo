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
import asyncio

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
    save_preview_to_file,
    reconcile_woocommerce_brands,
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
# 1. Purge Woo BIN (option)
# =========================

async def purge_woo_bin_if_needed(auto_purge: bool = True):
    if not auto_purge:
        return
    logger.info("🗑️  Purging WooCommerce bin/trash before sync...")
    try:
        await purge_wc_bin_products()
    except Exception as e:
        logger.error(f"Failed to purge Woo bin: {e}")

# =========================
# 2. Sync Entry Points
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
    # 🚫 Never treat Brand as a Woo *product attribute*
    used_attr_vals = {k: v for k, v in used_attr_vals.items() if str(k).strip().lower() != "brand"}

    if dry_run:
        attribute_report = {
            "count": len(used_attr_vals),
            "attributes": [
                {"attribute": {"name": name}, "terms_preview": sorted(vals)}
                for name, vals in sorted(used_attr_vals.items())
            ],
            "dry_run": True,
        }
        # 🔎 Preview the taxonomy reconciliation (no changes)
        brands = collect_erp_brands_from_items(erp_items)
        brand_report = await reconcile_woocommerce_brands(
            brands,
            delete_missing=False,   # flip to True if you want to preview deletions too
            dry_run=True
        )
    else:
        attribute_report = await ensure_wc_attributes_and_terms(used_attr_vals)
        # ✅ Live taxonomy reconciliation for brands (create/update; set delete_missing=True to remove extras)
        brands = collect_erp_brands_from_items(erp_items)
        brand_report = await reconcile_woocommerce_brands(
            brands,
            delete_missing=False,   # set True if you want to delete Woo brands not in ERP
            dry_run=False
        )

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

    # 5b) 🚀 Create (and lightly update) products based on preview when not dry run
    if not dry_run:
        from app.woocommerce import create_wc_product, update_wc_product, ensure_wp_image_uploaded
        try:
            from app.sync_utils import get_brand_id_map
            brand_id_map = await get_brand_id_map()
            brand_id_map_lc = {str(k).lower(): v for k, v in (brand_id_map or {}).items()}
        except Exception:
            brand_id_map_lc = {}

        wc_index = {p.get("sku"): p for p in (wc_products or []) if p.get("sku")}

        def _fmt_price(v) -> str:
            try:
                return f"{float(v):.2f}"
            except Exception:
                return "0.00"

        for row in (sync_report.get("to_create") or []):
            try:
                sku = row.get("sku")
                if not sku or wc_index.get(sku):
                    continue

                payload = {
                    "name": row.get("name") or sku,
                    "sku": sku,
                    "type": "simple",
                    "status": "publish",
                }
                if row.get("regular_price") is not None:
                    payload["regular_price"] = _fmt_price(row.get("regular_price"))

                if row.get("stock_quantity") is not None:
                    try:
                        payload["manage_stock"] = True
                        payload["stock_quantity"] = int(float(row.get("stock_quantity") or 0))
                    except Exception:
                        payload["manage_stock"] = False

                # Categories
                cat_ids = []
                for cname in (row.get("categories") or []):
                    cid = wc_cat_map.get(cname)
                    if cid:
                        cat_ids.append({"id": cid})
                if cat_ids:
                    payload["categories"] = cat_ids

                # Attributes (exclude Brand)
                attrs = []
                for aname, aval in (row.get("attributes") or {}).items():
                    if str(aname).strip().lower() == "brand":
                        continue
                    if aval is not None and str(aval).strip():
                        attrs.append({
                            "name": aname,
                            "visible": True,
                            "options": [str(aval).strip()]
                        })
                if attrs:
                    payload["attributes"] = attrs

                # Brand taxonomy
                bname = row.get("brand")
                if bname:
                    bid = brand_id_map_lc.get(str(bname).lower())
                    if bid:
                        payload["brands"] = [{"id": int(bid)}]

                # 📸 Images: featured + gallery for this SKU
                try:
                    erp_urls_abs: list[str] = []
                    featured_rel = await _erp_get_featured(sku)
                    if featured_rel:
                        erp_urls_abs.append(_abs_erp_file_url(featured_rel))

                    file_rows = await _erp_get_file_rows_for_items([sku])
                    for frow in file_rows:
                        fu = frow.get("file_url")
                        fld = (frow.get("attached_to_field") or "").lower()
                        if not fu or fld in {"image", "website_image"}:
                            continue
                        absu = _abs_erp_file_url(fu)
                        if absu and absu not in erp_urls_abs:
                            erp_urls_abs.append(absu)

                    media_ids = []
                    for u in erp_urls_abs:
                        try:
                            mid = await ensure_wp_image_uploaded(u, basename(u))
                            if mid:
                                media_ids.append(mid)
                        except Exception as ie:
                            logger.error(f"[IMAGES] Upload failed for {sku}: {ie}")

                    if media_ids:
                        payload["images"] = [{"id": mid, "position": idx} for idx, mid in enumerate(media_ids)]
                except Exception as eimg:
                    logger.error(f"[IMAGES] Collecting/attaching images failed for {sku}: {eimg}")

                # Create
                resp = await create_wc_product(payload)
                product = resp.get("data") if isinstance(resp, dict) and "data" in resp else resp

                if isinstance(product, dict) and product.get("id"):
                    logger.info(f"[CREATE] Woo product created (sku={sku}, id={product.get('id')})")
                    wc_index[sku] = product  # store unwrapped product
                else:
                    logger.error(f"[CREATE] Woo product failed (sku={sku}): {resp}")
            except Exception as e:
                logger.error(f"[CREATE] Error creating SKU {row.get('sku')}: {e}")

        for row in (sync_report.get("to_update") or []):
            try:
                sku = row.get("sku")
                wp = wc_index.get(sku)
                if not sku or not wp:
                    continue
                upd = {}
                if row.get("regular_price") is not None:
                    upd["regular_price"] = _fmt_price(row.get("regular_price"))
                if row.get("stock_quantity") is not None:
                    try:
                        upd["manage_stock"] = True
                        upd["stock_quantity"] = int(float(row.get("stock_quantity") or 0))
                    except Exception:
                        pass
                if upd:
                    resp = await update_wc_product(wp.get("id"), upd)
                    product = resp.get("data") if isinstance(resp, dict) and "data" in resp else resp
                    if isinstance(product, dict) and product.get("id"):
                        logger.info(f"[UPDATE] Woo product updated (sku={sku}, id={product.get('id')})")
                    else:
                        logger.error(f"[UPDATE] Woo product update failed (sku={sku}): {resp}")
            except Exception as e:
                logger.error(f"[UPDATE] Error updating SKU {row.get('sku')}: {e}")

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

    # 7) Save Sync Preview
    try:
        save_preview_to_file(sync_report)
    except Exception as e:
        logger.error(f"Failed to save Partial Sync reference file: {e}")

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
    # 🚫 Never treat Brand as a Woo *product attribute*
    used_attr_vals = {k: v for k, v in used_attr_vals.items() if str(k).strip().lower() != "brand"}

    if dry_run:
        attribute_report = {
            "count": len(used_attr_vals),
            "attributes": [
                {"attribute": {"name": name}, "terms_preview": sorted(vals)}
                for name, vals in sorted(used_attr_vals.items())
            ],
            "dry_run": True,
        }
        # 🔎 Preview brand taxonomy reconciliation
        brands = collect_erp_brands_from_items(erp_items)
        brand_report = await reconcile_woocommerce_brands(
            brands,
            delete_missing=False,   # flip to True if you want to preview deletions too
            dry_run=True
        )
    else:
        attribute_report = await ensure_wc_attributes_and_terms(used_attr_vals)
        # ✅ Live brand taxonomy reconciliation
        brands = collect_erp_brands_from_items(erp_items)
        brand_report = await reconcile_woocommerce_brands(
            brands,
            delete_missing=False,   # set True if you want to delete Woo brands not in ERP
            dry_run=False
        )

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

    # 5b) 🚀 Create (and lightly update) products for requested SKUs when not dry run
    if not dry_run:
        from app.woocommerce import create_wc_product, update_wc_product, ensure_wp_image_uploaded
        try:
            from app.sync_utils import get_brand_id_map
            brand_id_map = await get_brand_id_map()
            brand_id_map_lc = {str(k).lower(): v for k, v in (brand_id_map or {}).items()}
        except Exception:
            brand_id_map_lc = {}

        wc_index = {p.get("sku"): p for p in (wc_products or []) if p.get("sku")}

        def _fmt_price(v) -> str:
            try:
                return f"{float(v):.2f}"
            except Exception:
                return "0.00"

        requested = set(skus_to_sync or [])

        for row in (sync_report.get("to_create") or []):
            sku = row.get("sku")
            if requested and sku not in requested:
                continue
            try:
                if not sku or wc_index.get(sku):
                    continue
                payload = {
                    "name": row.get("name") or sku,
                    "sku": sku,
                    "type": "simple",
                    "status": "publish",
                }
                if row.get("regular_price") is not None:
                    payload["regular_price"] = _fmt_price(row.get("regular_price"))
                if row.get("stock_quantity") is not None:
                    try:
                        payload["manage_stock"] = True
                        payload["stock_quantity"] = int(float(row.get("stock_quantity") or 0))
                    except Exception:
                        payload["manage_stock"] = False

                # Categories
                cat_ids = []
                for cname in (row.get("categories") or []):
                    cid = wc_cat_map.get(cname)
                    if cid:
                        cat_ids.append({"id": cid})
                if cat_ids:
                    payload["categories"] = cat_ids

                # Attributes (exclude Brand)
                attrs = []
                for aname, aval in (row.get("attributes") or {}).items():
                    if str(aname).strip().lower() == "brand":
                        continue
                    if aval is not None and str(aval).strip():
                        attrs.append({
                            "name": aname,
                            "visible": True,
                            "options": [str(aval).strip()]
                        })
                if attrs:
                    payload["attributes"] = attrs

                # Brand taxonomy
                bname = row.get("brand")
                if bname:
                    bid = brand_id_map_lc.get(str(bname).lower())
                    if bid:
                        payload["brands"] = [{"id": int(bid)}]

                # 📸 Images for this SKU
                try:
                    erp_urls_abs: list[str] = []
                    featured_rel = await _erp_get_featured(sku)
                    if featured_rel:
                        erp_urls_abs.append(_abs_erp_file_url(featured_rel))

                    file_rows = await _erp_get_file_rows_for_items([sku])
                    for frow in file_rows:
                        fu = frow.get("file_url")
                        fld = (frow.get("attached_to_field") or "").lower()
                        if not fu or fld in {"image", "website_image"}:
                            continue
                        absu = _abs_erp_file_url(fu)
                        if absu and absu not in erp_urls_abs:
                            erp_urls_abs.append(absu)

                    media_ids = []
                    for u in erp_urls_abs:
                        try:
                            mid = await ensure_wp_image_uploaded(u, basename(u))
                            if mid:
                                media_ids.append(mid)
                        except Exception as ie:
                            logger.error(f"[IMAGES] Upload failed for {sku}: {ie}")

                    if media_ids:
                        payload["images"] = [{"id": mid, "position": idx} for idx, mid in enumerate(media_ids)]
                except Exception as eimg:
                    logger.error(f"[IMAGES] Collecting/attaching images failed for {sku}: {eimg}")

                # Create
                resp = await create_wc_product(payload)
                product = resp.get("data") if isinstance(resp, dict) and "data" in resp else resp

                if isinstance(product, dict) and product.get("id"):
                    logger.info(f"[CREATE] Woo product created (sku={sku}, id={product.get('id')})")
                    wc_index[sku] = product
                else:
                    logger.error(f"[CREATE] Woo product failed (sku={sku}): {resp}")
            except Exception as e:
                logger.error(f"[CREATE] Error creating SKU {sku}: {e}")

        for row in (sync_report.get("to_update") or []):
            sku = row.get("sku")
            if requested and sku not in requested:
                continue
            try:
                wp = wc_index.get(sku)
                if not sku or not wp:
                    continue
                upd = {}
                if row.get("regular_price") is not None:
                    upd["regular_price"] = _fmt_price(row.get("regular_price"))
                if row.get("stock_quantity") is not None:
                    try:
                        upd["manage_stock"] = True
                        upd["stock_quantity"] = int(float(row.get("stock_quantity") or 0))
                    except Exception:
                        pass
                if upd:
                    resp = await update_wc_product(wp.get("id"), upd)
                    product = resp.get("data") if isinstance(resp, dict) and "data" in resp else resp
                    if isinstance(product, dict) and product.get("id"):
                        logger.info(f"[UPDATE] Woo product updated (sku={sku}, id={product.get('id')})")
                    else:
                        logger.error(f"[UPDATE] Woo product update failed (sku={sku}): {resp}")
            except Exception as e:
                logger.error(f"[UPDATE] Error updating SKU {sku}: {e}")

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
# 3. Core preview/sync
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
    """
    RULES (ERPNext → Woo):
      • Simple vs Variable:
          - If ERP Item ID has exactly 2 parts (e.g., 'PST-EVRST') → Simple product.
          - If ERP Item ID has 3+ parts (e.g., 'SVR-ANDES-MEDIUM') → Variable product:
              parent SKU = first two parts joined ('SVR-ANDES'), variations = remaining parts.
          - Even if only one child exists, still create a variable parent with a single variation.
          - Detection uses SKU structure (3+ parts ⇒ variable), not the number of ERP items.
      • Attribute mapping for variable:
          - DROP the 2nd ERP part (type like 'ANDES') as a Woo attribute (it’s encoded in the parent SKU).
          - All parts after the 2nd become Woo attributes. In current data this is primarily 'Sheet Size'.
          - Normalize size labels: "X" or "×" → " x ", collapse whitespace.
      • Brands handled via taxonomy 'product_brand' (not Woo product attributes).
      • Image strategy:
          - Variable parent gallery: intersection across siblings (excluding Item.image/website_image).
            If empty, fallback to first variant’s featured + its attachments.
            **Patched:** even if intersection exists, we *top up* with first variant’s featured+attachments (dedup).
          - Variation image: featured of that variant.
          - Simple product: featured + its attachments (excluding featured) in creation order.
      • Image linking: always send Woo 'images' (parent/simple) and 'image' (variation) with media IDs.
        If Woo ignores them on first call, do a correcting PUT.
    """

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
    touched_skus = set()
    variation_skus_seen: set[str] = set()

    # --- helpers ---
    WC_API = settings.WC_BASE_URL.rstrip("/") + "/wp-json/wc/v3"
    WP_BRAND_API = settings.WC_BASE_URL.rstrip("/") + "/wp-json/wp/v2/product_brand"

    brand_id_cache: dict[str, int] = {}

    # compact result packers (avoid HTML log spam)
    def _pack_http_result(r):
        ctype = r.headers.get("content-type", "") or ""
        is_json = ("application/json" in ctype) or ctype.startswith("application/json")
        payload = {}
        code = None
        message = None
        if is_json:
            try:
                payload = r.json() or {}
                code = payload.get("code")
                message = payload.get("message")
            except Exception:
                payload = {}
        excerpt = ""
        if not is_json:
            t = (r.text or "").strip().splitlines()
            excerpt = (t[0] if t else "")[:200]
        return {
            "status_code": r.status_code,
            "content_type": ctype,
            "data": payload,
            "code": code,
            "message": message,
            "raw_excerpt": excerpt,
        }

    def _resp_summary(res: dict) -> str:
        ctype = res.get("content_type") or ""
        if "application/json" in ctype:
            c = res.get("code") or (res.get("data") or {}).get("code")
            m = res.get("message") or (res.get("data") or {}).get("message")
            m = (str(m)[:160] + "…") if m and len(str(m)) > 160 else (m or "")
            return f"{res.get('status_code')} json code={c} msg={m}"
        ex = res.get("raw_excerpt") or ""
        return f"{res.get('status_code')} {ctype} {ex}"

    async def _request_with_retry(method: str, url: str, *, auth=None, json=None, max_attempts: int = 3, timeout: float = 40.0):
        import asyncio
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, verify=False, auth=auth) as client:
                    if method == "GET":
                        return await client.get(url)
                    elif method == "POST":
                        return await client.post(url, json=json)
                    elif method == "PUT":
                        return await client.put(url, json=json)
                    elif method == "DELETE":
                        return await client.delete(url, json=json)
                    else:
                        raise ValueError(f"Unsupported method: {method}")
            except Exception as e:
                last_exc = e
                delay = 0.5 * (2 ** (attempt - 1))
                logger.warning(f"[HTTP RETRY] {method} {url} failed (attempt {attempt}/{max_attempts}): {e}. Retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
        raise last_exc

    async def _upload_with_retry(url: str, fname: str, tries: int = 3):
        import asyncio
        last_exc = None
        for attempt in range(1, tries + 1):
            try:
                return await ensure_wp_image_uploaded(url, fname)
            except Exception as e:
                last_exc = e
                delay = 0.5 * (2 ** (attempt - 1))
                logger.warning("[IMG][RETRY] upload %s failed (%s/%s): %s; retrying in %.1fs", fname, attempt, tries, e, delay)
                await asyncio.sleep(delay)
        raise last_exc

    async def _load_brand_id_cache():
        if brand_id_cache:
            return
        auth = (settings.WP_USERNAME, settings.WP_PASSWORD)
        page = 1
        async with httpx.AsyncClient(timeout=20.0, verify=False, auth=auth) as client:
            while True:
                r = await client.get(f"{WP_BRAND_API}?per_page=100&page={page}")
                if r.status_code != 200:
                    break
                arr = r.json() or []
                if not arr:
                    break
                for b in arr:
                    name = (b.get("name") or "").strip()
                    bid = b.get("id")
                    if name and bid:
                        brand_id_cache[name.lower()] = int(bid)
                if len(arr) < 100:
                    break
                page += 1

    def _brand_payload(brand_name: Optional[str]) -> list[dict]:
        if not brand_name:
            return []
        bid = brand_id_cache.get(str(brand_name).strip().lower())
        return [{"id": bid}] if bid else []

    def _price_str(v: Optional[float]) -> Optional[str]:
        if v is None:
            return None
        try:
            return f"{float(v):.2f}"
        except Exception:
            return None

    def _sizes_from_wc_gallery(wc_gallery_obj) -> list[int]:
        out: list[int] = []
        if isinstance(wc_gallery_obj, list):
            for g in wc_gallery_obj:
                if isinstance(g, dict):
                    s = g.get("size", 0)
                    try:
                        out.append(int(s))
                    except Exception:
                        out.append(0)
                elif isinstance(g, (int, float)):
                    try:
                        out.append(int(g))
                    except Exception:
                        out.append(0)
                else:
                    out.append(0)
        return out

    def _sku_parts(s: str) -> list[str]:
        return [p for p in (s or "").split("-") if p]

    def _family_is_variable(variants, template_code: str, wc_product_index: dict) -> bool:
        if any(len(_sku_parts(v.get("item_code") or v.get("sku") or template_code)) >= 3 for v in (variants or [])):
            return True
        existing = wc_product_index.get(template_code)
        if existing and (existing.get("type") or "").lower() == "variable":
            return True
        return False

    async def _get_product_by_sku(sku: str) -> Optional[dict]:
        from urllib.parse import quote_plus
        auth = (settings.WC_API_KEY, settings.WC_API_SECRET)
        url = f"{WC_API}/products?sku={quote_plus(sku)}"
        r = await _request_with_retry("GET", url, auth=auth, max_attempts=3, timeout=30.0)
        if r.status_code == 200:
            arr = r.json() or []
            if arr:
                return arr[0]
        return None

    async def _get_product_by_id(pid: int) -> Optional[dict]:
        auth = (settings.WC_API_KEY, settings.WC_API_SECRET)
        url = f"{WC_API}/products/{pid}"
        r = await _request_with_retry("GET", url, auth=auth, max_attempts=3, timeout=30.0)
        if r.status_code in (200, 201):
            return r.json()
        return None

    async def _get_variations_map(product_id: int) -> dict:
        out = {}
        auth = (settings.WC_API_KEY, settings.WC_API_SECRET)
        page = 1
        while True:
            r = await _request_with_retry(
                "GET",
                f"{WC_API}/products/{product_id}/variations?per_page=100&page={page}",
                auth=auth, max_attempts=3, timeout=40.0
            )
            if r.status_code != 200:
                break
            arr = r.json() or []
            if not arr:
                break
            for v in arr:
                sku = (v.get("sku") or "").strip()
                if sku:
                    out[sku] = v
                for a in v.get("attributes", []):
                    if (a.get("name") or "").strip().lower() == "sheet size":
                        opt = (a.get("option") or "").strip()
                        if opt:
                            out[f"size::{opt.lower()}"] = v
            if len(arr) < 100:
                break
            page += 1
        return out

    async def _create_or_update_product_by_sku(sku: str, payload: dict) -> dict:
        """Create or update Woo product by SKU, with pre-flight, collision fallback, and a guard to block variation SKUs."""
        auth = (settings.WC_API_KEY, settings.WC_API_SECRET)

        # Never create/update a top-level product for a variation-like SKU.
        parts = [p for p in (sku or "").split("-") if p]
        if (payload.get("type") != "variable") and (len(parts) >= 3 or sku in variation_skus_seen):
            logger.warning("[BLOCK] Top-level product call blocked for variation SKU %s", sku)
            return {
                "status_code": 409,
                "content_type": "application/json",
                "data": {"code": "blocked_variation_sku", "message": "SKU belongs to a variable product's variation"},
                "raw_excerpt": "",
            }

        # Avoid double work in this run
        if sku in touched_skus and sku in wc_product_index:
            pid = wc_product_index[sku]["id"]
            r = await _request_with_retry("PUT", f"{WC_API}/products/{pid}", auth=auth, json=payload)
            data = _pack_http_result(r)
            if r.status_code in (200, 201):
                wc_product_index[sku] = data["data"]
                logger.info(f"[UPDATE] Woo product updated (sku={sku}, id={wc_product_index[sku]['id']})")
            else:
                logger.error(f"[WC] update product {_resp_summary(data)}")
            return data

        # Pre-flight: check by SKU on Woo (important for re-runs)
        if sku not in wc_product_index:
            found = await _get_product_by_sku(sku)
            if found:
                wc_product_index[sku] = found

        # Create if still missing
        if sku not in wc_product_index:
            r = await _request_with_retry("POST", f"{WC_API}/products", auth=auth, json=payload)
            data = _pack_http_result(r)
            if r.status_code in (200, 201):
                prod = data["data"]
                wc_product_index[sku] = prod
                touched_skus.add(sku)
                logger.info(f"[CREATE] Woo product created (sku={sku}, id={prod['id']})")
                return data

            # Duplicate SKU → treat as "exists": PUT using resource_id
            body = data.get("data") or {}
            if body.get("code") == "product_invalid_sku":
                rid = (body.get("data") or {}).get("resource_id")
                logger.info(f"[CREATE->UPDATE] {sku} exists (rid={rid}); switching to PUT")
                recovered = await _get_product_by_sku(sku)
                if not recovered and isinstance(rid, int):
                    recovered = await _get_product_by_id(rid)
                if recovered:
                    wc_product_index[sku] = recovered
                    pid = recovered["id"]
                    r2 = await _request_with_retry("PUT", f"{WC_API}/products/{pid}", auth=auth, json=payload)
                    data2 = _pack_http_result(r2)
                    if r2.status_code in (200, 201):
                        wc_product_index[sku] = data2["data"]
                        touched_skus.add(sku)
                        logger.info(f"[UPDATE] Woo product updated (sku={sku}, id={wc_product_index[sku]['id']})")
                    else:
                        logger.error(f"[WC] update after duplicate {_resp_summary(data2)}")
                    return data2

            # Some other error
            return data

        # Update path
        pid = wc_product_index[sku]["id"]
        r = await _request_with_retry("PUT", f"{WC_API}/products/{pid}", auth=auth, json=payload)
        data = _pack_http_result(r)
        if r.status_code in (200, 201):
            wc_product_index[sku] = data["data"]
            touched_skus.add(sku)
            logger.info(f"[UPDATE] Woo product updated (sku={sku}, id={wc_product_index[sku]['id']})")
        else:
            logger.error(f"[WC] update product {_resp_summary(data)}")
        return data

    async def _create_or_update_variation(parent_id: int, sku: str, size_option: str, payload: dict, var_map: dict) -> dict:
        auth = (settings.WC_API_KEY, settings.WC_API_SECRET)
        existing = var_map.get(sku) or var_map.get(f"size::{(size_option or '').lower()}")
        if existing:
            vid = existing["id"]
            r = await _request_with_retry("PUT", f"{WC_API}/products/{parent_id}/variations/{vid}", auth=auth, json=payload)
            data = _pack_http_result(r)
            if r.status_code in (200, 201):
                logger.info(f"[VAR][UPDATE] sku={sku} (vid={vid})")
            else:
                logger.error(f"[WC] update variation {_resp_summary(data)}")
            return data
        r = await _request_with_retry("POST", f"{WC_API}/products/{parent_id}/variations", auth=auth, json=payload)
        data = _pack_http_result(r)
        if r.status_code in (200, 201):
            vid = data["data"]["id"]
            logger.info(f"[VAR][CREATE] sku={sku} (pid={parent_id})")
        else:
            logger.error(f"[WC] create variation {_resp_summary(data)}")
        return data

    def _normalize_size_label(val: str) -> str:
        import re
        s = str(val or "")
        s = re.sub(r"\s*[xX×]\s*", " x ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    # Preload brand ids once (for parent/simple)
    await _load_brand_id_cache()

    for template_code, data in (variant_matrix or {}).items():
        template_item = data["template_item"]
        variants = data["variants"]
        attr_matrix = data.get("attribute_matrix") or [{} for _ in variants]

        # decide variable by SKU parts, not count
        is_variable = _family_is_variable(variants, template_code, wc_product_index)

        # Build parent options (drop 2nd ID part; keep 'Sheet Size' etc.)
        options_by_attr: Dict[str, set] = defaultdict(set)
        for rec in (attr_matrix or []):
            if not isinstance(rec, dict):
                continue
            for aname, v in rec.items():
                if not isinstance(v, dict):
                    continue
                val = v.get("value")
                if val is None or not str(val).strip():
                    continue
                if str(aname).strip().lower() == "sheet size":
                    options_by_attr[aname].add(_normalize_size_label(val))
                else:
                    options_by_attr[aname].add(str(val).strip())

        sheet_sizes = sorted(options_by_attr.get("Sheet Size", set()))

        logger.info(f"[FAMILY] parent={template_code} items={len(variants)} variable={bool(is_variable)}")
        if is_variable:
            parent_sku = template_code
            parent_wc = wc_product_index.get(parent_sku)
            parent_attrs_for_preview = [{
                "name": "Sheet Size",
                "options": sheet_sizes if sheet_sizes else [],
                "variation": True,
                "visible": True,
            }]
            logger.info("[ATTR][PARENT] %s attrs=['Sheet Size'] options=%s", parent_sku, {"Sheet Size": sheet_sizes})
            report["variant_parents"].append({
                "sku": parent_sku,
                "name": template_item.get("item_name") or template_code,
                "has_variants": 1,
                "action": "Create" if not parent_wc else "Sync",
                "fields_to_update": "ALL" if not parent_wc else "None",
                "attributes": parent_attrs_for_preview,
            })

        # ---- parent gallery (variable) ----
        parent_media_ids: list[int] = []
        parent_images_payload: list[dict] = []
        family_brand = None

        family_rows = []
        family_skus = []
        if is_variable:
            for v in variants:
                code = v.get("item_code") or v.get("sku") or template_code
                if code:
                    family_skus.append(code)
            family_rows = await _erp_get_file_rows_for_items(family_skus)

            # 1) Common images across all variants (excluding Item.image / website_image)
            per_file: dict[str, set] = {}
            created_at: dict[str, str] = {}
            for row in family_rows:
                fu = row.get("file_url")
                fld = (row.get("attached_to_field") or "").lower()
                name = row.get("attached_to_name")
                crt = row.get("creation")
                if not fu or fld in {"image", "website_image"}:
                    continue
                per_file.setdefault(fu, set()).add(name)
                if fu not in created_at or (crt and str(crt) < str(created_at[fu])):
                    created_at[fu] = crt or ""
            common_rel = []
            if family_skus:
                total = len(set(family_skus))
                for fu, names in per_file.items():
                    if len(names) == total:
                        common_rel.append(fu)
            common_rel.sort(key=lambda fu: created_at.get(fu, "") or fu)

            # 2) First-variant featured + attachments (fallback/top-up)
            fallback_rel = []
            if family_skus:
                first_code = family_skus[0]
                first_feat = await _erp_get_featured(first_code)
                rows_first = await _erp_get_file_rows_for_items([first_code])
                created_at_f: dict[str, str] = {}
                ordered: list[str] = []
                if first_feat:
                    ordered.append(first_feat)
                for row in rows_first:
                    fu = row.get("file_url")
                    fld = (row.get("attached_to_field") or "").lower()
                    crt = row.get("creation")
                    if not fu or fld in {"image", "website_image"}:
                        continue
                    if first_feat and fu == first_feat:
                        continue
                    if fu not in created_at_f or (crt and str(crt) < str(created_at_f[fu])):
                        created_at_f[fu] = crt or ""
                    ordered.append(fu)
                # preserve order; featured first
                seen_fu = set()
                for fu in ordered:
                    if fu not in seen_fu:
                        seen_fu.add(fu)
                        fallback_rel.append(fu)

            # 3) Combine: common first, then fallback extras (dedup)
            parent_gallery_rel: list[str] = []
            seen_all = set()
            for fu in (common_rel + fallback_rel):
                if fu not in seen_all:
                    seen_all.add(fu)
                    parent_gallery_rel.append(fu)

            if not dry_run and parent_gallery_rel:
                media_ids = []
                for fu in parent_gallery_rel:
                    absu = _abs_erp_file_url(fu)
                    try:
                        mid = await _upload_with_retry(absu, basename(absu))
                        if mid:
                            media_ids.append(int(mid))
                    except Exception as e:
                        logger.warning(f"[IMG][PARENT] upload failed for {template_code}: {e}")
                parent_media_ids = media_ids[:]
                parent_images_payload = [{"id": mid, "position": idx} for idx, mid in enumerate(media_ids)]
                logger.info(f"[IMG][PARENT] {template_code} linked {len(parent_images_payload)} images (common={len(common_rel)}, extra={max(0, len(parent_gallery_rel)-len(common_rel))})")

        # --- iterate children ---
        parent_id_for_vars: Optional[int] = None
        existing_var_map: dict = {}

        for i, variant in enumerate(variants):
            sku = variant.get("item_code") or variant.get("sku") or template_code
            if sku in seen_skus:
                continue
            seen_skus.add(sku)

            if is_variable:
                variation_skus_seen.add(sku)

            attributes_entry = attr_matrix[i] if i < len(attr_matrix) else {}

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
                        if str(attr_name).strip().lower() == "sheet size":
                            attributes_values[attr_name] = _normalize_size_label(val)
                        else:
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
            if family_brand is None and brand:
                family_brand = brand

            # CATEGORY
            categories = [
                normalize_category_name(
                    variant.get("item_group") or template_item.get("item_group") or "Products"
                )
            ]
            _ = [wc_cat_map.get(cat) for cat in categories if cat in wc_cat_map]

            # DESCRIPTION DIFF
            erp_desc = variant.get("description") or template_item.get("description") or ""
            wc_desc = wc_prod.get("description") if wc_prod else ""
            erp_desc_plain = strip_html(erp_desc)
            wc_desc_plain = strip_html(wc_desc)
            desc_diff = erp_desc_plain.strip() != wc_desc_plain.strip()

            # IMAGES
            erp_urls_abs: list[str] = []
            featured_rel: Optional[str] = None
            gallery_rel: list[str] = []

            if is_variable:
                featured_rel = await _erp_get_featured(sku)
                rows = await _erp_get_file_rows_for_items([sku])
                created_at_v: dict[str, str] = {}
                for row in rows:
                    fu = row.get("file_url")
                    fld = (row.get("attached_to_field") or "").lower()
                    crt = row.get("creation")
                    if not fu or fld in {"image", "website_image"}:
                        continue
                    if featured_rel and fu == featured_rel:
                        continue
                    if fu not in created_at_v or (crt and str(crt) < str(created_at_v[fu])):
                        created_at_v[fu] = crt or ""
                    gallery_rel.append(fu)
                gallery_rel = list(dict.fromkeys(gallery_rel))
                gallery_rel.sort(key=lambda fu: created_at_v.get(fu, "") or fu)
            else:
                featured_rel = await _erp_get_featured(sku)
                rows = await _erp_get_file_rows_for_items([sku])
                created_at_v: dict[str, str] = {}
                for row in rows:
                    fu = row.get("file_url")
                    fld = (row.get("attached_to_field") or "").lower()
                    crt = row.get("creation")
                    if not fu or fld in {"image", "website_image"}:
                        continue
                    if featured_rel and fu == featured_rel:
                        continue
                    if fu not in created_at_v or (crt and str(crt) < str(created_at_v[fu])):
                        created_at_v[fu] = crt or ""
                    gallery_rel.append(fu)
                gallery_rel = list(dict.fromkeys(gallery_rel))
                gallery_rel.sort(key=lambda fu: created_at_v.get(fu, "") or fu)

            if featured_rel:
                erp_urls_abs.append(_abs_erp_file_url(featured_rel))
            for fu in gallery_rel:
                absu = _abs_erp_file_url(fu)
                if absu and absu not in erp_urls_abs:
                    erp_urls_abs.append(absu)

            erp_sizes = await _head_sizes_for_urls(erp_urls_abs) if erp_urls_abs else []
            erp_gallery = [{"url": u, "size": (erp_sizes[idx] if idx < len(erp_sizes) else 0)} for idx, u in enumerate(erp_urls_abs)]

            wc_gallery = normalize_gallery_from_wc_product(wc_prod or {})
            wc_img_sizes = _sizes_from_wc_gallery(wc_gallery)
            gallery_diff = not gallery_images_equal(erp_gallery, wc_gallery if isinstance(wc_gallery, list) else [])

            # STOCK
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
            if is_variable:
                # We don't manage desc/gallery at variation level; suppress noise
                desc_diff = False
                gallery_diff = False

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
                "wc_img_sizes": wc_img_sizes,
                "gallery_diff": gallery_diff,
                "description_diff": desc_diff,
                "has_variants": int(is_variable),
                "action": "Create" if needs_create else ("Update" if needs_update else "Synced"),
                "fields_to_update": "ALL" if needs_create else (update_fields or []),
            }

            if is_variable:
                if needs_create:
                    report["variant_to_create"].append(preview_entry)
                elif needs_update:
                    report["variant_to_update"].append(preview_entry)
                else:
                    report["variant_synced"].append(preview_entry)
            else:
                if needs_create:
                    report["to_create"].append(preview_entry)
                elif needs_update:
                    report["to_update"].append(preview_entry)
                else:
                    report["already_synced"].append(preview_entry)

            # ---- Real side effects ----
            if dry_run:
                report["mapping"][sku] = {
                    "template": template_code,
                    "attributes": attributes_values,
                    "brand": brand,
                    "categories": categories
                }
                continue

            cats_payload = [{"id": wc_cat_map[c]} for c in categories if c in wc_cat_map]

            if is_variable:
                parent_sku = template_code
                if parent_id_for_vars is None:
                    parent_payload = {
                        "name": template_item.get("item_name") or template_code,
                        "sku": parent_sku,
                        "type": "variable",
                        "status": "publish",
                        "manage_stock": False,
                        "categories": cats_payload,
                        "brands": _brand_payload(family_brand),
                        "attributes": [{
                            "name": "Sheet Size",
                            "variation": True,
                            "visible": True,
                            "options": sheet_sizes if sheet_sizes else (
                                [] if attributes_values.get("Sheet Size") is None else [attributes_values.get("Sheet Size")]
                            ),
                        }],
                    }
                    if parent_images_payload:
                        parent_payload["images"] = parent_images_payload

                    logger.info(f"[PARENT][UPSERT] {parent_sku} with {len(parent_payload['attributes'])} attrs")
                    resp = await _create_or_update_product_by_sku(parent_sku, parent_payload)
                    if resp.get("status_code") not in (200, 201):
                        logger.error(f"[PARENT] create/update failed for {parent_sku}: {_resp_summary(resp)}")
                        report["errors"].append({
                            "sku": parent_sku,
                            "status": resp.get("status_code"),
                            "code": (resp.get("data") or {}).get("code"),
                            "message": (resp.get("data") or {}).get("message"),
                            "content_type": resp.get("content_type"),
                            "raw_excerpt": resp.get("raw_excerpt"),
                            "where": "parent"
                        })
                        parent_id_for_vars = None
                    else:
                        parent_id_for_vars = resp["data"]["id"]
                        logger.info(f"[PARENT][OK] id={parent_id_for_vars}")

                        if parent_images_payload and parent_id_for_vars:
                            assigned = resp["data"].get("images") or []
                            assigned_ids = [img.get("id") for img in assigned if isinstance(img, dict) and "id" in img]
                            want_ids = [img["id"] for img in parent_images_payload]
                            if images_payload := parent_images_payload:
                                if sorted(assigned_ids) != sorted(want_ids):
                                    logger.info("[PARENT][IMAGES] correcting images for %s: have=%s want=%s", parent_sku, assigned_ids, want_ids)
                                    auth_w = (settings.WC_API_KEY, settings.WC_API_SECRET)
                                    _ = await _request_with_retry("PUT", f"{WC_API}/products/{parent_id_for_vars}", auth=auth_w, json={"images": images_payload})

                    existing_var_map = await _get_variations_map(parent_id_for_vars) if parent_id_for_vars else {}

                if parent_id_for_vars:
                    var_image_id = None
                    if erp_urls_abs:
                        try:
                            mid = await _upload_with_retry(erp_urls_abs[0], basename(erp_urls_abs[0]))
                            if mid:
                                var_image_id = int(mid)
                        except Exception as e:
                            logger.warning(f"[IMG][VAR] skip image for {sku}: {e}")

                    size_val = attributes_values.get("Sheet Size") or ""
                    norm_size = _normalize_size_label(size_val)
                    var_payload = {
                        "sku": sku,
                        "regular_price": _price_str(price) or "0.00",
                        "manage_stock": (stock_q is not None),
                        "stock_quantity": (int(stock_q) if stock_q is not None else None),
                        "attributes": [{"name": "Sheet Size", "option": norm_size}],
                        "status": "publish",
                    }
                    if var_image_id:
                        var_payload["image"] = {"id": var_image_id}

                    vresp = await _create_or_update_variation(parent_id_for_vars, sku, norm_size, var_payload, existing_var_map)
                    if vresp.get("status_code") not in (200, 201):
                        logger.error(f"[VAR] create/update failed for {sku}: {_resp_summary(vresp)}")
                        report["errors"].append({
                            "sku": sku,
                            "status": vresp.get("status_code"),
                            "code": (vresp.get("data") or {}).get("code"),
                            "message": (vresp.get("data") or {}).get("message"),
                            "content_type": vresp.get("content_type"),
                            "raw_excerpt": vresp.get("raw_excerpt"),
                            "where": "variation"
                        })
                    else:
                        vjson = vresp.get("data") or {}
                        if vjson:
                            existing_var_map[sku] = vjson
                            for a in (vjson.get("attributes") or []):
                                if (a.get("name") or "").strip().lower() == "sheet size":
                                    opt = (a.get("option") or "").strip()
                                    if opt:
                                        existing_var_map[f"size::{opt.lower()}"] = vjson
                        if var_image_id:
                            vid = None
                            existing = existing_var_map.get(sku) or existing_var_map.get(f"size::{(norm_size or '').lower()}")
                            if existing:
                                vid = existing.get("id")
                            if not vid:
                                vid = (vresp.get("data") or {}).get("id")
                            if vid:
                                have = ((vresp.get("data") or {}).get("image") or {}).get("id")
                                if have != var_image_id:
                                    auth_w = (settings.WC_API_KEY, settings.WC_API_SECRET)
                                    _ = await _request_with_retry(
                                        "PUT",
                                        f"{WC_API}/products/{parent_id_for_vars}/variations/{vid}",
                                        auth=auth_w,
                                        json={"image": {"id": var_image_id}},
                                    )

            else:
                # SIMPLE PRODUCT
                if len(_sku_parts(sku)) >= 3 or sku in variation_skus_seen:
                    logger.warning("[SIMPLE->VAR BLOCK] %s looks like a variation SKU; skipping simple path", sku)
                    continue

                image_ids = []
                if erp_gallery:
                    logger.info(f"[IMG][SIMPLE] uploading {len(erp_gallery)} images for {sku}")
                for img in erp_gallery:
                    try:
                        mid = await _upload_with_retry(img["url"], basename(img["url"]))
                        if mid:
                            image_ids.append(int(mid))
                    except Exception as e:
                        logger.warning(f"[IMG][SIMPLE] skip one image for {sku}: {e}")
                images_payload = [{"id": mid, "position": idx} for idx, mid in enumerate(image_ids)]

                payload = {
                    "name": variant.get("item_name") or template_item.get("item_name") or sku,
                    "sku": sku,
                    "type": "simple",
                    "status": "publish",
                    "regular_price": _price_str(price) or "0.00",
                    "manage_stock": (stock_q is not None),
                    "stock_quantity": (int(stock_q) if stock_q is not None else None),
                    "categories": cats_payload,
                    "brands": _brand_payload(brand),
                    "description": erp_desc or "",
                    "images": images_payload if images_payload else [],
                }

                resp = await _create_or_update_product_by_sku(sku, payload)
                if resp.get("status_code") not in (200, 201):
                    logger.error(f"[CREATE] Woo product failed (sku={sku}): {_resp_summary(resp)}")
                    report["errors"].append({
                        "sku": sku,
                        "status": resp.get("status_code"),
                        "code": (resp.get("data") or {}).get("code"),
                        "message": (resp.get("data") or {}).get("message"),
                        "content_type": resp.get("content_type"),
                        "raw_excerpt": resp.get("raw_excerpt"),
                        "where": "simple"
                    })
                else:
                    assigned = resp["data"].get("images") or []
                    assigned_ids = [img.get("id") for img in assigned if isinstance(img, dict) and "id" in img]
                    want_ids = [img["id"] for img in images_payload]
                    if images_payload and sorted(assigned_ids) != sorted(want_ids):
                        logger.info("[SIMPLE][IMAGES] correcting images for %s: have=%s want=%s", sku, assigned_ids, want_ids)
                        auth_w = (settings.WC_API_KEY, settings.WC_API_SECRET)
                        _ = await _request_with_retry("PUT", f"{WC_API}/products/{resp['data']['id']}", auth=auth_w, json={"images": images_payload})

            report["mapping"][sku] = {
                "template": template_code,
                "attributes": attributes_values,
                "brand": brand,
                "categories": categories
            }

    return report

