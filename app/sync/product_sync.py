# app/sync/product_sync.py
# =======================================================
# ERPNext → WooCommerce Product/Variant Sync Pipeline
# Preview + Full/Partial sync
# - No instance-specific attribute constants: attribute order is inferred per family from SKUs
# - Consistent price-list detection & reporting
# - Proper variant parent attributes + child attributes/abbrs
# - Brand & images pulled robustly
# - variant_to_create / variant_to_update populated
# =======================================================

import logging
import inspect
import os
from typing import List, Dict, Any, Optional, Iterable, Tuple
from collections import defaultdict, Counter
from urllib.parse import urlparse

from app.config import settings

from app.erpnext import (
    get_erpnext_items,
    get_erp_images,          # callable(get_erp_images(item)) -> list[str] or list[dict]
    get_erpnext_categories,
    get_price_map,
    get_stock_map,
)
from app.woocommerce import (
    get_wc_products,
    get_wc_categories,
    create_wc_category,
    ensure_wp_image_uploaded,
    purge_wc_bin_products,
)
from app.sync_utils import (
    build_wc_cat_map,
    normalize_category_name,
    get_erp_image_list,     # tolerant wrapper around ERPNext item image(s)
    sync_categories,
    normalize_woo_image_url,
    strip_html_tags,
)
from app.mapping_store import save_mapping_file
from app.erp.erp_variant_matrix import build_variant_matrix
from app.erp.erp_attribute_loader import (
    get_erpnext_attribute_order,
    get_erpnext_attribute_map,
    AttributeValueMapping,
)
from app.erp.erp_sku_parser import parse_erp_sku

logger = logging.getLogger("uvicorn.error")
MAPPING_FILE = "app/mapping/products_to_sync.json"

# -------------------------------------------------------
# Small utility to safely handle async/sync helpers
# -------------------------------------------------------
async def _maybe_await(x):
    if inspect.isawaitable(x):
        return await x
    return x

# =========================
# Helper extractors / utils
# =========================

def _extract_stock_qty(d: Dict[str, Any]) -> Optional[float]:
    if not isinstance(d, dict):
        return None
    for k in (
        "stock_qty", "actual_qty", "available_qty", "on_hand", "onhand",
        "qty", "quantity", "projected_qty", "total_stock", "in_stock",
        "stock_quantity"
    ):
        v = d.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except Exception:
            continue
    return None

def _price_list_meta(price_map: Dict[str, Any]) -> Optional[str]:
    if not isinstance(price_map, dict):
        return None
    meta = price_map.get("_meta") or price_map.get("__meta__") or {}
    for k in ("price_list", "price_list_name", "name", "title"):
        v = meta.get(k)
        if isinstance(v, (str, int)):
            return str(v)
    for k in ("price_list", "price_list_name", "name", "title"):
        v = price_map.get(k)
        if isinstance(v, (str, int)):
            return str(v)
    return None

def _normalize_gallery_return(g):
    out = []
    if not g:
        return out
    if isinstance(g, dict):
        parts = []
        if g.get("main"):
            parts.append(g["main"])
        parts.extend(g.get("attachments") or [])
        g = parts
    if isinstance(g, list):
        for item in g:
            if isinstance(item, dict):
                url = item.get("url") or item.get("src") or item.get("file") or ""
                size = item.get("size")
                try:
                    size = int(size) if size is not None else 0
                except Exception:
                    size = 0
                if url:
                    out.append({"url": url, "size": size})
            elif isinstance(item, str):
                if item.strip():
                    out.append({"url": item.strip(), "size": 0})
    return out

def _normalize_gallery_from_wc_product(wc_prod: Dict[str, Any]) -> List[Dict[str, Any]]:
    imgs = wc_prod.get("images") or []
    out = []
    for i in imgs:
        src = (i.get("src") or "").strip()
        if src:
            out.append({"url": src, "size": 0})
    return out

def gallery_images_equal(erp_gallery, wc_gallery):
    erp_set = { (img.get("url") or "").strip() for img in (erp_gallery or []) }
    wc_set  = { (img.get("url") or "").strip() for img in (wc_gallery or []) }
    return erp_set == wc_set

