# app/sync/sync.py
# ==========================================
# ERPNext → WooCommerce Sync (mapping-driven, robust, preview support)
# ==========================================

import logging
import asyncio
from collections import defaultdict

from app.erpnext import (
    get_erpnext_items,
    get_erp_images,
    get_price_map,
    get_stock_map,
)
from app.woocommerce import (
    get_wc_products,
    create_wc_product,
    update_wc_product,
    get_wc_categories,
    ensure_wp_image_uploaded,
    get_wc_variations,
)
from app.sync_utils import (
    normalize_category_name,
    build_wc_cat_map,
    parse_variant_attributes,
    is_variant_row,
    get_variant_parent_code,
    get_erp_image_list,
    ensure_all_erp_attributes_exist_global,
    ensure_all_erp_brands_exist,
    assign_brand_to_product,
    sync_categories,
    get_variant_gallery_images,
    save_preview_to_file,
    load_preview_from_file,
    diff_fields,
    sync_products_filtered,
)
from app.mapping_store import build_product_mapping, save_mapping_file
from app.config import settings
from app.field_mapping import map_erp_to_wc_product, get_wc_sync_fields

IMAGE_FETCH_CONCURRENCY = 3
image_fetch_semaphore = asyncio.Semaphore(IMAGE_FETCH_CONCURRENCY)
ERP_URL = settings.ERP_URL
logger = logging.getLogger("uvicorn.error")

# ---- PRODUCT SYNC ----

