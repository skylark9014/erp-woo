# app/sync/product_sync.py
# =======================================================
# ERPNext → WooCommerce Product/Variant Sync Orchestrator
# - Category sync
# - Price list resolution (single log source-of-truth)
# - Variant matrix build + fallbacks
# - Attribute & Brand ensure (preview vs live)
# - Product preview w/ diffs (desc, images, price, brand)
# - Admin-UI friendly preview JSON (adds deletes + meta)
# =======================================================
from __future__ import annotations

import logging
import httpx
import asyncio
import os
from urllib.parse import urlparse, urlunparse, quote
from typing import List, Dict, Any, Optional
from collections import defaultdict, Counter

from app.erp.erp_variant_matrix import build_variant_matrix
from app.sync.components.price import resolve_price_map
from app.sync.components.images import gallery_images_equal  # kept for compatibility (not used directly here)
from app.sync.components.attributes import collect_used_attribute_values
from app.config import settings
from app.erpnext import (
    get_erpnext_items,
    get_erpnext_categories,
    get_price_map,
    get_stock_map,
)
from app.woocommerce import (
    get_wc_products,
    get_wc_categories,
    ensure_wc_attributes_and_terms,
    ensure_wp_image_uploaded,
    purge_wc_bin_products,
)
from app.sync_utils import (
    build_wc_cat_map,
    normalize_category_name,
    sync_categories,
    save_preview_to_file,
    reconcile_woocommerce_brands,
    _mapping_dir,
)
from app.erp.erp_attribute_loader import (
    get_erpnext_attribute_order,
    get_erpnext_attribute_map,
    AttributeValueMapping,
)
from app.sync.components.util import (
    maybe_await,
    strip_html,
    basename,
)
from app.sync.components.matrix import (
    merge_simple_items_into_matrix,
    filter_variant_matrix_by_sku,
    build_fallback_variant_matrix,
    build_fallback_variant_matrix_by_base,
    infer_global_attribute_order_from_skus,
)
from app.sync.components.brands import (
    extract_brand,
    collect_erp_brands_from_items,
)
from app.logging_filters import (
    _HTML_SIG_RE,
    _summarize_html,
    _HtmlTrimFilter,
)

logger = logging.getLogger("uvicorn.error")

MAPPING_STORE_PATH = os.path.join(_mapping_dir(), "mapping_store.json")
ERP_URL = settings.ERP_URL
ERP_API_KEY = settings.ERP_API_KEY
ERP_API_SECRET = settings.ERP_API_SECRET
WC_BASE_URL = settings.WC_BASE_URL
_SIZE_CACHE: Dict[str, int] = {}

# ---- host rewrite for image sizing ----

def _wp_base_host() -> str | None:
    base = os.getenv("WC_BASE_URL") or os.getenv("WP_BASE_URL") or os.getenv("WORDPRESS_BASE_URL")
    if not base:
        return None
    try:
        return urlparse(base).netloc
    except Exception:
        return None

def _rewrite_wp_media_host(url: str) -> str:
    """
    If the URL points at a .local/localhost host, rewrite the host to match WC_BASE_URL,
    keeping the original path/query so HEAD size checks don't 0-out.
    """
    base_host = _wp_base_host()
    if not base_host:
        return url
    try:
        u = urlparse(url)
        if not u.netloc:
            return url
        rewrite_hosts = {"techniclad.local", "localhost", "127.0.0.1"}
        if u.netloc in rewrite_hosts or u.netloc.endswith(".local"):
            base = os.getenv("WC_BASE_URL")
            scheme = urlparse(base).scheme if base else (u.scheme or "https")
            return urlunparse((scheme, base_host, u.path, u.params, u.query, u.fragment))
        return url
    except Exception:
        return url

# --- Robust size probing (memoized + concurrent) -----------------------------

async def head_content_length(client: httpx.AsyncClient, url: str) -> int:
    """
    Return the byte size for a URL using HEAD; if blocked/missing, fall back to a ranged GET.
    """
    try:
        r = await client.head(url)
        if r.status_code < 400:
            val = r.headers.get("Content-Length") or r.headers.get("content-length")
            if val and val.isdigit():
                return int(val)

        # Fallback: some servers block HEAD; try a 1-byte ranged GET
        r = await client.get(url, headers={"Range": "bytes=0-0"})
        if r.status_code < 400:
            # Prefer Content-Range total (bytes 0-0/12345)
            cr = r.headers.get("Content-Range") or r.headers.get("content-range")
            if cr and "/" in cr:
                total = cr.split("/")[-1]
                if total.isdigit():
                    return int(total)
            # Last resort: Content-Length (often 1 for ranged GETs)
            val = r.headers.get("Content-Length") or r.headers.get("content-length")
            if val and val.isdigit():
                clen = int(val)
                return clen if clen > 1 else 0
    except Exception as e:
        logger.debug("HEAD size failed for %s: %s", url, e)
    return 0

async def _head_sizes_for_urls(urls: list[str]) -> list[int]:
    """
    Return Content-Length for each URL (0 if missing/error). Rewrites local hosts so DNS doesn’t fail.
    Memoized per-URL and rate-limited concurrency.
    """
    if not urls:
        return []
    sem = asyncio.Semaphore(12)

    async def _probe(client, u: str) -> int:
        tgt = _rewrite_wp_media_host(u)
        if tgt in _SIZE_CACHE:
            return _SIZE_CACHE[tgt]
        async with sem:
            sz = await head_content_length(client, tgt)
        _SIZE_CACHE[tgt] = sz
        return sz

    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False, follow_redirects=True) as client:
            return await asyncio.gather(*(_probe(client, u) for u in urls))
    except Exception as e:
        logger.debug("HEAD client error: %s", e)
        return [0] * len(urls)

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
# 2. Shared prep
# =========================

async def _prepare_context(*, dry_run: bool, skus: Optional[List[str]] = None) -> Dict[str, Any]:
    """Fetch ERP/Woo state, build matrices, ensure taxonomies, and return everything needed for the core sync."""
    # Categories: need Woo cat IDs
    await get_erpnext_categories()
    await get_wc_categories()
    category_report = await sync_categories(dry_run=dry_run)
    wc_categories = await get_wc_categories()  # refresh after potential creation

    # ERP items, prices, stock
    erp_items = await get_erpnext_items()

    price_map, price_list_name, price_count = await resolve_price_map(get_price_map, settings.ERP_SELLING_PRICE_LIST)
    if price_list_name:
        logger.info("Using price list: %s with %d prices", price_list_name, price_count)
    else:
        logger.info("Using price list with %d prices", price_count)

    stock_map = await get_stock_map()

    # Attributes & variant matrix
    erp_attr_order = await maybe_await(get_erpnext_attribute_order())
    attribute_map = await maybe_await(get_erpnext_attribute_map(erp_attr_order))

    template_variant_matrix = build_variant_matrix(erp_items, attribute_map, erp_attr_order)

    # Fallbacks if ERP matrix yields no multi-variant templates
    if not any(len(v.get("variants", [])) > 1 for v in (template_variant_matrix or {}).values()):
        fb = build_fallback_variant_matrix(erp_items)
        for k, v in fb.items():
            template_variant_matrix.setdefault(k, v)

    fb_base = build_fallback_variant_matrix_by_base(erp_items, erp_attr_order, attribute_map)
    base_or_template = fb_base if fb_base else template_variant_matrix

    unified_matrix = merge_simple_items_into_matrix(erp_items, base_or_template)
    variant_matrix = filter_variant_matrix_by_sku(unified_matrix, skus) if skus else unified_matrix

    # Attribute order for preview (based on real SKUs)
    attribute_order_for_preview = infer_global_attribute_order_from_skus(
        erp_items, attribute_map, erp_attr_order
    )

    # Attributes/Brand taxonomy ensure or preview
    used_attr_vals = collect_used_attribute_values(variant_matrix)
    used_attr_vals = {k: v for k, v in used_attr_vals.items() if str(k).strip().lower() != "brand"}  # exclude Brand

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
        brand_report = await reconcile_woocommerce_brands(
            brands, delete_missing=False, dry_run=True
        )
    else:
        attribute_report = await ensure_wc_attributes_and_terms(used_attr_vals)
        brands = collect_erp_brands_from_items(erp_items)
        brand_report = await reconcile_woocommerce_brands(
            brands, delete_missing=False, dry_run=False
        )

    # Woo state + category map
    wc_products = await get_wc_products()
    wc_cat_map = build_wc_cat_map(wc_categories)

    return {
        "category_report": category_report,
        "brand_report": brand_report,
        "attribute_report": attribute_report,
        "erp_items": erp_items,
        "wc_products": wc_products,
        "wc_cat_map": wc_cat_map,
        "price_map": price_map,
        "price_list_name": price_list_name,
        "stock_map": stock_map,
        "attribute_map": attribute_map,
        "attribute_order_for_preview": attribute_order_for_preview,
        "variant_matrix": variant_matrix,
    }