def _mapping_dict_to_file_shape(mapping: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for sku, m in (mapping or {}).items():
        rows.append({
            "erp_item_code": m.get("template") or sku,
            "sku": sku,
            "woo_product_id": None,
            "woo_status": None,
            "brand": m.get("brand"),
            "categories": ", ".join(m.get("categories") or []),
        })
    return rows

def _guess_parent_code_from_sku(sku: str) -> Optional[str]:
    parts = (sku or "").split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else None

# ---------- Attribute order inference (no instance constants) ----------

def _norm(s: Optional[str]) -> str:
    return ("" if s is None else str(s).strip())

def _lower(s: Optional[str]) -> str:
    return _norm(s).lower()

def _parse_sku_tokens(sku: str) -> Tuple[str, List[str]]:
    parts = [p for p in _norm(sku).split("-") if p]
    if not parts:
        return "", []
    return parts[0], parts[1:]

def _abbr_index(attribute_map: Dict[str, AttributeValueMapping]) -> Dict[str, List[str]]:
    """Map lower(abbr) -> [attribute_names...]"""
    idx: Dict[str, List[str]] = defaultdict(list)
    for attr, mapping in (attribute_map or {}).items():
        for abbr in mapping.abbreviations():
            a = _lower(abbr)
            if attr not in idx[a]:
                idx[a].append(attr)
    return idx

def _infer_attribute_order_for_group(
    skus: List[str],
    attribute_map: Dict[str, AttributeValueMapping],
    fallback_order: List[str],
) -> List[str]:
    """Infer attribute order by analyzing tokens at each position across a family of SKUs."""
    if not skus:
        return []

    idx = _abbr_index(attribute_map)
    tokens_by_pos: Dict[int, List[str]] = defaultdict(list)
    max_len = 0
    for sku in skus:
        _, toks = _parse_sku_tokens(sku)
        max_len = max(max_len, len(toks))
        for i, t in enumerate(toks):
            tokens_by_pos[i].append(_lower(t))

    chosen: List[str] = []
    used = set()

    for pos in range(max_len):
        tokens_here = tokens_by_pos.get(pos, [])
        if not tokens_here:
            continue

        score = Counter()
        for tok in tokens_here:
            for a in idx.get(tok, []):
                score[a] += 1

        # remove already-used attrs
        for a in list(score.keys()):
            if a in used:
                del score[a]

        if score:
            best_attr, _ = score.most_common(1)[0]
            chosen.append(best_attr)
            used.add(best_attr)
        else:
            # Heuristic fallback: first fallback attr that can resolve any token here
            plausible = None
            for a in fallback_order:
                if a in used:
                    continue
                amap = attribute_map.get(a)
                if not amap:
                    continue
                for tok in tokens_here:
                    if amap.get_value(tok) is not None:
                        plausible = a
                        break
                if plausible:
                    break
            if plausible:
                chosen.append(plausible)
                used.add(plausible)

    return chosen

# ---------- Image helpers ----------

def _extract_image_urls_from_item(item: dict) -> List[Dict[str, Any]]:
    urls: List[Dict[str, Any]] = []
    for key in ("website_image", "image", "thumbnail", "image_url", "img"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            urls.append({"url": v.strip(), "size": 0})
    for i in range(1, 6):
        key = f"image_{i}"
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            urls.append({"url": v.strip(), "size": 0})
    # Dedup
    seen = set()
    out = []
    for d in urls:
        u = d["url"]
        if u not in seen:
            seen.add(u)
            out.append(d)
    return out

# ---------- Image helpers (async-safe) ----------

async def _safe_get_erp_gallery_for_sku(
    sku: str,
    variant: dict | None,
    template_item: dict | None,
) -> List[Dict[str, Any]]:
    """
    Try several shapes:
    - get_erp_image_list(sku, get_erp_images, as_gallery=True)
    - get_erp_image_list(sku, as_gallery=True)
    - get_erp_image_list(sku)
    - Fallback: get_erp_images(variant/template)
    - Fallback: item fields (website_image, image, etc.)
    Always returns a normalized list: [{'url': str, 'size': int}, ...]
    """
    # Preferred: pass callback (works whether either is async or sync)
    try:
        res = await _maybe_await(get_erp_image_list(sku, get_erp_images, as_gallery=True))
        norm = _normalize_gallery_return(res)
        if norm:
            return norm
    except TypeError:
        pass
    except Exception:
        pass

    # Older signatures (also async/sync tolerant)
    try:
        res = await _maybe_await(get_erp_image_list(sku, as_gallery=True))
        norm = _normalize_gallery_return(res)
        if norm:
            return norm
    except TypeError:
        pass
    except Exception:
        pass

    try:
        res = await _maybe_await(get_erp_image_list(sku))
        norm = _normalize_gallery_return(res)
        if norm:
            return norm
    except TypeError:
        pass
    except Exception:
        pass

    # Direct item-based callback fallback
    for src in (variant, template_item):
        if not src:
            continue
        try:
            res = await _maybe_await(get_erp_images(src))
            norm = _normalize_gallery_return(res)
            if norm:
                return norm
        except Exception:
            continue

    # Raw item fields fallback
    out = []
    out.extend(_extract_image_urls_from_item(variant or {}))
    out.extend(_extract_image_urls_from_item(template_item or {}))
    return out

# ---------- Brand helper ----------

def _extract_brand_from_attrlist(attr_list) -> str:
    """
    ERPNext variants often carry Brand in the child table 'attributes'
    with rows like:
      {"attribute": "Brand", "attribute_value": "Techniclad", "abbr": "TECH"}
    Handle common shapes and return a clean string if found, else "".
    """
    if not isinstance(attr_list, (list, tuple)):
        return ""
    for row in attr_list:
        if not isinstance(row, dict):
            continue
        # Look for the attribute name indicating brand
        name_candidates = [
            row.get("attribute"),
            row.get("attribute_name"),
            row.get("name"),
        ]
        is_brand = any(
            isinstance(n, str) and n.strip().lower() == "brand"
            for n in name_candidates
        )
        if not is_brand:
            continue

        # Try the common value fields in order
        val = (
            row.get("attribute_value")
            or row.get("value")
            or row.get("brand")
            or row.get("abbr")
        )
        if isinstance(val, dict):
            # sometimes nested
            val = val.get("value") or val.get("name") or val.get("abbr")
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)):
            return str(val)
    return ""