async def sync_products(dry_run=False):
    erp_items = await get_erpnext_items()
    erp_items = [item for item in erp_items if item.get("has_variants") == 0]
    wc_products = await get_wc_products()
    await sync_categories()
    wc_categories = await get_wc_categories()
    price_map = await get_price_map()
    stock_map = await get_stock_map()
    wc_cat_id_by_name = build_wc_cat_map(wc_categories)
    wc_map = {prod.get("sku"): prod for prod in wc_products if prod.get("sku")}
    brand_id_map = await ensure_all_erp_brands_exist(erp_items)
    attr_id_map, attr_term_id_map = await ensure_all_erp_attributes_exist_global()

    parent_groups = defaultdict(list)
    simple_products = []

    # --- Split simple vs variant ---
    for item in erp_items:
        item_code = item.get("item_code") or item.get("Item Code")
        item_group = item.get("item_group") or item.get("Item Group")
        cat_name = normalize_category_name(item_group)
        if is_variant_row(item):
            parent_code = get_variant_parent_code(item)
            if parent_code:
                parent_groups[(cat_name, parent_code)].append(item)
            else:
                simple_products.append(item)
        elif any(parse_variant_attributes(i) for i in erp_items if get_variant_parent_code(i) == item_code):
            parent_groups[(cat_name, item_code)].append(item)
        else:
            simple_products.append(item)

    results_create = []
    results_update = []
    stats = {"created": 0, "updated": 0, "skipped": 0, "variants_created": 0, "variants_updated": 0, "errors": []}

    # --- 1. Sync Simple Products ---
    for item in simple_products:
        sku = item.get("item_code") or item.get("Item Code")
        wc = wc_map.get(sku)
        price = price_map.get(sku, item.get("standard_rate", 0))
        default_wh = item.get("default_warehouse")
        stock_qty = (
            stock_map.get((sku, default_wh), 0)
            if sku and default_wh
            else sum(qty for (code, wh), qty in stock_map.items() if code == sku)
        )

        # 1. Get all ERPNext image URLs (main + gallery)
        erp_imgs = await get_erp_image_list(item, get_erp_images)
        if item.get("image") and item.get("image") not in erp_imgs:
            erp_imgs = [item.get("image")] + erp_imgs

        # 2. Upload to WP and collect media IDs
        img_payloads = []
        for erp_img_url in erp_imgs:
            filename = erp_img_url.split("/")[-1]
            if not erp_img_url.startswith(("http://", "https://")):
                erp_img_url = ERP_URL.rstrip("/") + erp_img_url
            async with image_fetch_semaphore:
                media_id = await ensure_wp_image_uploaded(erp_img_url, filename)
            if media_id:
                img_payloads.append({"id": media_id})
        deduped_imgs = []
        seen = set()
        for img in img_payloads:
            if img["id"] not in seen:
                deduped_imgs.append(img)
                seen.add(img["id"])

        # Build Woo payload
        wc_payload = map_erp_to_wc_product(
            item, category_map=wc_cat_id_by_name, brand_map=brand_id_map, image_list=deduped_imgs
        )
        wc_payload["regular_price"] = str(price)
        wc_payload["stock_quantity"] = stock_qty

        product_id = None
        if wc is None:
            logger.info(f"🟢 Creating simple product: {sku}")
            if not dry_run:
                resp = await create_wc_product(wc_payload)
                product_id = resp.get("data", {}).get("id") or resp.get("id")
            stats["created"] += 1
            results_create.append(sku)
        else:
            product_id = wc.get("id")
            changed = bool(diff_fields(wc, wc_payload, include=get_wc_sync_fields()))
            if changed:
                logger.info(f"🟠 Updating simple product: {sku}")
                if not dry_run:
                    await update_wc_product(wc["id"], wc_payload)
                stats["updated"] += 1
                results_update.append(sku)
            else:
                stats["skipped"] += 1
        brand = item.get("brand") or item.get("Brand")
        brand_id = brand_id_map.get(brand)
        if brand_id and product_id:
            await assign_brand_to_product(product_id, brand_id)

    # --- 2. Sync Variable (Variant) Products ---
    for (cat_name, parent_code), items in parent_groups.items():
        parent_item = None
        variants = []
        for i in items:
            if not get_variant_parent_code(i):
                parent_item = i
            else:
                variants.append(i)
        if not parent_item and variants:
            parent_item = variants[0]
        parent_sku = parent_item.get("item_code")
        wc = wc_map.get(parent_sku)
        price = price_map.get(parent_sku, parent_item.get("standard_rate", 0))
        default_wh = parent_item.get("default_warehouse")
        stock_qty = (
            stock_map.get((parent_sku, default_wh), 0)
            if parent_sku and default_wh
            else sum(qty for (code, wh), qty in stock_map.items() if code == parent_sku)
        )
        parent_imgs = await get_erp_image_list(parent_item, get_erp_images)
        if parent_item.get("image") and parent_item.get("image") not in parent_imgs:
            parent_imgs = [parent_item.get("image")] + parent_imgs
        img_payloads = []
        for erp_img_url in parent_imgs:
            filename = erp_img_url.split("/")[-1]
            async with image_fetch_semaphore:
                media_id = await ensure_wp_image_uploaded(erp_img_url, filename)
            if media_id:
                img_payloads.append({"id": media_id})
        deduped_imgs = []
        seen = set()
        for img in img_payloads:
            if img["id"] not in seen:
                deduped_imgs.append(img)
                seen.add(img["id"])
        wc_parent_payload = map_erp_to_wc_product(
            parent_item, category_map=wc_cat_id_by_name, brand_map=brand_id_map, image_list=deduped_imgs
        )
        wc_parent_payload["regular_price"] = str(price)
        wc_parent_payload["stock_quantity"] = stock_qty

        parent_id = None
        if wc is None:
            logger.info(f"🟢 Creating variable product: {parent_sku}")
            if not dry_run:
                parent_resp = await create_wc_product(wc_parent_payload)
                parent_id = parent_resp.get("data", {}).get("id") or parent_resp.get("id")
            stats["created"] += 1
        else:
            parent_id = wc.get("id")
            changed = bool(diff_fields(wc, wc_parent_payload, include=get_wc_sync_fields()))
            if changed:
                logger.info(f"🟠 Updating variable product: {parent_sku}")
                if not dry_run:
                    await update_wc_product(parent_id, wc_parent_payload)
                stats["updated"] += 1
            else:
                stats["skipped"] += 1
        brand = parent_item.get("brand") or parent_item.get("Brand")
        brand_id = brand_id_map.get(brand)
        if brand_id and parent_id:
            await assign_brand_to_product(parent_id, brand_id)
        # Variants
        woo_variations = await get_wc_variations(parent_id) if parent_id else []
        woo_variant_map = {v.get("sku"): v for v in woo_variations}
        for v in variants:
            v_sku = v.get("item_code")
            price = price_map.get(v_sku, v.get("standard_rate", 0))
            var_default_wh = v.get("default_warehouse") or default_wh
            var_stock_qty = (
                stock_map.get((v_sku, var_default_wh), 0)
                if v_sku and var_default_wh
                else sum(qty for (code, wh), qty in stock_map.items() if code == v_sku)
            )
            var_imgs = await get_variant_gallery_images(v, parent_item, get_erp_images)
            img_payloads = []
            for erp_img_url in var_imgs:
                filename = erp_img_url.split("/")[-1]
                async with image_fetch_semaphore:
                    media_id = await ensure_wp_image_uploaded(erp_img_url, filename)
                if media_id:
                    img_payloads.append({"id": media_id})
            deduped_imgs = []
            seen = set()
            for img in img_payloads:
                if img["id"] not in seen:
                    deduped_imgs.append(img)
                    seen.add(img["id"])
            var_payload = map_erp_to_wc_product(
                v, category_map=wc_cat_id_by_name, brand_map=brand_id_map,
                image_list=deduped_imgs, is_variant=True, parent_item=parent_item
            )
            var_payload["regular_price"] = str(price)
            var_payload["stock_quantity"] = var_stock_qty
            # TODO: Create/update Woo variations as needed

    # --- Write mapping file (overwrite each time) ---
    try:
        mapping = build_product_mapping(erp_items, wc_products)
        save_mapping_file(mapping)
        logger.info("Product mapping file written to mapping_store.json")
    except Exception as e:
        logger.error(f"Failed to write product mapping file: {e}")
    logger.info(
        f"✅ Product sync complete. Created: {stats['created']} Updated: {stats['updated']} "
        f"Variants: {stats['variants_created']} Errors: {len(stats['errors'])}"
    )
    return stats