# =========================
# 3. Sync Entry Points
# =========================

async def sync_products_full(dry_run: bool = False, purge_bin: bool = True) -> Dict[str, Any]:
    if dry_run:
        sync_type = "Preview"
    else:
        sync_type = "Full"

    logger.info(f"🔁 [SYNC] Starting {sync_type} ERPNext → Woo sync (dry_run=%s)", dry_run)

    if not dry_run and purge_bin:
        await purge_woo_bin_if_needed(True)

    ctx = await _prepare_context(dry_run=dry_run, skus=None)

    # Core preview/sync (performs real mutations when dry_run=False)
    sync_report = await sync_all_templates_and_variants(
        variant_matrix=ctx["variant_matrix"],
        wc_products=ctx["wc_products"],
        wc_cat_map=ctx["wc_cat_map"],
        price_map=ctx["price_map"],
        attribute_map=ctx["attribute_map"],
        stock_map=ctx["stock_map"],
        attribute_order=ctx["attribute_order_for_preview"],
        dry_run=dry_run,
        # Be explicit: on the main pass we *do not* preserve parent attrs/images
        # (we want the new intersections/fallbacks to apply during a real full sync).
        preserve_parent_attrs_on_update=False,
        erp_items=ctx["erp_items"],  # <-- pass ERP items so simples land in shipping_params.json
    )

    # Save Sync Preview (products_to_sync.json)
    try:
        if dry_run:
            # Admin UI needs this for selection
            save_preview_to_file(sync_report, source="full", dry_run=True, skus=None)
        else:
            # Post-sync refresh in the background so the response isn’t blocked
            async def _refresh():
                try:
                    ctx2 = await _prepare_context(dry_run=True, skus=None)
                    snap = await sync_all_templates_and_variants(
                        variant_matrix=ctx2["variant_matrix"],
                        wc_products=ctx2["wc_products"],
                        wc_cat_map=ctx2["wc_cat_map"],
                        price_map=ctx2["price_map"],
                        attribute_map=ctx2["attribute_map"],
                        stock_map=ctx2["stock_map"],
                        attribute_order=ctx2["attribute_order_for_preview"],
                        dry_run=True,
                        # For the *preview* pass, preserve parent attrs/images to avoid
                        # accidental shrinking in the snapshot.
                        preserve_parent_attrs_on_update=True,
                        erp_items=ctx2["erp_items"],  # <-- pass ERP items in refresh too
                    )
                    save_preview_to_file(snap, source="post-full", dry_run=True, skus=None)
                except Exception as ie:
                    logger.warning(f"[FULL] post-sync preview refresh failed: {ie}")
            # Fire and forget background refresh
            asyncio.create_task(_refresh())
    except Exception as e:
        logger.error(f"Failed to write products_to_sync.json: {e}")

    logger.info(f"✅ [SYNC] {sync_type} sync complete (dry_run=%s)", dry_run)
    return {
        "category_report": ctx["category_report"],
        "attribute_report": ctx["attribute_report"],
        "brand_report": ctx["brand_report"],
        "sync_report": sync_report,
        "price_list_used": ctx["price_list_name"] or (settings.ERP_SELLING_PRICE_LIST or "Standard Selling"),
        "attribute_order": ctx["attribute_order_for_preview"],
        "dry_run": dry_run,
    }

async def sync_preview() -> Dict[str, Any]:
    return await sync_products_full(dry_run=True, purge_bin=False)

async def sync_products_partial(skus_to_sync: List[str], dry_run: bool = False) -> Dict[str, Any]:
    """
    Partial sync:
      - If skus_to_sync is provided (from UI selection), use those.
      - Otherwise, read /app/mapping/products_to_sync.json and compute targets:
          simples:  to_create + to_update
          variants: variant_to_create + variant_to_update
      - Filter the ERP context to ONLY those SKUs.
      - Preserve parent attributes/images when parent already exists (avoid shrinking).
    """
    import os
    import json

    logger.info("🔁 [SYNC] Starting PARTIAL ERPNext → Woo sync (dry_run=%s)", dry_run)
    PREVIEW_PATH = "/app/mapping/products_to_sync.json"

    def _is_variation(s: str) -> bool:
        return len([p for p in (s or "").split("-") if p]) >= 3

    def _load_preview_targets() -> set[str]:
        if not os.path.exists(PREVIEW_PATH):
            logger.warning("[PARTIAL] Preview file %s not found; falling back to provided skus_to_sync.", PREVIEW_PATH)
            return set()
        try:
            with open(PREVIEW_PATH, "r", encoding="utf-8") as f:
                j = json.load(f) or {}
        except Exception as e:
            logger.warning("[PARTIAL] Failed to read preview file: %s; falling back to provided SKUs.", e)
            return set()

        targets = set()
        # Only take actionable buckets for now
        for key in ("to_create", "to_update", "variant_to_create", "variant_to_update"):
            for row in (j.get(key) or []):
                sku = (row or {}).get("sku")
                if sku:
                    targets.add(sku)
        return targets

    # 1) Decide target SKUs
    preview_targets = _load_preview_targets()
    user_targets = set(skus_to_sync or [])
    if user_targets:
        # If preview exists, intersect; if not, trust user list
        targets = (user_targets & (preview_targets or user_targets))
    else:
        targets = preview_targets

    if not targets:
        logger.info("[PARTIAL] No targets to sync. Nothing to do.")
        return {
            "brand_report": {"created": [], "updated": [], "deleted": [], "skipped": [], "total_erp_brands": 0, "total_wc_brands": 0, "dry_run": dry_run, "delete_missing": False},
            "attribute_report": {"count": 0, "attributes": [], "dry_run": dry_run},
            # Keep this shape IDENTICAL to sync_all_templates_and_variants
            "sync_report": {
                "created": [], "updated": [], "skipped": [], "errors": [], "mapping": {},
                "to_create": [], "to_update": [], "already_synced": [],
                "variant_parents": [], "variant_to_create": [], "variant_to_update": [], "variant_synced": [],
            },
            "attribute_order": [], "dry_run": dry_run,
        }

    # Make runs stable/reproducible
    targets = sorted(targets)
    logger.info("[PARTIAL] %d target SKU(s): %s%s",
                len(targets), ", ".join(targets[:10]), (" …" if len(targets) > 10 else ""))

    # 2) Prepare ERP/Woo context (ERP loads enough to resolve families & attrs)
    ctx = await _prepare_context(dry_run=dry_run, skus=list(targets))

    # 3) Filter the variant_matrix down to ONLY the selected SKUs
    filtered_matrix: Dict[str, Dict[str, Any]] = {}
    parent_skus_needed = set()
    simple_skus = {s for s in targets if not _is_variation(s)}
    variation_skus = {s for s in targets if _is_variation(s)}

    for parent_sku, family in (ctx.get("variant_matrix") or {}).items():
        variants = family.get("variants") or []
        attr_matrix = family.get("attribute_matrix") or []
        keep_variants, keep_attrs = [], []

        for idx, v in enumerate(variants):
            vsku = v.get("item_code") or v.get("sku") or ""
            is_var = _is_variation(vsku)
            if (not is_var and vsku in simple_skus) or (is_var and vsku in variation_skus):
                keep_variants.append(v)
                keep_attrs.append(attr_matrix[idx] if idx < len(attr_matrix) else {})

        # For a simple product, the "parent_sku" equals the SKU.
        # For a variable family, include the family if we kept at least one child.
        if keep_variants:
            new_family = dict(family)
            new_family["variants"] = keep_variants
            new_family["attribute_matrix"] = keep_attrs
            filtered_matrix[parent_sku] = new_family
            parent_skus_needed.add(parent_sku)

    # 4) Filter wc_products to reduce surface area (parents + simples only)
    wc_products_filtered = []
    for p in (ctx.get("wc_products") or []):
        sku = p.get("sku")
        if not sku:
            continue
        # keep product if it is:
        #  - a simple SKU directly targeted, OR
        #  - a variable parent for a family we are touching
        if (sku in simple_skus) or (sku in parent_skus_needed):
            wc_products_filtered.append(p)

    # 5) Run the sync on the filtered subset; tell sync_all to preserve parent attrs/images on update
    sync_report = await sync_all_templates_and_variants(
        variant_matrix=filtered_matrix,
        wc_products=wc_products_filtered,
        wc_cat_map=ctx["wc_cat_map"],
        price_map=ctx["price_map"],
        attribute_map=ctx["attribute_map"],
        stock_map=ctx["stock_map"],
        attribute_order=ctx["attribute_order_for_preview"],
        dry_run=dry_run,
        preserve_parent_attrs_on_update=True,   # avoid shrinking options/images in partial
        erp_items=ctx["erp_items"],             # <-- pass ERP items
    )

    # 6) After a REAL partial sync, refresh the preview snapshot in the background
    if not dry_run:
        async def _refresh():
            try:
                ctx2 = await _prepare_context(dry_run=True, skus=None)
                snap = await sync_all_templates_and_variants(
                    variant_matrix=ctx2["variant_matrix"],
                    wc_products=ctx2["wc_products"],
                    wc_cat_map=ctx2["wc_cat_map"],
                    price_map=ctx2["price_map"],
                    attribute_map=ctx2["attribute_map"],
                    stock_map=ctx2["stock_map"],
                    attribute_order=ctx2["attribute_order_for_preview"],
                    dry_run=True,
                    preserve_parent_attrs_on_update=True,
                    erp_items=ctx2["erp_items"],   # <-- pass ERP items in refresh too
                )
                save_preview_to_file(snap, source="post-partial", dry_run=True, skus=None)
            except Exception as ie:
                logger.warning("[PARTIAL] post-sync preview refresh failed: %s", ie)
        asyncio.create_task(_refresh())

    logger.info("✅ [SYNC] Completed PARTIAL ERPNext → Woo sync (dry_run=%s)", dry_run)
    return {
        "brand_report": ctx["brand_report"],
        "attribute_report": ctx["attribute_report"],
        "sync_report": sync_report,
        "attribute_order": ctx["attribute_order_for_preview"],
        "dry_run": dry_run
    }