def _extract_brand(variant: Dict[str, Any], template_item: Dict[str, Any], attributes: Dict[str, Any]) -> str:
    """
    Robust brand lookup across:
      1) top-level item fields (variant, then template)
      2) ERPNext 'attributes' child table (variant, then template)
      3) the per-variant attributes dict from the matrix (if 'Brand' present)
    Returns "" if not found.
    """
    # 1) direct fields (variant first, then template)
    for src in (variant, template_item):
        if not isinstance(src, dict):
            continue
        for k in ("brand", "item_brand", "brand_name", "manufacturer"):
            v = src.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)):
                return str(v)

    # 2) attributes child table (variant first, then template)
    b = _extract_brand_from_attrlist(variant.get("attributes"))
    if b:
        return b
    b = _extract_brand_from_attrlist(template_item.get("attributes"))
    if b:
        return b

    # 3) attributes from the variant matrix row (if Brand was part of it)
    #    (Not typical for your SKUs, but keep the logic.)
    for key in ("Brand", "brand"):
        v = (attributes or {}).get(key)
        if isinstance(v, dict):
            v = v.get("value") or v.get("abbr") or v.get("name")
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)

    return ""

# =========================
# 1. Purge Woo BIN (Requirement 6)
# =========================

async def purge_woo_bin_if_needed(auto_purge: bool = True):
    if auto_purge:
        logger.info("🗑️ Purging WooCommerce bin/trash before sync...")
        try:
            await purge_wc_bin_products()
        except Exception as e:
            logger.error(f"Failed to purge Woo bin: {e}")

# =========================
# 2. Sync Entry Points (ASYNC)
# =========================