# ---- PREVIEW SYNC ----

async def sync_products_preview():
    from app.sync_utils import get_image_size_with_fallback, get_image_size

    def filter_user_diff_fields(fields_changed):
        # Remove all fields that are not relevant to the user as "fields to update"
        for unwanted in ("images", "erp_img_sizes", "wc_img_sizes", "image_diff", "has_variants"):
            fields_changed.pop(unwanted, None)
        return fields_changed

    erp_items = await get_erpnext_items()
    erp_items = [item for item in erp_items if item.get("has_variants") == 0]
    wc_products = await get_wc_products()
    wc_categories = await get_wc_categories()
    price_map = await get_price_map()
    stock_map = await get_stock_map()
    wc_cat_id_by_name = build_wc_cat_map(wc_categories)
    wc_map = {prod.get("sku"): prod for prod in wc_products if prod.get("sku")}
    brand_id_map = await ensure_all_erp_brands_exist(erp_items)

    # --- Category/brand helpers ---
    cat_id_to_name = {c['id']: c['name'] for c in wc_categories if 'id' in c and 'name' in c}
    def category_names_from_wc_product(wc_product):
        return set(
            (cat_id_to_name.get(cat['id'], cat.get('name', '')).strip().lower())
            for cat in (wc_product.get('categories') or [])
        )
    def brand_names_from_wc_product(wc_product):
        brands = wc_product.get("brands", [])
        if isinstance(brands, list):
            return set((b.get("name") or str(b.get("id"))).strip().lower() for b in brands)
        return set()

    parent_codes = set()
    for item in erp_items:
        if is_variant_row(item):
            pc = get_variant_parent_code(item)
            if pc:
                parent_codes.add(pc)

    parent_groups = defaultdict(list)
    simple_products = []
    for item in erp_items:
        item_code = item.get("item_code") or item.get("Item Code")
        item_group = item.get("item_group") or item.get("Item Group")
        cat_name = normalize_category_name(item_group)
        if is_variant_row(item):
            parent_code = get_variant_parent_code(item)
            if parent_code:
                parent_groups[(cat_name, parent_code)].append(item)
            else:
                simple_products.append(item)
        elif item_code in parent_codes:
            parent_groups[(cat_name, item_code)].append(item)
        else:
            simple_products.append(item)

    preview = {
        "to_create": [],
        "to_update": [],
        "already_synced": [],
        "variant_parents": [],
        "variant_to_create": [],
        "variant_to_update": [],
        "variant_synced": [],
    }

    # --- Simple Products ---
    for item in simple_products:
        sku = item.get("item_code") or item.get("Item Code")
        wc = wc_map.get(sku)
        price_to_use = price_map.get(sku, item.get("standard_rate", 0))
        default_wh = item.get("default_warehouse")
        stock_qty = (
            stock_map.get((sku, default_wh), 0)
            if sku and default_wh
            else sum(qty for (code, wh), qty in stock_map.items() if code == sku)
        )

        wc_payload = map_erp_to_wc_product(
            item, category_map=wc_cat_id_by_name, brand_map=brand_id_map, image_list=None
        )
        wc_payload["regular_price"] = str(price_to_use)
        wc_payload["stock_quantity"] = stock_qty

        erp_imgs = await get_erp_image_list(item, get_erp_images)
        if item.get("image") and item.get("image") not in erp_imgs:
            erp_imgs = [item.get("image")] + erp_imgs
        deduped_imgs = list(dict.fromkeys([
            img if img.startswith("http") else f"{ERP_URL.rstrip('/')}{img}"
            for img in erp_imgs
        ]))
        erp_img_sizes = []
        for url in deduped_imgs:
            sz, _, _ = await get_image_size_with_fallback(url)
            if sz: erp_img_sizes.append(sz)
        wc_img_sizes = []
        if wc and wc.get("images"):
            for img in wc["images"]:
                sz = await get_image_size(img.get("src"))
                if sz: wc_img_sizes.append(sz)
        wc_payload["erp_img_sizes"] = erp_img_sizes
        wc_payload["wc_img_sizes"] = wc_img_sizes
        wc_payload["image_diff"] = set(erp_img_sizes) != set(wc_img_sizes)

        if wc is None:
            preview["to_create"].append({**wc_payload, "action": "Create", "fields_to_update": "ALL"})
        else:
            fields_changed = diff_fields(
                wc, wc_payload,
                include=get_wc_sync_fields(),
                ignore={"erp_img_sizes", "wc_img_sizes", "image_diff", "images", "has_variants"},
            )

            # --- PATCH: Remove false category/brand diffs ---
            wc_cat_names = category_names_from_wc_product(wc)
            erp_cat_name = (item.get("item_group") or item.get("Item Group") or "").strip().lower()
            if "categories" in fields_changed and erp_cat_name in wc_cat_names:
                fields_changed.pop("categories")

            wc_brand_names = brand_names_from_wc_product(wc)
            erp_brand_name = (item.get("brand") or item.get("Brand") or "").strip().lower()
            if "brands" in fields_changed and erp_brand_name in wc_brand_names:
                fields_changed.pop("brands")

            fields_changed = filter_user_diff_fields(fields_changed)
            changed = bool(fields_changed) or wc_payload["image_diff"]
            if changed:
                preview["to_update"].append({
                    "current": wc,
                    "new": wc_payload,
                    "fields_changed": list(fields_changed.keys()),
                    "fields_diff": fields_changed,
                    "fields_to_update": ", ".join(fields_changed.keys()),
                    "action": "Update",
                    "image_diff": wc_payload["image_diff"],
                })
            else:
                preview["already_synced"].append({
                    "current": wc, "action": "No Change", "fields_to_update": "", "image_diff": wc_payload["image_diff"]
                })

    # --- Variant Parents and Children ---
    from app.woocommerce import get_wc_variations

    for (cat_name, parent_code), items in parent_groups.items():
        parent_item = None
        variants = []
        for i in items:
            if not get_variant_parent_code(i):
                parent_item = i
            else:
                variants.append(i)
        if not parent_item and variants:
            parent_item = variants[0]
        parent_sku = parent_item.get("item_code")
        wc = wc_map.get(parent_sku)
        parent_default_wh = parent_item.get("default_warehouse")
        stock_qty = (
            stock_map.get((parent_sku, parent_default_wh), 0)
            if parent_sku and parent_default_wh
            else sum(qty for (code, wh), qty in stock_map.items() if code == parent_sku)
        )

        wc_parent_payload = map_erp_to_wc_product(
            parent_item, category_map=wc_cat_id_by_name, brand_map=brand_id_map, image_list=None
        )
        wc_parent_payload["stock_quantity"] = stock_qty

        parent_imgs = await get_erp_image_list(parent_item, get_erp_images)
        if parent_item.get("image") and parent_item.get("image") not in parent_imgs:
            parent_imgs = [parent_item.get("image")] + parent_imgs
        deduped_imgs = list(dict.fromkeys([
            img if img.startswith("http") else f"{ERP_URL.rstrip('/')}{img}"
            for img in parent_imgs
        ]))
        erp_parent_img_sizes = []
        for url in deduped_imgs:
            sz, _, _ = await get_image_size_with_fallback(url)
            if sz: erp_parent_img_sizes.append(sz)
        wc_img_sizes = []
        if wc and wc.get("images"):
            for img in wc["images"]:
                sz = await get_image_size(img.get("src"))
                if sz: wc_img_sizes.append(sz)
        wc_parent_payload["erp_img_sizes"] = erp_parent_img_sizes
        wc_parent_payload["wc_img_sizes"] = wc_img_sizes
        wc_parent_payload["image_diff"] = set(erp_parent_img_sizes) != set(wc_img_sizes)

        if wc is None:
            preview["variant_parents"].append({
                "new": wc_parent_payload,
                "status": "to_create",
                "action": "Create",
                "fields_to_update": "ALL"
            })
        else:
            fields_changed = diff_fields(
                wc, wc_parent_payload,
                include=get_wc_sync_fields(),
                ignore={"erp_img_sizes", "wc_img_sizes", "image_diff", "images", "has_variants"},
            )

            # --- PATCH for variant parent ---
            wc_cat_names = category_names_from_wc_product(wc)
            erp_cat_name = (parent_item.get("item_group") or parent_item.get("Item Group") or "").strip().lower()
            if "categories" in fields_changed and erp_cat_name in wc_cat_names:
                fields_changed.pop("categories")

            wc_brand_names = brand_names_from_wc_product(wc)
            erp_brand_name = (parent_item.get("brand") or parent_item.get("Brand") or "").strip().lower()
            if "brands" in fields_changed and erp_brand_name in wc_brand_names:
                fields_changed.pop("brands")

            fields_changed = filter_user_diff_fields(fields_changed)
            changed = bool(fields_changed) or wc_parent_payload["image_diff"]
            if changed:
                preview["variant_parents"].append({
                    "current": wc,
                    "new": wc_parent_payload,
                    "fields_changed": list(fields_changed.keys()),
                    "fields_diff": fields_changed,
                    "fields_to_update": ", ".join(fields_changed.keys()),
                    "action": "Update",
                    "status": "to_update",
                    "image_diff": wc_parent_payload["image_diff"]
                })
            else:
                preview["variant_parents"].append({
                    "current": wc,
                    "action": "No Change",
                    "fields_to_update": "",
                    "status": "already_synced",
                    "image_diff": wc_parent_payload["image_diff"]
                })

        # --- Variant-level preview (each variant child) ---
        woo_variants = []
        woo_variant_map = {}
        if wc is not None:
            try:
                woo_variants = await get_wc_variations(wc["id"])
                woo_variant_map = {v.get("sku"): v for v in woo_variants}
            except Exception as ex:
                logger.warning(f"Failed to fetch WC variations for parent {parent_sku}: {ex}")

        for v in variants:
            v_sku = v.get("item_code")
            price = price_map.get(v_sku, v.get("standard_rate", 0))
            var_default_wh = v.get("default_warehouse") or parent_default_wh
            var_stock_qty = (
                stock_map.get((v_sku, var_default_wh), 0)
                if v_sku and var_default_wh
                else sum(qty for (code, wh), qty in stock_map.items() if code == v_sku)
            )

            var_payload = map_erp_to_wc_product(
                v,
                category_map=wc_cat_id_by_name,
                brand_map=brand_id_map,
                image_list=None,
                is_variant=True,
                parent_item=parent_item,
            )
            var_payload["regular_price"] = str(price)
            var_payload["stock_quantity"] = var_stock_qty

            var_imgs = await get_variant_gallery_images(v, parent_item, get_erp_images)
            deduped_var_imgs = list(dict.fromkeys([
                img if img.startswith("http") else f"{ERP_URL.rstrip('/')}{img}"
                for img in var_imgs
            ]))
            erp_var_img_sizes = []
            for url in deduped_var_imgs:
                sz, _, _ = await get_image_size_with_fallback(url)
                if sz: erp_var_img_sizes.append(sz)
            wc_var_img_sizes = []
            wc_variant = woo_variant_map.get(v_sku) if woo_variant_map else None
            if wc_variant and wc_variant.get("images"):
                for img in wc_variant["images"]:
                    sz = await get_image_size(img.get("src"))
                    if sz: wc_var_img_sizes.append(sz)
            var_payload["erp_img_sizes"] = erp_var_img_sizes
            var_payload["wc_img_sizes"] = wc_var_img_sizes
            var_payload["image_diff"] = set(erp_var_img_sizes) != set(wc_var_img_sizes)

            if wc_variant is None:
                preview["variant_to_create"].append({**var_payload, "action": "Create", "fields_to_update": "ALL"})
            else:
                fields_changed = diff_fields(
                    wc_variant, var_payload,
                    include=get_wc_sync_fields(),
                    ignore={"erp_img_sizes", "wc_img_sizes", "image_diff", "images", "has_variants"},
                )
                wc_brand_names = brand_names_from_wc_product(wc_variant)
                erp_brand_name = (v.get("brand") or v.get("Brand") or "").strip().lower()
                if "brands" in fields_changed and erp_brand_name in wc_brand_names:
                    fields_changed.pop("brands")
                wc_cat_names = category_names_from_wc_product(wc_variant)
                erp_cat_name = (v.get("item_group") or v.get("Item Group") or "").strip().lower()
                if "categories" in fields_changed and erp_cat_name in wc_cat_names:
                    fields_changed.pop("categories")
                fields_changed = filter_user_diff_fields(fields_changed)
                changed = bool(fields_changed) or var_payload["image_diff"]
                if changed:
                    preview["variant_to_update"].append({
                        "current": wc_variant,
                        "new": var_payload,
                        "fields_changed": list(fields_changed.keys()),
                        "fields_diff": fields_changed,
                        "fields_to_update": ", ".join(fields_changed.keys()),
                        "action": "Update",
                        "image_diff": var_payload["image_diff"],
                    })
                else:
                    preview["variant_synced"].append({
                        "current": wc_variant, "action": "No Change", "fields_to_update": "", "image_diff": var_payload["image_diff"]
                    })

    logger.info(f"Saving preview file with N items to update: {len(preview['to_update'])}")
    save_preview_to_file(preview)
    return preview



# ---- PARTIAL SYNC ----

async def sync_products_partial(dry_run=False, filename: str = "products_to_sync.json"):
    """
    Partial sync: sync only the products flagged in the last preview file.
    """
    preview = load_preview_from_file(filename)
    flagged_skus = set()
    for section in ("to_create", "to_update"):
        flagged_skus.update([item.get("sku") for item in preview.get(section, []) if item.get("sku")])
    logger.info(f"Partial sync on these SKUs: {flagged_skus}")

    erp_items = await get_erpnext_items()
    erp_items = [item for item in erp_items if (item.get("item_code") or item.get("Item Code")) in flagged_skus]
    wc_products = await get_wc_products()
    wc_products = [prod for prod in wc_products if prod.get("sku") in flagged_skus]
    stock_map = await get_stock_map()
    # Note: If you call sync_products_filtered here, make sure it also gets stock_map injected
    results = await sync_products_filtered(erp_items, wc_products, dry_run=dry_run)
    return results