# =========================
# 4. Core preview/sync
# =========================

async def sync_all_templates_and_variants(
    variant_matrix: Dict[str, Dict[str, Any]],
    wc_products,
    wc_cat_map,
    price_map,
    attribute_map: Optional[Dict[str, AttributeValueMapping]] = None,
    stock_map: Optional[Dict[str, float]] = None,
    attribute_order: Optional[List[str]] = None,
    dry_run: bool = False,
    preserve_parent_attrs_on_update: bool = False,
    erp_items: Optional[List[dict]] = None,   # keep param
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
          - Variation image: featured of that variant.
          - Simple product: featured + its attachments in creation order.
      • Image linking: always send Woo 'images' (parent/simple) and 'image' (variation) with media IDs.
        If Woo ignores them on first call, do a correcting PUT.
      • Shipping:
          - Maintain /app/mapping/shipping_params.json with editable per-SKU weight/dimensions/class.
          - Apply to simple products and each variation (and optionally to the variable parent for class).
      • Admin-UI preview:
          - Add delete candidates, per-row woo/meta, parent_sku for variations, and a meta counts block.
    """

    report = {
        "created": [], "updated": [], "skipped": [], "errors": [], "mapping": {},
        "to_create": [], "to_update": [], "to_delete": [],
        "already_synced": [],
        "variant_parents": [], "variant_to_create": [], "variant_to_update": [],
        "variant_to_delete": [], "variant_parents_to_delete": [], "variant_synced": [],
    }

    wc_product_index = {p.get("sku"): p for p in (wc_products or []) if p.get("sku")}
    seen_skus = set()
    touched_skus = set()
    variation_skus_seen: set[str] = set()

    SHIPPING_PARAMS_PATH = "/app/mapping/shipping_params.json"
    DEFAULT_SHIP = {"weight_kg": 0, "length_cm": 0, "width_cm": 0, "height_cm": 0, "shipping_class": ""}

    shipping_skeleton = {
        "generated_at": None,
        "defaults": DEFAULT_SHIP.copy(),
        "simples": {},
        "variables": {},
        "meta": {
            "units": {"weight": "kg", "dimensions": "cm"},
            "notes": "Edit values per SKU. Leave 0/blank to skip. 'shipping_class' accepts Woo class slug or name."
        }
    }

    WC_API = settings.WC_BASE_URL.rstrip("/") + "/wp-json/wc/v3"
    WP_BRAND_API = settings.WC_BASE_URL.rstrip("/") + "/wp-json/wp/v2/product_brand"

    brand_id_cache: dict[str, int] = {}
    _ship_class_cache_by_slug: dict[str, dict] = {}
    _ship_class_cache_by_name: dict[str, dict] = {}
    _ship_classes_loaded = False

    def _now_iso():
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def _atomic_write_json(path: str, obj: dict):
        import json, os
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def _load_json_or_empty(path: str) -> dict:
        import json, os
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def _merge_shipping_values(skeleton: dict, existing: dict, *, keep_unknown: bool = True) -> dict:
        defaults = skeleton.get("defaults", DEFAULT_SHIP)
        def _fill(d: dict) -> dict:
            v = {"weight_kg": 0, "length_cm": 0, "width_cm": 0, "height_cm": 0, "shipping_class": ""}
            v.update({k: d.get(k) for k in v.keys() if d.get(k) is not None})
            for k in v.keys():
                if v[k] in (None, ""):
                    v[k] = defaults.get(k, 0 if k != "shipping_class" else "")
            return v

        new_obj = {
            "generated_at": _now_iso(),
            "defaults": defaults,
            "simples": {},
            "variables": {},
            "meta": skeleton.get("meta", {}),
        }

        if keep_unknown:
            for sku, spec in (existing.get("simples") or {}).items():
                if isinstance(spec, dict):
                    new_obj["simples"][sku] = _fill(spec)
            for parent, pv in (existing.get("variables") or {}).items():
                eparent = (pv or {}).get("parent") or {}
                new_obj["variables"].setdefault(parent, {"parent": {"shipping_class": eparent.get("shipping_class", "")}, "variations": {}})
                for vsku, vspec in ((pv or {}).get("variations") or {}).items():
                    if isinstance(vspec, dict):
                        new_obj["variables"][parent]["variations"][vsku] = _fill(vspec)

        for sku in (skeleton.get("simples") or {}):
            new_obj["simples"].setdefault(sku, _fill({}))

        for parent, pv in (skeleton.get("variables") or {}).items():
            new_obj["variables"].setdefault(parent, {"parent": {"shipping_class": ((existing.get("variables") or {}).get(parent) or {}).get("parent", {}).get("shipping_class", "")}, "variations": {}})
            for vsku in ((pv or {}).get("variations") or {}):
                new_obj["variables"][parent]["variations"].setdefault(vsku, _fill({}))

        return new_obj

    def _upsert_mapping(
        sku: str, *, template: str, attributes: dict, brand: Optional[str], categories: list[str],
        woo_product_id: Optional[int] = None, woo_status: Optional[str] = None,
    ):
        m = report["mapping"].setdefault(sku, {
            "template": template, "attributes": {}, "brand": None, "categories": [],
            "woo_product_id": None, "woo_status": None,
        })
        m["template"] = template
        m["attributes"] = attributes or {}
        m["brand"] = brand
        m["categories"] = categories or []
        if woo_product_id is not None:
            try: m["woo_product_id"] = int(woo_product_id)
            except Exception: m["woo_product_id"] = None
        if woo_status is not None:
            m["woo_status"] = str(woo_status)

    def _sku_parts(s: str) -> list[str]:
        return [p for p in (s or "").split("-") if p]

    def _family_is_variable(variants, template_code: str, wc_product_index: dict) -> bool:
        if any(len(_sku_parts(v.get("item_code") or v.get("sku") or template_code)) >= 3 for v in (variants or [])):
            return True
        existing = wc_product_index.get(template_code)
        if existing and (existing.get("type") or "").lower() == "variable":
            return True
        return False

    def _slugify(text: str) -> str:
        import re, unicodedata
        s = unicodedata.normalize("NFKD", str(text or ""))
        s = s.encode("ascii", "ignore").decode("ascii")
        s = re.sub(r"[^A-Za-z0-9\-\s]", "", s)
        s = re.sub(r"\s+", "-", s).strip("-").lower()
        return s

    async def _ensure_shipping_classes_loaded():
        nonlocal _ship_classes_loaded
        if _ship_classes_loaded:
            return
        auth = (settings.WC_API_KEY, settings.WC_API_SECRET)
        page = 1
        while True:
            r = await _request_with_retry("GET", f"{WC_API}/products/shipping_classes?per_page=100&page={page}", auth=auth, max_attempts=3, timeout=30.0)
            if r.status_code != 200:
                break
            arr = r.json() or []
            if not arr:
                break
            for sc in arr:
                slug = (sc.get("slug") or "").strip().lower()
                name = (sc.get("name") or "").strip()
                if slug: _ship_class_cache_by_slug[slug] = sc
                if name: _ship_class_cache_by_name[name.lower()] = sc
            if len(arr) < 100:
                break
            page += 1
        _ship_classes_loaded = True

    async def _resolve_shipping_class_slug(name_or_slug: str, create_if_missing: bool) -> Optional[str]:
        val = (name_or_slug or "").strip()
        if not val:
            return None
        await _ensure_shipping_classes_loaded()
        guess_slug = _slugify(val)
        if guess_slug in _ship_class_cache_by_slug:
            return guess_slug
        hit = _ship_class_cache_by_name.get(val.lower())
        if hit and (hit.get("slug") or "").lower():
            return (hit["slug"] or "").lower()
        if create_if_missing and not dry_run:
            auth = (settings.WC_API_KEY, settings.WC_API_SECRET)
            payload = {"name": val, "slug": guess_slug}
            r = await _request_with_retry("POST", f"{WC_API}/products/shipping_classes", auth=auth, json=payload)
            if r.status_code in (200, 201):
                sc = r.json() or {}
                slug = (sc.get("slug") or "").lower()
                if slug:
                    _ship_class_cache_by_slug[slug] = sc
                    nm = (sc.get("name") or "")
                    if nm: _ship_class_cache_by_name[nm.lower()] = sc
                    logger.info("[SHIPPING] Created Woo shipping class '%s' (slug=%s)", nm or val, slug)
                    return slug
            else:
                logger.warning("[SHIPPING] Failed to create class '%s' (%s)", val, r.status_code)
        return None

    def _fmt_weight(v) -> Optional[str]:
        try:
            f = float(v)
            if f <= 0: return None
            s = f"{f:.3f}"
            return s.rstrip("0").rstrip(".")
        except Exception:
            return None

    def _fmt_dim(v) -> Optional[str]:
        try:
            f = float(v)
            if f <= 0: return None
            s = f"{f:.1f}"
            return s.rstrip("0").rstrip(".")
        except Exception:
            return None

    async def _apply_shipping_to_product_payload(payload: dict, ship_rec: Optional[dict], *, create_class: bool):
        if not ship_rec or not isinstance(ship_rec, dict):
            return
        wt = _fmt_weight(ship_rec.get("weight_kg"))
        L = _fmt_dim(ship_rec.get("length_cm"))
        W = _fmt_dim(ship_rec.get("width_cm"))
        H = _fmt_dim(ship_rec.get("height_cm"))
        dims = {}
        if L: dims["length"] = L
        if W: dims["width"]  = W
        if H: dims["height"] = H
        if wt: payload["weight"] = wt
        if dims: payload["dimensions"] = dims
        sc = (ship_rec.get("shipping_class") or "").strip()
        if sc:
            slug = await _resolve_shipping_class_slug(sc, create_if_missing=create_class)
            if slug: payload["shipping_class"] = slug

    async def _request_with_retry(method: str, url: str, *, auth=None, json=None, max_attempts: int = 3, timeout: float = 40.0):
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, verify=False, auth=auth) as client:
                    if method == "GET":    return await client.get(url)
                    elif method == "POST": return await client.post(url, json=json)
                    elif method == "PUT":  return await client.put(url, json=json)
                    elif method == "DELETE": return await client.delete(url, json=json)
                    else: raise ValueError(f"Unsupported method: {method}")
            except Exception as e:
                last_exc = e
                delay = 0.5 * (2 ** (attempt - 1))
                logger.warning(f"[HTTP RETRY] {method} {url} failed (attempt {attempt}/{max_attempts}): {e}. Retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
        raise last_exc

    async def _upload_with_retry(url: str, fname: str, tries: int = 3):
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

    def _trim_log(resp, max_len: int = 500):
        try:
            def _maybe_trim_str(s: str) -> str:
                if not isinstance(s, str): return s
                if _HTML_SIG_RE.search(s): return _summarize_html(s, limit=180)
                return (s if len(s) <= max_len else s[:max_len] + "…")
            if isinstance(resp, dict):
                r = dict(resp)
                if "raw" in r: r["raw"] = _maybe_trim_str(r["raw"])
                if "data" in r and isinstance(r["data"], str): r["data"] = _maybe_trim_str(r["data"])
                return r
            if isinstance(resp, str): return _maybe_trim_str(resp)
        except Exception:
            pass
        return resp

    async def _load_brand_id_cache():
        if brand_id_cache:
            return
        auth = (settings.WP_USERNAME, settings.WP_PASSWORD)
        page = 1
        async with httpx.AsyncClient(timeout=20.0, verify=False, auth=auth) as client:
            while True:
                r = await client.get(f"{WP_BRAND_API}?per_page=100&page={page}")
                if r.status_code != 200: break
                arr = r.json() or []
                if not arr: break
                for b in arr:
                    name = (b.get("name") or "").strip()
                    bid = b.get("id")
                    if name and bid: brand_id_cache[name.lower()] = int(bid)
                if len(arr) < 100: break
                page += 1

    def _brand_payload(brand_name: Optional[str]) -> list[dict]:
        if not brand_name: return []
        bid = brand_id_cache.get(str(brand_name).strip().lower())
        return [{"id": bid}] if bid else []

    def _price_str(v: Optional[float]) -> Optional[str]:
        if v is None: return None
        try: return f"{float(v):.2f}"
        except Exception: return None

    async def _get_product_by_sku(sku: str) -> Optional[dict]:
        from urllib.parse import quote_plus
        auth = (settings.WC_API_KEY, settings.WC_API_SECRET)
        url = f"{WC_API}/products?sku={quote_plus(sku)}"
        r = await _request_with_retry("GET", url, auth=auth, max_attempts=3, timeout=30.0)
        if r.status_code == 200:
            arr = r.json() or []
            if arr: return arr[0]
        return None

    async def _get_product_by_id(pid: int) -> Optional[dict]:
        auth = (settings.WC_API_KEY, settings.WC_API_SECRET)
        url = f"{WC_API}/products/{pid}"
        r = await _request_with_retry("GET", url, auth=auth, max_attempts=3, timeout=30.0)
        if r.status_code in (200, 201): return r.json()
        return None

    async def _get_variations_map(product_id: int) -> dict:
        out = {}
        auth = (settings.WC_API_KEY, settings.WC_API_SECRET)
        page = 1
        while True:
            r = await _request_with_retry("GET", f"{WC_API}/products/{product_id}/variations?per_page=100&page={page}", auth=auth, max_attempts=3, timeout=40.0)
            if r.status_code != 200: break
            arr = r.json() or []
            if not arr: break
            for v in arr:
                sku = (v.get("sku") or "").strip()
                if sku: out[sku] = v
                for a in v.get("attributes", []):
                    if (a.get("name") or "").strip().lower() == "sheet size":
                        opt = (a.get("option") or "").strip()
                        if opt: out[f"size::{opt.lower()}"] = v
            if len(arr) < 100: break
            page += 1
        return out

    async def _create_or_update_product_by_sku(sku: str, payload: dict) -> dict:
        auth = (settings.WC_API_KEY, settings.WC_API_SECRET)
        parts = [p for p in (sku or "").split("-") if p]
        if (payload.get("type") != "variable") and (len(parts) >= 3 or sku in variation_skus_seen):
            logger.warning("[BLOCK] Top-level product call blocked for variation SKU %s", sku)
            return {"status_code": 409, "data": {"code": "blocked_variation_sku", "message": "SKU belongs to a variable product's variation"}, "raw": ""}

        if sku in touched_skus and sku in wc_product_index:
            pid = wc_product_index[sku]["id"]
            r = await _request_with_retry("PUT", f"{WC_API}/products/{pid}", auth=auth, json=payload)
            data = {"status_code": r.status_code, "data": (r.json() if r.headers.get("content-type","").startswith("application/json") else {}), "raw": r.text}
            if r.status_code in (200, 201):
                wc_product_index[sku] = data["data"]
                logger.info(f"[UPDATE] Woo product updated (sku={sku}, id={wc_product_index[sku]['id']})")
            else:
                logger.error(f"[WC] update product {r.status_code} {r.headers.get('content-type')} body={data['data']}")
            return data

        if sku not in wc_product_index:
            found = await _get_product_by_sku(sku)
            if found: wc_product_index[sku] = found

        if sku not in wc_product_index:
            r = await _request_with_retry("POST", f"{WC_API}/products", auth=auth, json=payload)
            data = {"status_code": r.status_code, "data": (r.json() if r.headers.get("content-type","").startswith("application/json") else {}), "raw": r.text}
            if r.status_code in (200, 201):
                prod = data["data"]
                wc_product_index[sku] = prod
                touched_skus.add(sku)
                logger.info(f"[CREATE] Woo product created (sku={sku}, id={prod['id']})")
                return data

            body = data.get("data") or {}
            if body.get("code") == "product_invalid_sku":
                logger.error(f"[WC] create product {r.status_code} {r.headers.get('content-type')} body={body}")
                recovered = await _get_product_by_sku(sku)
                if not recovered:
                    rid = (body.get("data") or {}).get("resource_id")
                    if isinstance(rid, int):
                        recovered = await _get_product_by_id(rid)
                if recovered:
                    wc_product_index[sku] = recovered
                    pid = recovered["id"]
                    r2 = await _request_with_retry("PUT", f"{WC_API}/products/{pid}", auth=auth, json=payload)
                    data2 = {"status_code": r2.status_code, "data": (r2.json() if r2.headers.get("content-type","").startswith("application/json") else {}), "raw": r2.text}
                    if r2.status_code in (200, 201):
                        wc_product_index[sku] = data2["data"]
                        touched_skus.add(sku)
                        logger.info(f"[UPDATE] Woo product updated (sku={sku}, id={wc_product_index[sku]['id']})")
                    else:
                        logger.error(f"[WC] update after duplicate {r2.status_code} {r2.headers.get('content-type')} body={data2['data']}")
                    return data2

            return data

        pid = wc_product_index[sku]["id"]
        r = await _request_with_retry("PUT", f"{WC_API}/products/{pid}", auth=auth, json=payload)
        data = {"status_code": r.status_code, "data": (r.json() if r.headers.get("content-type","").startswith("application/json") else {}), "raw": r.text}
        if r.status_code in (200, 201):
            wc_product_index[sku] = data["data"]
            touched_skus.add(sku)
            logger.info(f"[UPDATE] Woo product updated (sku={sku}, id={wc_product_index[sku]['id']})")
        else:
            logger.error(f"[WC] update product {r.status_code} {r.headers.get('content-type')} body={data['data']}")
        return data

    async def _create_or_update_variation(parent_id: int, sku: str, size_option: str, payload: dict, var_map: dict) -> dict:
        auth = (settings.WC_API_KEY, settings.WC_API_SECRET)
        existing = var_map.get(sku) or var_map.get(f"size::{(size_option or '').lower()}")
        if existing:
            vid = existing["id"]
            r = await _request_with_retry("PUT", f"{WC_API}/products/{parent_id}/variations/{vid}", auth=auth, json=payload)
            data = {"status_code": r.status_code, "data": (r.json() if r.headers.get("content-type","").startswith("application/json") else {}), "raw": r.text}
            if r.status_code in (200, 201):
                logger.info(f"[VAR][UPDATE] sku={sku} (vid={vid})")
            else:
                logger.error(f"[WC] update variation {r.status_code} {r.headers.get('content-type')} body={data['data']}")
            return data
        r = await _request_with_retry("POST", f"{WC_API}/products/{parent_id}/variations", auth=auth, json=payload)
        data = {"status_code": r.status_code, "data": (r.json() if r.headers.get("content-type","").startswith("application/json") else {}), "raw": r.text}
        if r.status_code in (200, 201):
            logger.info(f"[VAR][CREATE] sku={sku} (pid={parent_id})")
        else:
            logger.error(f"[WC] create variation {r.status_code} {r.headers.get('content-type')} body={data['data']}")
        return data

    def _normalize_size_label(val: str) -> str:
        import re
        s = str(val or "")
        s = re.sub(r"\s*[xX×]\s*", " x ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _rewrite_host_for_wc(url: str) -> str:
        try:
            if not url: return url
            u = urlparse(url)
            base = urlparse(settings.WC_BASE_URL)
            if not u.netloc or not base.netloc or u.netloc == base.netloc: return url
            return urlunparse((base.scheme or u.scheme, base.netloc, u.path, u.params, u.query, u.fragment))
        except Exception:
            return url

    def _galleries_match_loose(a: list[dict], b: list[dict], *, tol: int = 0) -> bool:
        def _sizes(lst):
            out = []
            for d in lst or []:
                try:
                    v = int((d or {}).get("size") or 0)
                    if v > 0: out.append(v)
                except Exception:
                    pass
            return out
        sa, sb = _sizes(a), _sizes(b)
        if len(sa) != len(sb): return False
        if tol <= 0: return Counter(sa) == Counter(sb)
        def _bucketed(xs): return Counter([(x // tol) for x in xs])
        return _bucketed(sa) == _bucketed(sb)

    await _load_brand_id_cache()

    # --- Pre-pass: compute ERP sets for delete detection & parent preservation
    erp_parent_skus: set[str] = set()
    erp_simple_skus: set[str] = set()
    erp_variations_by_parent: dict[str, set[str]] = defaultdict(set)
    erp_prefixes: set[str] = set()

    for template_code, data in (variant_matrix or {}).items():
        variants = data.get("variants") or []
        is_variable = _family_is_variable(variants, template_code, wc_product_index)
        if is_variable:
            erp_parent_skus.add(template_code)
            for v in variants:
                vsku = v.get("item_code") or v.get("sku") or ""
                if vsku:
                    erp_variations_by_parent[template_code].add(vsku)
                    erp_prefixes.add(_sku_parts(vsku)[0] if _sku_parts(vsku) else "")
        else:
            for v in variants:
                ssku = v.get("item_code") or v.get("sku") or ""
                if ssku:
                    erp_simple_skus.add(ssku)
                    erp_prefixes.add(_sku_parts(ssku)[0] if _sku_parts(ssku) else "")

    shipping_existing = _load_json_or_empty(SHIPPING_PARAMS_PATH)

    for template_code, data in (variant_matrix or {}).items():
        template_item = data["template_item"]
        variants = data["variants"]
        attr_matrix = data.get("attribute_matrix") or [{} for _ in variants]

        is_variable = _family_is_variable(variants, template_code, wc_product_index)

        # Build parent options
        options_by_attr: Dict[str, set] = defaultdict(set)
        for rec in (attr_matrix or []):
            if not isinstance(rec, dict): continue
            for aname, v in rec.items():
                if not isinstance(v, dict): continue
                val = v.get("value")
                if val is None or not str(val).strip(): continue
                if str(aname).strip().lower() == "sheet size":
                    options_by_attr[aname].add(_normalize_size_label(val))
                else:
                    options_by_attr[aname].add(str(val).strip())

        sheet_sizes = sorted(options_by_attr.get("Sheet Size", set()))

        parent_wc = wc_product_index.get(template_code) if is_variable else None
        existing_parent_size_opts: list[str] = []
        if preserve_parent_attrs_on_update and parent_wc:
            for a in (parent_wc.get("attributes") or []):
                if isinstance(a, dict) and (a.get("name") or "").strip().lower() == "sheet size":
                    existing_parent_size_opts = [_normalize_size_label(o) for o in (a.get("options") or []) if o]
                    break
        sheet_sizes_for_preview = sorted(set(sheet_sizes) | set(existing_parent_size_opts)) if existing_parent_size_opts else sheet_sizes

        # Parent description diff (template → parent.description)
        erp_parent_desc = template_item.get("description") or ""
        wc_parent_long = (parent_wc or {}).get("description") or ""
        parent_desc_diff = strip_html(erp_parent_desc).strip() != strip_html(wc_parent_long).strip()

        logger.info(f"[FAMILY] parent={template_code} items={len(variants)} variable={bool(is_variable)}")
        existing_var_map_preview: dict = {}
        if is_variable:
            parent_sku = template_code
            parent_attrs_for_preview = [{
                "name": "Sheet Size",
                "options": sheet_sizes_for_preview if sheet_sizes_for_preview else [],
                "variation": True,
                "visible": True,
            }]
            logger.info("[ATTR][PARENT] %s attrs=['Sheet Size'] options=%s", parent_sku, {"Sheet Size": sheet_sizes_for_preview})
            report["variant_parents"].append({
                "sku": parent_sku,
                "name": template_item.get("item_name") or template_code,
                "has_variants": 1,
                "action": "Create" if not parent_wc else "Sync",
                "fields_to_update": "ALL" if not parent_wc else (["description"] if parent_desc_diff else "None"),
                "description_diff": bool(parent_desc_diff),
                "attributes": parent_attrs_for_preview,
                "woo": ({"id": parent_wc.get("id"), "status": parent_wc.get("status")} if parent_wc else None),
            })
            shipping_skeleton["variables"].setdefault(parent_sku, {"parent": {"shipping_class": ""}, "variations": {}})

            # Load existing variations for PREVIEW-only comparisons
            if dry_run and parent_wc and parent_wc.get("id"):
                try:
                    existing_var_map_preview = await _get_variations_map(parent_wc["id"])
                except Exception as e:
                    logger.debug(f"[VAR][PREVIEW] failed to load variations for {parent_sku}: {e}")

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
                    shipping_skeleton["variables"].setdefault(template_code, {"parent": {"shipping_class": ""}, "variations": {}})
                    shipping_skeleton["variables"][template_code]["variations"].setdefault(code, DEFAULT_SHIP.copy())
            family_rows = await _erp_get_file_rows_for_items(family_skus)

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
            parent_gallery_rel = []
            if family_skus:
                total = len(set(family_skus))
                for fu, names in per_file.items():
                    if len(names) == total:
                        parent_gallery_rel.append(fu)
            parent_gallery_rel.sort(key=lambda fu: created_at.get(fu, "") or fu)

            if len(set(family_skus)) == 1:
                try:
                    single_feat = await _erp_get_featured(family_skus[0])
                except Exception:
                    single_feat = None
                if single_feat:
                    if single_feat in parent_gallery_rel:
                        parent_gallery_rel = [single_feat] + [fu for fu in parent_gallery_rel if fu != single_feat]
                    else:
                        parent_gallery_rel = [single_feat] + parent_gallery_rel

            if not parent_gallery_rel and family_rows:
                union_created: dict[str, str] = {}
                union_list: list[str] = []
                seen_fu = set()
                for row in family_rows:
                    fu = row.get("file_url")
                    fld = (row.get("attached_to_field") or "").lower()
                    crt = row.get("creation")
                    if not fu or fld in {"image", "website_image"}:
                        continue
                    if fu not in seen_fu:
                        seen_fu.add(fu)
                        union_list.append(fu)
                    if fu not in union_created or (crt and str(crt) < str(union_created[fu])):
                        union_created[fu] = crt or ""
                union_list.sort(key=lambda fu: union_created.get(fu, "") or fu)
                parent_gallery_rel = union_list

            if not parent_gallery_rel and family_skus:
                first_code = family_skus[0]
                first_feat = await _erp_get_featured(first_code)
                rows_first = await _erp_get_file_rows_for_items([first_code])
                created_at_f: dict[str, str] = {}
                first_list: list[str] = []
                if first_feat: first_list.append(first_feat)
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
                    first_list.append(fu)
                seen_fu = set()
                fallback_gallery = []
                for fu in first_list:
                    if fu not in seen_fu:
                        seen_fu.add(fu)
                        fallback_gallery.append(fu)
                parent_gallery_rel = fallback_gallery

            if not dry_run and parent_gallery_rel:
                media_ids = []
                for fu in parent_gallery_rel:
                    absu = _abs_erp_file_url(fu)
                    try:
                        mid = await _upload_with_retry(absu, basename(absu))
                        if mid: media_ids.append(int(mid))
                    except Exception as e:
                        logger.error(f"[IMG][PARENT] upload failed for {template_code}: {e}")
                parent_media_ids = media_ids[:]
                parent_images_payload = [{"id": mid, "position": idx} for idx, mid in enumerate(media_ids)]
                logger.info(f"[IMG][PARENT] {template_code} linked {len(parent_images_payload)} images")

        # --- iterate children ---
        parent_id_for_vars: Optional[int] = None
        existing_var_map: dict = {}

        for i, variant in enumerate(variants):
            sku = variant.get("item_code") or variant.get("sku") or template_code
            if sku in seen_skus:
                continue
            seen_skus.add(sku)

            if not is_variable:
                shipping_skeleton["simples"].setdefault(sku, DEFAULT_SHIP.copy())
            else:
                variation_skus_seen.add(sku)

            attributes_entry = attr_matrix[i] if i < len(attr_matrix) else {}
            attributes_values = {}
            attributes_abbrs = {}
            if isinstance(attributes_entry, dict):
                for attr_name, rec in attributes_entry.items():
                    if not isinstance(rec, dict): continue
                    abbr = rec.get("abbr")
                    val = rec.get("value")
                    if abbr is not None: attributes_abbrs[attr_name] = abbr
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
                    try: price = float(v)
                    except Exception: price = None
            except Exception:
                price = None

            # BRAND
            brand = extract_brand(variant, template_item, attributes_entry)
            if family_brand is None and brand: family_brand = brand

            # CATEGORY
            categories = [normalize_category_name(variant.get("item_group") or template_item.get("item_group") or "Products")]
            _ = [wc_cat_map.get(cat) for cat in categories if cat in wc_cat_map]

            # DESCRIPTION: choose ERP side first, but compute DIFF *after* we resolve wc_prod (maybe from variation map)
            if is_variable:
                erp_desc_for_compare = variant.get("description") or ""
            else:
                erp_desc_for_compare = variant.get("description") or template_item.get("description") or ""

            # IMAGES (ERP gallery)
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
                    if not fu or fld in {"image", "website_image"}: continue
                    if featured_rel and fu == featured_rel: continue
                    if fu not in created_at_v or (crt and str(crt) < str(created_at_v[fu])): created_at_v[fu] = crt or ""
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
                    if not fu or fld in {"image", "website_image"}: continue
                    if featured_rel and fu == featured_rel: continue
                    if fu not in created_at_v or (crt and str(crt) < str(created_at_v[fu])): created_at_v[fu] = crt or ""
                    gallery_rel.append(fu)
                gallery_rel = list(dict.fromkeys(gallery_rel))
                gallery_rel.sort(key=lambda fu: created_at_v.get(fu, "") or fu)

            if featured_rel: erp_urls_abs.append(_abs_erp_file_url(featured_rel))
            for fu in gallery_rel:
                absu = _abs_erp_file_url(fu)
                if absu and absu not in erp_urls_abs: erp_urls_abs.append(absu)

            erp_sizes = await _head_sizes_for_urls(erp_urls_abs) if erp_urls_abs else []
            erp_gallery = [{"url": u, "size": (erp_sizes[idx] if idx < len(erp_sizes) else 0)} for idx, u in enumerate(erp_urls_abs)]

            # If variation, for PREVIEW try to read existing variation object so we can diff correctly
            if is_variable and not wc_prod and existing_var_map_preview:
                size_opt = (attributes_values.get("Sheet Size") or "").lower()
                wc_prod = existing_var_map_preview.get(sku) or (existing_var_map_preview.get(f"size::{size_opt}") if size_opt else None)

            # WOO images (preview): compute actual sizes from image src to avoid 0s
            wc_urls: list[str] = []
            if wc_prod:
                imgs = wc_prod.get("images")
                if isinstance(imgs, list) and imgs:
                    for img in imgs:
                        if isinstance(img, dict) and img.get("src"):
                            wc_urls.append(_rewrite_host_for_wc(img["src"]))
                else:
                    vimg = wc_prod.get("image")
                    if isinstance(vimg, dict) and vimg.get("src"):
                        wc_urls.append(_rewrite_host_for_wc(vimg["src"]))
            wc_sizes = await _head_sizes_for_urls(wc_urls) if wc_urls else []
            wc_gallery_for_compare = [{"url": u, "size": (wc_sizes[idx] if idx < len(wc_sizes) else 0)} for idx, u in enumerate(wc_urls)]
            gallery_diff = not _galleries_match_loose(erp_gallery, wc_gallery_for_compare)

            # STOCK
            stock_q = None
            try:
                total = 0.0
                found = False
                if isinstance(stock_map, dict):
                    for (code, _wh), q in stock_map.items():
                        if code == sku:
                            try: total += float(q or 0)
                            except Exception: pass
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

            # Now compute description diff using the resolved Woo object
            wc_desc = wc_prod.get("description") if wc_prod else ""
            desc_diff = strip_html(erp_desc_for_compare).strip() != strip_html(wc_desc).strip()

            # Decide preview action
            update_fields = []
            if desc_diff: update_fields.append("description")
            if gallery_diff: update_fields.append("gallery_images")
            if wc_prod is not None:
                wc_price_norm = _price_str(wc_prod.get("regular_price"))
                erp_price_norm = _price_str(price)
                if (erp_price_norm is not None) and (wc_price_norm != erp_price_norm):
                    update_fields.append("price")

            needs_create = wc_prod is None
            needs_update = bool(update_fields) and not needs_create

            woo_info = None
            if wc_prod:
                woo_info = {"id": wc_prod.get("id"), "status": wc_prod.get("status")}

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
                "wc_img_sizes": wc_sizes,
                "gallery_diff": gallery_diff,
                "description_diff": desc_diff,
                "has_variants": int(is_variable),
                "action": "Create" if needs_create else ("Update" if needs_update else "Synced"),
                "fields_to_update": "ALL" if needs_create else (update_fields or []),
                "woo": woo_info,
            }
            if is_variable:
                preview_entry["parent_sku"] = template_code

            if is_variable:
                if needs_create: report["variant_to_create"].append(preview_entry)
                elif needs_update: report["variant_to_update"].append(preview_entry)
                else: report["variant_synced"].append(preview_entry)
            else:
                if needs_create: report["to_create"].append(preview_entry)
                elif needs_update: report["to_update"].append(preview_entry)
                else: report["already_synced"].append(preview_entry)

            # ---- Real side effects ----
            if dry_run:
                woo_id = wc_prod.get("id") if wc_prod else None
                woo_status = wc_prod.get("status") if wc_prod else None
                _upsert_mapping(
                    sku, template=template_code, attributes=attributes_values, brand=brand, categories=categories,
                    woo_product_id=woo_id, woo_status=woo_status,
                )
                continue

            cats_payload = [{"id": wc_cat_map[c]} for c in categories if c in wc_cat_map]

            if is_variable:
                parent_sku = template_code
                if parent_id_for_vars is None:
                    union_sizes = sorted(set(sheet_sizes) | set(existing_parent_size_opts)) if (preserve_parent_attrs_on_update and existing_parent_size_opts) else sheet_sizes
                    parent_payload = {
                        "name": template_item.get("item_name") or template_code,
                        "sku": parent_sku,
                        "type": "variable",
                        "status": "publish",
                        "manage_stock": False,
                        "categories": cats_payload,
                        "brands": _brand_payload(family_brand),
                        "attributes": [{
                            "name": "Sheet Size", "variation": True, "visible": True,
                            "options": union_sizes if union_sizes else (
                                [] if attributes_values.get("Sheet Size") is None else [attributes_values.get("Sheet Size")]
                            ),
                        }],
                        # Parent long description from template
                        "description": template_item.get("description") or "",
                    }
                    if parent_images_payload:
                        parent_payload["images"] = parent_images_payload

                    parent_ship_class = (((shipping_existing.get("variables") or {}).get(parent_sku) or {}).get("parent") or {}).get("shipping_class")
                    if parent_ship_class:
                        await _apply_shipping_to_product_payload(parent_payload, {"shipping_class": parent_ship_class}, create_class=True)

                    logger.info(f"[PARENT][UPSERT] {parent_sku} with {len(parent_payload['attributes'])} attrs")
                    resp = await _create_or_update_product_by_sku(parent_sku, parent_payload)
                    if resp.get("status_code") not in (200, 201):
                        logger.error(f"[PARENT] create/update failed for {parent_sku}: {_trim_log(resp)}")
                        report["errors"].append({"sku": parent_sku, "error": resp})
                        parent_id_for_vars = None
                    else:
                        parent_id_for_vars = resp["data"]["id"]
                        logger.info(f"[PARENT][OK] id={parent_id_for_vars}")
                        try:
                            pdata = resp["data"]
                            _upsert_mapping(
                                parent_sku, template=template_code,
                                attributes={"Sheet Size": sheet_sizes_for_preview},
                                brand=family_brand, categories=categories,
                                woo_product_id=pdata.get("id"), woo_status=pdata.get("status"),
                            )
                        except Exception:
                            pass

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
                        except Exception as e:
                            logger.error(f"[IMG][VAR] upload failed for {sku}: {e}")
                            mid = None
                        if mid:
                            var_image_id = int(mid)

                    size_val = attributes_values.get("Sheet Size") or ""
                    var_payload = {
                        "sku": sku,
                        "regular_price": _price_str(price) or "0.00",
                        "manage_stock": (stock_q is not None),
                        "stock_quantity": (int(stock_q) if stock_q is not None else None),
                        "attributes": [{"name": "Sheet Size", "option": _normalize_size_label(size_val)}],
                        "status": "publish",
                        # Variation description from variant
                        "description": (variant.get("description") or ""),
                    }
                    if var_image_id:
                        var_payload["image"] = {"id": var_image_id}

                    var_ship_rec = (((shipping_existing.get("variables") or {}).get(parent_sku) or {}).get("variations") or {}).get(sku)
                    await _apply_shipping_to_product_payload(var_payload, var_ship_rec, create_class=True)

                    vresp = await _create_or_update_variation(parent_id_for_vars, sku, _normalize_size_label(size_val), var_payload, existing_var_map)
                    if vresp.get("status_code") not in (200, 201):
                        logger.error(f"[VAR] create/update failed for {sku}: {_trim_log(vresp)}")
                        report["errors"].append({"sku": sku, "error": vresp})
                    else:
                        try:
                            vdata = vresp["data"] or {}
                            _upsert_mapping(
                                sku, template=template_code, attributes=attributes_values, brand=brand, categories=categories,
                                woo_product_id=vdata.get("id"), woo_status=vdata.get("status"),
                            )
                        except Exception:
                            pass

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
                        if mid: image_ids.append(int(mid))
                    except Exception as e:
                        logger.error(f"[IMG][SIMPLE] upload failed for {sku}: {e}")
                images_payload = [{"id": mid, "position": idx} for idx, mid in enumerate(image_ids)]
                erp_desc = variant.get("description") or template_item.get("description") or ""

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

                simple_ship_rec = (shipping_existing.get("simples") or {}).get(sku)
                await _apply_shipping_to_product_payload(payload, simple_ship_rec, create_class=True)

                resp = await _create_or_update_product_by_sku(sku, payload)
                if resp.get("status_code") not in (200, 201):
                    logger.error(f"[CREATE] Woo product failed (sku={sku}): {_trim_log(resp)}")
                    report["errors"].append({"sku": sku, "error": resp})
                else:
                    try:
                        sdata = resp["data"]
                        _upsert_mapping(
                            sku, template=template_code, attributes=attributes_values, brand=brand, categories=categories,
                            woo_product_id=sdata.get("id"), woo_status=sdata.get("status"),
                        )
                    except Exception:
                        pass

                    assigned = resp["data"].get("images") or []
                    assigned_ids = [img.get("id") for img in assigned if isinstance(img, dict) and "id" in img]
                    want_ids = [img["id"] for img in images_payload]
                    if images_payload and sorted(assigned_ids) != sorted(want_ids):
                        logger.info("[SIMPLE][IMAGES] correcting images for %s: have=%s want=%s", sku, assigned_ids, want_ids)
                        auth_w = (settings.WC_API_KEY, settings.WC_API_SECRET)
                        _ = await _request_with_retry("PUT", f"{WC_API}/products/{resp['data']['id']}", auth=auth_w, json={"images": images_payload})

            _upsert_mapping(sku, template=template_code, attributes=attributes_values, brand=brand, categories=categories)

    # --- Fallback: ensure ERP standalone simples make it into the shipping file
    if erp_items:
        for item in erp_items:
            sku0 = (item.get("item_code") or item.get("name") or "").strip()
            if not sku0:
                continue
            if item.get("has_variants") or item.get("variant_of"):
                continue
            if len(_sku_parts(sku0)) >= 3:
                continue
            if sku0 in erp_parent_skus:
                continue
            shipping_skeleton["simples"].setdefault(sku0, DEFAULT_SHIP.copy())

    # ---------------------------
    # Delete detection (preview)
    # ---------------------------
    if dry_run:
        woo_simple_prods = [p for p in (wc_products or []) if (p.get("type") or "").lower() == "simple" and p.get("sku")]
        woo_variable_parents = [p for p in (wc_products or []) if (p.get("type") or "").lower() == "variable" and p.get("sku")]

        def _allowed_delete(sku: str) -> bool:
            parts = _sku_parts(sku)
            return bool(parts and parts[0] in erp_prefixes)

        for p in woo_simple_prods:
            sku = p.get("sku")
            if sku and _allowed_delete(sku) and (sku not in erp_simple_skus):
                report["to_delete"].append({
                    "sku": sku, "name": p.get("name") or sku,
                    "woo": {"id": p.get("id"), "status": p.get("status")},
                    "reason": "not_in_erp",
                })

        erp_parent_set = set(erp_parent_skus)
        for p in woo_variable_parents:
            parent_sku = p.get("sku")
            if not parent_sku:
                continue
            if not _allowed_delete(parent_sku):
                continue
            if parent_sku not in erp_parent_set:
                report["variant_parents_to_delete"].append({
                    "sku": parent_sku, "name": p.get("name") or parent_sku,
                    "woo": {"id": p.get("id"), "status": p.get("status")},
                    "reason": "parent_not_in_erp",
                })

        for p in woo_variable_parents:
            parent_sku = p.get("sku")
            if not parent_sku or parent_sku not in erp_variations_by_parent:
                continue
            try:
                var_map = await _get_variations_map(p["id"])
            except Exception as e:
                logger.debug(f"[DELETE PREVIEW] variations fetch failed for {parent_sku}: {e}")
                var_map = {}
            woo_var_skus = {v.get("sku") for v in var_map.values() if isinstance(v, dict) and v.get("sku")}
            missing = [s for s in (woo_var_skus or set()) if s not in erp_variations_by_parent[parent_sku]]
            for msku in missing:
                v = var_map.get(msku)
                vid = v.get("id") if isinstance(v, dict) else None
                report["variant_to_delete"].append({
                    "sku": msku, "parent_sku": parent_sku,
                    "name": (v.get("name") if isinstance(v, dict) else msku) or msku,
                    "woo": {"id": vid, "parent_id": p.get("id"), "status": (v.get("status") if isinstance(v, dict) else None)},
                    "reason": "not_in_erp",
                })

    # --- finalize/merge and write shipping_params.json on BOTH preview & real
    shipping_skeleton["generated_at"] = _now_iso()
    shipping_new = _merge_shipping_values(shipping_skeleton, shipping_existing, keep_unknown=True)
    try:
        _atomic_write_json(SHIPPING_PARAMS_PATH, shipping_new)
        logger.info("[SHIPPING] Wrote merged shipping params to %s (simples=%d, variable parents=%d)",
                    SHIPPING_PARAMS_PATH,
                    len(shipping_new.get("simples", {}) or {}),
                    len(shipping_new.get("variables", {}) or {}))
    except Exception as e:
        logger.error("[SHIPPING] Failed to write %s: %s", SHIPPING_PARAMS_PATH, e)
        report["errors"].append({"shipping_params": str(e)})

    # Persist mapping_store.json ONLY on real runs
    if not dry_run:
        try:
            existing_map = _load_json_or_empty(MAPPING_STORE_PATH)
            existing_list = existing_map.get("products") if isinstance(existing_map, dict) else []
            by_sku = { (row or {}).get("sku"): (row or {}) for row in (existing_list or []) if isinstance(row, dict) and row.get("sku") }

            for sku, m in (report.get("mapping") or {}).items():
                row = by_sku.get(sku, {})
                row["erp_item_code"] = m.get("template") or sku
                row["sku"] = sku
                if m.get("woo_product_id") is not None:
                    try: row["woo_product_id"] = int(m.get("woo_product_id"))
                    except Exception: row["woo_product_id"] = m.get("woo_product_id")
                if m.get("woo_status") is not None:
                    row["woo_status"] = m.get("woo_status")
                row["brand"] = m.get("brand")
                cats = m.get("categories") or []
                row["categories"] = ", ".join(cats) if isinstance(cats, list) else cats
                by_sku[sku] = row

            merged = {"products": [by_sku[k] for k in sorted(by_sku.keys())]}
            _atomic_write_json(MAPPING_STORE_PATH, merged)
            logger.info("📝 mapping_store.json updated: %d products (%d with Woo IDs)",
                        len(merged["products"]),
                        sum(1 for p in merged["products"] if p.get("woo_product_id")))
        except Exception as e:
            logger.error("[MAPPING_STORE] Failed to write %s: %s", MAPPING_STORE_PATH, e)
            report["errors"].append({"mapping_store": str(e)})

    def _count(key: str) -> int:
        return len(report.get(key) or [])
    report["meta"] = {
        "generated_at": _now_iso(),
        "dry_run": dry_run,
        "counts": {
            "to_create": _count("to_create"),
            "to_update": _count("to_update"),
            "to_delete": _count("to_delete"),
            "variant_to_create": _count("variant_to_create"),
            "variant_to_update": _count("variant_to_update"),
            "variant_to_delete": _count("variant_to_delete"),
            "variant_parents": _count("variant_parents"),
            "variant_parents_to_delete": _count("variant_parents_to_delete"),
            "already_synced": _count("already_synced"),
            "variant_synced": _count("variant_synced"),
            "errors": _count("errors"),
        }
    }

    return report