async def sync_products_full(dry_run: bool = False, purge_bin: bool = True) -> Dict[str, Any]:
    """
    Full sync: ERPNext is source of truth.
    - Syncs categories; builds brand/attribute preview reports (and later ensure in live mode).
    - Builds variant matrix and product preview (or mutations if not dry_run).
    - Consistent price-list detection & reporting.
    """
    logger.info("🔁 [SYNC] Starting full ERPNext → Woo sync (dry_run=%s)", dry_run)

    if not dry_run and purge_bin:
        try:
            await _maybe_await(purge_wc_bin_products())
        except Exception as e:
            logger.error(f"Failed to purge Woo bin: {e}")

    # 1) Categories
    await _maybe_await(get_erpnext_categories())
    await _maybe_await(get_wc_categories())
    cat_report = await _maybe_await(sync_categories(dry_run=dry_run))
    wc_categories = await _maybe_await(get_wc_categories())  # refresh

    # 2) Items + price + stock
    erp_items = await _maybe_await(get_erpnext_items())

    # Price list (prefer env, fallback to auto)
    try:
        price_map = await _maybe_await(get_price_map(settings.ERP_SELLING_PRICE_LIST))
    except TypeError:
        price_map = await _maybe_await(get_price_map())
    except Exception:
        price_map = await _maybe_await(get_price_map())
    if not isinstance(price_map, dict):
        price_map = {}
    pl_name = _price_list_meta(price_map) or (settings.ERP_SELLING_PRICE_LIST or "Standard Selling")
    try:
        count = 0
        for v in price_map.values():
            if isinstance(v, (int, float)):
                count += 1
            elif isinstance(v, str):
                try:
                    float(v); count += 1
                except Exception:
                    pass
        logger.info("Using price list: %s with %d prices", pl_name, count)
    except Exception:
        logger.info("Using price list: %s", pl_name)

    stock_map = await _maybe_await(get_stock_map())

    # 3) Attributes & variant matrix
    erp_attr_order = await _maybe_await(get_erpnext_attribute_order())
    attribute_map = await _maybe_await(get_erpnext_attribute_map(erp_attr_order))

    template_variant_matrix = build_variant_matrix(erp_items, attribute_map, erp_attr_order)
    if not any(len(v.get("variants", [])) > 1 for v in (template_variant_matrix or {}).values()):
        fb = _build_fallback_variant_matrix(erp_items)
        for k, v in fb.items():
            template_variant_matrix.setdefault(k, v)

    fb_base = _build_fallback_variant_matrix_by_base(erp_items, erp_attr_order, attribute_map)
    base_or_template = fb_base if fb_base else template_variant_matrix

    unified_matrix = merge_simple_items_into_matrix(erp_items, base_or_template)

    # Infer GLOBAL order for preview (based on real SKUs)
    attribute_order_for_preview = _infer_global_attribute_order_from_skus(
        erp_items, attribute_map, erp_attr_order
    )

    # 3b) Attribute & Brand preview reports (parity with category_report)
    used_attr_vals = _collect_used_attribute_values(unified_matrix)
    attribute_report = await _bootstrap_wc_attributes_if_possible(used_attr_vals, dry_run=dry_run)
    brand_report = await _bootstrap_wc_brands_if_possible(erp_items, dry_run=dry_run)

    # 4) WC state + cat map
    wc_products = await _maybe_await(get_wc_products())
    wc_cat_map = build_wc_cat_map(wc_categories)

    # 5) Core sync
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
        file_shape = _mapping_dict_to_file_shape(sync_report.get("mapping"))
        save_mapping_file(file_shape)
    except Exception as e:
        logger.error(f"Failed to save mapping file: {e}")

    logger.info("✅ [SYNC] Full sync complete (dry_run=%s)", dry_run)
    return {
        "category_report": cat_report,
        "attribute_report": attribute_report,   # NEW
        "brand_report": brand_report,           # NEW
        "sync_report": sync_report,
        "price_list_used": pl_name,
        "attribute_order": attribute_order_for_preview,
        "dry_run": dry_run
    }

async def sync_products_partial(skus_to_sync: List[str], dry_run: bool = False) -> Dict[str, Any]:
    """
    Partial sync: Sync only specified SKUs (all business rules apply).
    Also returns attribute_report and brand_report (preview parity).
    """
    logger.info("🔁 [SYNC] Partial ERPNext → Woo sync (dry_run=%s)", dry_run)

    await _maybe_await(get_erpnext_categories())
    wc_categories = await _maybe_await(get_wc_categories())
    await _maybe_await(sync_categories(dry_run=dry_run))
    wc_categories = await _maybe_await(get_wc_categories())

    erp_items = await _maybe_await(get_erpnext_items())

    # Price list detection
    try:
        price_map = await _maybe_await(get_price_map(settings.ERP_SELLING_PRICE_LIST))
    except TypeError:
        price_map = await _maybe_await(get_price_map())
    except Exception:
        price_map = await _maybe_await(get_price_map())
    if not isinstance(price_map, dict):
        price_map = {}

    stock_map = await _maybe_await(get_stock_map())

    erp_attr_order = await _maybe_await(get_erpnext_attribute_order())
    attribute_map = await _maybe_await(get_erpnext_attribute_map(erp_attr_order))

    template_variant_matrix = build_variant_matrix(erp_items, attribute_map, erp_attr_order)
    if not any(len(v.get("variants", [])) > 1 for v in (template_variant_matrix or {}).values()):
        fb = _build_fallback_variant_matrix(erp_items)
        for k, v in fb.items():
            template_variant_matrix.setdefault(k, v)

    fb_base = _build_fallback_variant_matrix_by_base(erp_items, erp_attr_order, attribute_map)
    base_or_template = fb_base if fb_base else template_variant_matrix

    unified_matrix = merge_simple_items_into_matrix(erp_items, base_or_template)

    wc_products = await _maybe_await(get_wc_products())
    wc_cat_map = build_wc_cat_map(wc_categories)

    filtered_matrix = filter_variant_matrix_by_sku(unified_matrix, skus_to_sync)

    attribute_order_for_preview = _infer_global_attribute_order_from_skus(
        erp_items, attribute_map, erp_attr_order
    )

    # Attribute & Brand preview reports for the current dataset
    used_attr_vals = _collect_used_attribute_values(filtered_matrix)
    attribute_report = await _bootstrap_wc_attributes_if_possible(used_attr_vals, dry_run=dry_run)
    brand_report = await _bootstrap_wc_brands_if_possible(erp_items, dry_run=dry_run)

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

    try:
        file_shape = _mapping_dict_to_file_shape(sync_report.get("mapping"))
        save_mapping_file(file_shape)
    except Exception as e:
        logger.error(f"Failed to save mapping file: {e}")

    return {
        "brand_report": brand_report,
        "attribute_report": attribute_report,  # NEW
        "sync_report": sync_report,
        "attribute_order": attribute_order_for_preview,
        "dry_run": dry_run
    }

async def sync_preview() -> Dict[str, Any]:
    return await sync_products_full(dry_run=True, purge_bin=False)

# =========================
# 3. Core Sync Logic
# =========================

async def sync_all_templates_and_variants(
    variant_matrix: Dict[str, Dict[str, Any]],
    wc_products,
    wc_cat_map,
    price_map,
    attribute_map: Optional[Dict[str, Any]] = None,
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
        "variant_synced": []
    }

    wc_product_index = {p.get("sku"): p for p in (wc_products or []) if p.get("sku")}
    seen_skus = set()

    for template_code, data in (variant_matrix or {}).items():
        template_item = data["template_item"]
        variants = data["variants"]
        attr_matrix = data.get("attribute_matrix") or [{} for _ in variants]
        is_variable = len(variants) > 1

        # Build parent attribute options from child attribute matrix
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

        # Parent variable product (preview only)
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
                    val  = rec.get("value")
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
            brand = _extract_brand(variant, template_item, attributes_entry)

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
            erp_desc_plain = strip_html_tags(erp_desc)
            wc_desc_plain = strip_html_tags(wc_desc)
            desc_diff = erp_desc_plain.strip() != wc_desc_plain.strip()

            # IMAGES & GALLERY
            erp_gallery = await _safe_get_erp_gallery_for_sku(sku, variant, template_item)
            wc_gallery = _normalize_gallery_from_wc_product(wc_prod or {})
            gallery_diff = not gallery_images_equal(erp_gallery, wc_gallery)

            # STOCK
            stock_q = None
            try:
                # get_stock_map() often yields {(item_code, warehouse): qty}
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
                stock_q = _extract_stock_qty(variant)
            if stock_q is None:
                stock_q = _extract_stock_qty(template_item)

            # Decide action / update fields
            update_fields = []
            if desc_diff:
                update_fields.append("description")
            if gallery_diff:
                update_fields.append("gallery_images")
            if (price is not None) and wc_prod and (str(price) != str(wc_prod.get("regular_price"))):
                update_fields.append("price")
            if brand and wc_prod and (brand != (wc_prod.get("brand") or "")):
                update_fields.append("brand")

            needs_create = wc_prod is None
            needs_update = bool(update_fields) and not needs_create

            # Preview row
            preview_entry = {
                "sku": sku,
                "name": variant.get("item_name") or template_item.get("item_name") or sku,
                "regular_price": price,
                "stock_quantity": stock_q,
                "categories": categories,
                "brand": brand,
                "attributes": attributes_values,   # values
                "attr_abbr": attributes_abbrs,     # abbreviations
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

            # Real mutations if not dry_run (upload images, etc.)
            if not dry_run:
                media_ids = []
                for img in erp_gallery:
                    try:
                        media_id = await _maybe_await(ensure_wp_image_uploaded(img["url"]))
                        if media_id:
                            media_ids.append(media_id)
                    except Exception as e:
                        logger.error(f"Image upload failed for {sku}: {e}")
                # TODO: create/update Woo product using media_ids, attributes, etc.

            # Mapping
            report["mapping"][sku] = {
                "template": template_code,
                "attributes": attributes_values,
                "brand": brand,
                "categories": categories
            }

    return report

# =========================
# Helper Functions
# =========================

def merge_simple_items_into_matrix(
    erp_items: List[Dict[str, Any]],
    template_variant_matrix: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    matrix = dict(template_variant_matrix or {})
    for item in erp_items or []:
        has_variants = item.get("has_variants", 0)
        variant_of = item.get("variant_of")
        if not has_variants and not variant_of:
            code = item.get("item_code")
            if not code or code in matrix:
                continue
            matrix[code] = {
                "template_item": item,
                "variants": [item],
                "attribute_matrix": [{}],
            }
    return matrix

def filter_variant_matrix_by_sku(
    variant_matrix: Dict[str, Dict[str, Any]], skus: List[str]
) -> Dict[str, Dict[str, Any]]:
    if not skus:
        return variant_matrix
    filtered = {}
    for template_code, data in (variant_matrix or {}).items():
        variants = data["variants"]
        attr_matrix = data.get("attribute_matrix") or [{} for _ in variants]
        keep = [i for i, v in enumerate(variants) if v.get("item_code") in skus]
        if keep:
            filtered[template_code] = {
                "template_item": data["template_item"],
                "variants": [variants[i] for i in keep],
                "attribute_matrix": [attr_matrix[i] for i in keep],
            }
    return filtered

def _build_fallback_variant_matrix(erp_items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for it in erp_items or []:
        parent = it.get("variant_of")
        if parent:
            by_parent.setdefault(parent, []).append(it)
    matrix: Dict[str, Dict[str, Any]] = {}
    for parent_code, children in by_parent.items():
        template_item = next((i for i in erp_items if i.get("item_code") == parent_code), None) or children[0]
        matrix[parent_code] = {
            "template_item": template_item,
            "variants": children,
            "attribute_matrix": [ (i.get("attributes") or {}) for i in children ],
        }
    return matrix

def _build_fallback_variant_matrix_by_base(
    erp_items: List[Dict[str, Any]],
    attribute_order_global: List[str],
    attribute_map: Dict[str, AttributeValueMapping],
) -> Dict[str, Dict[str, Any]]:
    """
    Group by base code (e.g., SVR-ALSKA) and build attribute_matrix by:
      1) inferring per-family attribute order from SKUs (no instance constants)
      2) parsing each SKU with parse_erp_sku(order, attribute_map)
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in erp_items or []:
        base = _guess_parent_code_from_sku(it.get("item_code") or "")
        if base:
            groups.setdefault(base, []).append(it)

    matrix: Dict[str, Dict[str, Any]] = {}
    for base, items in groups.items():
        skus = [i.get("item_code") or "" for i in items if (i.get("item_code") or "").strip()]
        # Infer order for this family
        inferred_order = _infer_attribute_order_for_group(skus, attribute_map, attribute_order_global)

        attr_matrix = []
        for v in items:
            sku = v.get("item_code") or ""
            parsed = parse_erp_sku(sku, inferred_order, attribute_map) or {}
            entry = {}
            for attr_name in inferred_order:
                pr = parsed.get(attr_name) or {}
                entry[attr_name] = {"abbr": pr.get("abbr"), "value": pr.get("value")}
            attr_matrix.append(entry)

        template_item = items[0]
        matrix[base] = {
            "template_item": template_item,
            "variants": items,
            "attribute_matrix": attr_matrix,
        }
    return matrix

def _infer_global_attribute_order_from_skus(
    erp_items: List[Dict[str, Any]],
    attribute_map: Dict[str, AttributeValueMapping],
    erp_attr_order: List[str],
) -> List[str]:
    """
    Build a global attribute order based on real SKU tokens.
    - Only includes attributes that appear in SKUs (via abbr mapping).
    - Orders them by average position across families.
    """
    # group SKUs by base (e.g., SVR-ALSKA)
    groups: Dict[str, List[str]] = defaultdict(list)
    for it in erp_items or []:
        sku = (it.get("item_code") or "").strip()
        if not sku:
            continue
        base = _guess_parent_code_from_sku(sku)
        if base:
            groups.setdefault(base, []).append(sku)

    pos_votes: Dict[str, Counter] = defaultdict(Counter)
    any_found = False

    for base, skus in groups.items():
        order = _infer_attribute_order_for_group(skus, attribute_map, erp_attr_order)
        if not order:
            continue
        any_found = True
        for pos, attr in enumerate(order):
            pos_votes[attr][pos] += 1

    if not any_found:
        # fallback: if we couldn't infer anything, return ERP order unchanged
        return list(erp_attr_order or [])

    # compute average position for each attribute observed
    scored: List[Tuple[float, int, str]] = []
    for attr, ctr in pos_votes.items():
        total_votes = sum(ctr.values())
        weighted_sum = sum(p * c for p, c in ctr.items())
        avg_pos = weighted_sum / max(total_votes, 1)
        # sort by avg_pos asc, then by votes desc to stabilize
        scored.append((avg_pos, -total_votes, attr))

    scored.sort()
    return [attr for _avg, _negvotes, attr in scored]

# ---------- Price list resolution (single source of truth) ----------

def _numeric_price_count(price_map: Dict[str, Any]) -> int:
    """Count number-like values in the map (ignores meta keys)."""
    if not isinstance(price_map, dict):
        return 0
    n = 0
    for v in price_map.values():
        if isinstance(v, (int, float)):
            n += 1
        elif isinstance(v, str):
            try:
                float(v)
                n += 1
            except Exception:
                pass
    return n

def _has_meaningful_prices(price_map: Dict[str, Any]) -> bool:
    return _numeric_price_count(price_map) > 0

async def _resolve_price_list() -> tuple[str, Dict[str, Any]]:
    """
    Resolve price list exactly once (return the real name, not a guess):
    1) Try preferred (ENV ERP_SELLING_PRICE_LIST). If it yields prices, use it.
    2) Else call default get_price_map(). Use whatever it picked (capture its name).
    3) Else return empty map and preferred or 'Standard Selling' as the label.
    """
    preferred = (settings.ERP_SELLING_PRICE_LIST or "").strip()

    async def _call_with_name(*args):
        """
        Try to call get_price_map with return_name=True across possible signatures.
        Return (price_map, name_or_None).
        """
        # 1) Named kw
        try:
            res = await _maybe_await(get_price_map(*args, return_name=True))
            if isinstance(res, tuple) and len(res) == 2:
                return res
            if isinstance(res, dict):
                return res, None
        except TypeError:
            pass

        # 2) Positional
        try:
            res = await _maybe_await(get_price_map(*args, True))
            if isinstance(res, tuple) and len(res) == 2:
                return res
            if isinstance(res, dict):
                return res, None
        except TypeError:
            pass

        # 3) No return_name support
        pm = await _maybe_await(get_price_map(*args))
        name = None
        try:
            # If the implementation set this, grab it.
            import app.erpnext as erpmod
            name = getattr(erpmod, "LAST_PRICE_LIST_USED", None)
        except Exception:
            pass
        return pm, name

    # Helper
    def _meaningful(pm: Dict[str, Any]) -> bool:
        return _numeric_price_count(pm) > 0

    # 1) Preferred
    if preferred:
        pm, nm = await _call_with_name(preferred)
        if isinstance(pm, dict) and _meaningful(pm):
            return (nm or preferred, pm)

    # 2) Default
    pm2, nm2 = await _call_with_name()
    if isinstance(pm2, dict) and _meaningful(pm2):
        # If name is unknown, don’t guess incorrectly—use 'Standard Selling' only as a last resort label.
        return (nm2 or "Standard Selling", pm2)

    # 3) Nothing usable
    return (preferred or "Standard Selling"), {}

def _collect_erp_brands_from_items(items: List[Dict[str, Any]]) -> List[str]:
    """
    Return unique brand names found on items.
    Uses variant->template fallback if variant brand is empty.
    """
    brands: set[str] = set()
    template_brand: Dict[str, str] = {}
    for it in items or []:
        if it.get("has_variants") == 1:
            b = it.get("brand")
            if isinstance(b, str) and b.strip():
                template_brand[it.get("item_code")] = b.strip()

    for it in items or []:
        b = it.get("brand")
        if isinstance(b, str) and b.strip():
            brands.add(b.strip())
            continue
        parent = it.get("variant_of")
        if parent:
            pb = template_brand.get(parent)
            if pb:
                brands.add(pb)

    return sorted(brands)

async def _bootstrap_wc_brands_if_possible(erp_items: List[Dict[str, Any]], dry_run: bool = False) -> Dict[str, Any]:
    """
    No-op brand bootstrap (Woo brand API not used).
    We just report which brands ERP has, so preview shows them.
    """
    wanted = _collect_erp_brands_from_items(erp_items)
    if wanted:
        logger.info("ERP brands detected: %s", ", ".join(wanted))
    else:
        logger.info("No ERP brands detected.")
    return {"wanted": wanted, "existing": [], "created": []}

# ---------- Brands / Attributes bootstrap (preview-first) ----------

# ---------- Brands / Attributes bootstrap (preview-first) ----------

def _collect_used_attribute_values(
    variant_matrix: Dict[str, Dict[str, Any]]
) -> Dict[str, set]:
    """
    Walk the unified variant matrix and collect the set of attribute *values*
    in use per attribute name.
    Returns: { "Stone": {"Sierra", ...}, "Sheet Size": {"2440mm x 1220mm", ...}, ... }
    """
    used: Dict[str, set] = defaultdict(set)
    for _, data in (variant_matrix or {}).items():
        for rec in (data.get("attribute_matrix") or []):
            if not isinstance(rec, dict):
                continue
            for aname, v in rec.items():
                if isinstance(v, dict):
                    val = v.get("value")
                else:
                    val = None
                if val is not None and str(val).strip():
                    used[aname].add(str(val).strip())
    return used

async def _bootstrap_wc_brands_if_possible(
    erp_items: List[Dict[str, Any]],
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Preview-first 'ensure' for brands.
    - Collect brand names from ERP items (variant → template fallback).
    - In dry_run: just report what would be created.
    - Live mode creation will be wired in later via woocommerce.py helpers.
    """
    brands = _collect_erp_brands_from_items(erp_items)
    created_rows = [{"brand": b, "wc_response": {"dry_run": dry_run}} for b in brands]
    return {
        "created": created_rows,
        "total_erp_brands": len(brands),
    }

async def _bootstrap_wc_attributes_if_possible(
    used_attribute_values: Dict[str, set],
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Preview-first 'ensure' for product attributes & their terms in Woo.
    - used_attribute_values: {attr_name: {value1, value2, ...}} derived from matrix.
    - In dry_run: just report which attributes + values would be ensured/created.
    - Live mode creation will be wired in later via woocommerce.py helpers.
    """
    created_rows = []
    for attr_name in sorted(used_attribute_values.keys()):
        values = sorted(used_attribute_values[attr_name])
        created_rows.append({
            "attribute": attr_name,
            "values": values,
            "wc_response": {"dry_run": dry_run},
        })
    return {
        "created": created_rows,
        "total_attributes": len(used_attribute_values),
    }
