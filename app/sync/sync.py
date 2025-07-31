# app/sync.py
# ==========================================
# ERPNext → WooCommerce Sync (With robust brand and global attribute sync, + mapping file)
# ==========================================

import logging
from collections import defaultdict

from app.erpnext import (
    get_erpnext_items,
    get_erp_images,
    get_erpnext_categories,
    get_price_map,
)
from app.woocommerce import (
    get_wc_products,
    create_wc_product,
    update_wc_product,
    get_wc_categories,
    create_wc_category,
    ensure_wp_image_uploaded,
    get_wc_variations,
    set_wc_variant_image,
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
)
from app.mapping.mapping_store import build_product_mapping, save_mapping_file

logger = logging.getLogger("uvicorn.error")

# --- MAIN SYNC FUNCTION ---

async def sync_products(dry_run=False):
    erp_items = await get_erpnext_items()
    erp_items = [item for item in erp_items if not (item.get("has_variants") == 1 or item.get("is_template") is True)]
    wc_products = await get_wc_products()
    await sync_categories()
    wc_categories = await get_wc_categories()
    price_map = await get_price_map()
    wc_cat_id_by_name = build_wc_cat_map(wc_categories)
    wc_map = {prod.get("sku"): prod for prod in wc_products if prod.get("sku")}

    brand_id_map = await ensure_all_erp_brands_exist(erp_items)
    attr_id_map, attr_term_id_map = await ensure_all_erp_attributes_exist_global()

    parent_groups = defaultdict(list)
    simple_products = []
    parent_map = {}

    for item in erp_items:
        item_group = item.get("item_group") or item.get("Item Group")
        erp_cat_name = normalize_category_name(item_group)
        parent_code = get_variant_parent_code(item)
        if is_variant_row(item):
            if parent_code:
                parent_groups[(erp_cat_name, parent_code)].append(item)
                parent_map[parent_code] = item.get("item_name") or item.get("Item Name")
            else:
                simple_products.append(item)
        elif any(parse_variant_attributes(i) for i in erp_items if get_variant_parent_code(i) == item.get("item_code")):
            parent_groups[(erp_cat_name, item.get("item_code"))].append(item)
            parent_map[item.get("item_code")] = item.get("item_name") or item.get("Item Name")
        else:
            simple_products.append(item)

    results_create = []
    results_update = []
    stats = {"created": 0, "updated": 0, "skipped": 0, "variants_created": 0, "variants_updated": 0, "errors": []}

    # --- 2. Sync Simple Products (No Variants) ---
    for item in simple_products:
        sku = item.get("item_code") or item.get("Item Code")
        name = item.get("item_name") or item.get("Item Name")
        item_group = item.get("item_group") or item.get("Item Group")
        erp_cat_name = normalize_category_name(item_group)
        wc_cat_id = wc_cat_id_by_name.get(erp_cat_name)
        price = price_map.get(sku)
        price_to_use = price if price is not None else item.get("standard_rate", 0)

        attrs_for_this_product = parse_variant_attributes(item)
        wc_attributes = []
        for attr, value in attrs_for_this_product.items():
            attr_id = attr_id_map.get(attr)
            if attr_id:
                wc_attributes.append({
                    "id": attr_id,
                    "name": attr,
                    "option": value,
                    "visible": True,
                    "variation": True
                })
        wc_payload = {
            "name": name,
            "sku": sku,
            "type": "simple",
            "categories": [{"id": wc_cat_id}] if wc_cat_id else [],
            "description": item.get("description", "") or item.get("Description", ""),
            "manage_stock": True,
            "stock_quantity": item.get("opening_stock", 0) or item.get("Opening Stock", 0),
            "regular_price": str(price_to_use),
        }
        if wc_attributes:
            wc_payload["attributes"] = wc_attributes

        brand = item.get("brand") or item.get("Brand")
        brand_id = brand_id_map.get(brand)
        if brand and not brand_id:
            logger.error(f"Brand '{brand}' is missing in Woo and could not be created!")

        # Images: featured + gallery (same logic as before for simple)
        erp_imgs = await get_erp_image_list(item, get_erp_images)
        if item.get("image") and item.get("image") not in erp_imgs:
            erp_imgs = [item.get("image")] + erp_imgs
        img_payloads = []
        for erp_img_url in erp_imgs:
            filename = erp_img_url.split("/")[-1]
            media_id = await ensure_wp_image_uploaded(erp_img_url, filename)
            if media_id:
                img_payloads.append({"id": media_id})
        if img_payloads:
            wc_payload["images"] = img_payloads

        wc = wc_map.get(sku)
        product_id = None
        if wc is None:
            logger.info(f"🟢 Creating simple product: {name} [{sku}]")
            if not dry_run:
                resp = await create_wc_product(wc_payload)
                product_id = resp.get("data", {}).get("id") or resp.get("id")
            stats["created"] += 1
            results_create.append(sku)
        else:
            product_id = wc.get("id")
            changed = (
                wc.get("name") != name or
                str(wc.get("regular_price", "")) != str(price_to_use) or
                wc.get("description", "") != wc_payload["description"] or
                (wc_cat_id and [c["id"] for c in wc.get("categories", [])] != [wc_cat_id])
            )
            if changed:
                logger.info(f"🟠 Updating simple product: {name} [{sku}]")
                if not dry_run:
                    await update_wc_product(wc["id"], wc_payload)
                stats["updated"] += 1
                results_update.append(sku)
            else:
                stats["skipped"] += 1

        if brand_id and product_id:
            await assign_brand_to_product(product_id, brand_id)

    # --- 3. Sync Variable (Variant) Products ---
    for (erp_cat_name, parent_code), items in parent_groups.items():
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
        parent_name = parent_item.get("item_name") or parent_item.get("Item Name")
        wc_cat_id = wc_cat_id_by_name.get(erp_cat_name)
        price = price_map.get(parent_sku)
        price_to_use = price if price is not None else parent_item.get("standard_rate", 0)

        attr_options = defaultdict(set)
        for v in variants:
            attrs = parse_variant_attributes(v)
            for attr, val in attrs.items():
                attr_options[attr].add(val)
        wc_attributes = []
        for attr, options in attr_options.items():
            attr_id = attr_id_map.get(attr)
            if attr_id:
                wc_attributes.append({
                    "id": attr_id,
                    "name": attr,
                    "visible": True,
                    "variation": True,
                    "options": sorted(list(options))
                })

        wc_parent_payload = {
            "name": parent_name,
            "sku": parent_sku,
            "type": "variable",
            "categories": [{"id": wc_cat_id}] if wc_cat_id else [],
            "attributes": wc_attributes,
            "description": parent_item.get("description", "") or parent_item.get("Description", ""),
            "regular_price": str(price_to_use),
        }

        brand = parent_item.get("brand") or parent_item.get("Brand")
        brand_id = brand_id_map.get(brand)
        if brand and not brand_id:
            logger.error(f"Brand '{brand}' is missing in Woo and could not be created!")

        logger.info(f"Variable product '{parent_name}' SKU:{parent_sku} cat_id:{wc_cat_id} price:{price_to_use} brand:{brand} (brand_id:{brand_id})")

        parent_imgs = await get_erp_image_list(parent_item, get_erp_images)
        if parent_item.get("image") and parent_item.get("image") not in parent_imgs:
            parent_imgs = [parent_item.get("image")] + parent_imgs
        img_payloads = []
        for erp_img_url in parent_imgs:
            filename = erp_img_url.split("/")[-1]
            media_id = await ensure_wp_image_uploaded(erp_img_url, filename)
            if media_id:
                img_payloads.append({"id": media_id})
        if img_payloads:
            wc_parent_payload["images"] = img_payloads

        wc = wc_map.get(parent_sku)
        parent_id = None
        if wc is None:
            logger.info(f"🟢 Creating variable product: {parent_name} [{parent_sku}]")
            if not dry_run:
                parent_resp = await create_wc_product(wc_parent_payload)
                parent_id = parent_resp.get("data", {}).get("id") or parent_resp.get("id")
            stats["created"] += 1
        else:
            parent_id = wc.get("id")
            changed = (
                wc.get("name") != parent_name or
                wc.get("description", "") != wc_parent_payload["description"] or
                (wc_cat_id and [c["id"] for c in wc.get("categories", [])] != [wc_cat_id])
            )
            if changed:
                logger.info(f"🟠 Updating variable product: {parent_name} [{parent_sku}]")
                if not dry_run:
                    await update_wc_product(parent_id, wc_parent_payload)
                stats["updated"] += 1
            else:
                stats["skipped"] += 1

        if brand_id and parent_id:
            await assign_brand_to_product(parent_id, brand_id)

        woo_variations = await get_wc_variations(parent_id) if parent_id else []
        woo_variant_map = {v.get("sku"): v for v in woo_variations}

        for v in variants:
            v_sku = v.get("item_code")
            attrs = parse_variant_attributes(v)
            price = price_map.get(v_sku)
            price_to_use = price if price is not None else v.get("standard_rate", 0)

            wc_var_attrs = []
            for attr, value in attrs.items():
                attr_id = attr_id_map.get(attr)
                if attr_id:
                    wc_var_attrs.append({
                        "id": attr_id,
                        "name": attr,
                        "option": value,
                        "visible": True,
                        "variation": True
                    })

            var_payload = {
                "sku": v_sku,
                "attributes": wc_var_attrs,
                "regular_price": str(price_to_use),
                "manage_stock": True,
                "stock_quantity": v.get("opening_stock", 0) or v.get("Opening Stock", 0),
            }
            brand = v.get("brand") or v.get("Brand")
            brand_id = brand_id_map.get(brand)
            if brand and not brand_id:
                logger.error(f"Brand '{brand}' is missing in Woo and could not be created!")

            # --- GALLERY: Item Image + own attached + template attached (no template item image) ---
            var_imgs = await get_variant_gallery_images(v, parent_item, get_erp_images)
            img_payloads = []
            for erp_img_url in var_imgs:
                filename = erp_img_url.split("/")[-1]
                media_id = await ensure_wp_image_uploaded(erp_img_url, filename)
                if media_id:
                    img_payloads.append({"id": media_id})
            if img_payloads:
                var_payload["images"] = img_payloads

            try:
                logger.info(f"🟢 Creating/updating variation: {parent_name} {attrs} SKU:{v_sku} brand:{brand} (brand_id:{brand_id})")
                if not dry_run and parent_id:
                    woo_variant = woo_variant_map.get(v_sku)
                    if woo_variant:
                        stats["variants_updated"] += 1
                        if brand_id:
                            await assign_brand_to_product(woo_variant["id"], brand_id)
                    else:
                        stats["variants_created"] += 1
            except Exception as e:
                logger.error(f"❌ Error syncing variation {v_sku}: {e}")
                stats["errors"].append(str(e))

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

async def sync_products_preview():
    from app.sync_utils import get_image_size_with_fallback, get_image_size

    erp_items = await get_erpnext_items()
    erp_items = [item for item in erp_items if not (item.get("has_variants") == 1 or item.get("is_template") is True)]
    wc_products = await get_wc_products()
    wc_categories = await get_wc_categories()
    price_map = await get_price_map()
    wc_cat_id_by_name = build_wc_cat_map(wc_categories)
    wc_map = {prod.get("sku"): prod for prod in wc_products if prod.get("sku")}

    parent_groups = defaultdict(list)
    simple_products = []
    preview = {
        "to_create": [],
        "to_update": [],
        "already_synced": [],
        "variant_parents": [],
        "variant_to_create": [],
        "variant_to_update": [],
        "variant_synced": [],
    }

    for item in erp_items:
        item_group = item.get("item_group") or item.get("Item Group")
        cat_name = normalize_category_name(item_group)
        parent_code = get_variant_parent_code(item)

        if is_variant_row(item):
            if parent_code:
                parent_groups[(cat_name, parent_code)].append(item)
            else:
                simple_products.append(item)
        elif any(parse_variant_attributes(i) for i in erp_items if get_variant_parent_code(i) == item.get("item_code")):
            parent_groups[(cat_name, item.get("item_code"))].append(item)
        else:
            simple_products.append(item)

    for item in simple_products:
        sku = item.get("item_code") or item.get("Item Code")
        name = item.get("item_name") or item.get("Item Name")
        item_group = item.get("item_group") or item.get("Item Group")
        wc_cat_id = wc_cat_id_by_name.get(normalize_category_name(item_group))
        price = price_map.get(sku)
        price_to_use = price if price is not None else item.get("standard_rate", 0)
        wc_payload = {
            "name": name,
            "sku": sku,
            "type": "simple",
            "categories": [{"id": wc_cat_id}] if wc_cat_id else [],
            "description": item.get("description", "") or item.get("Description", ""),
            "manage_stock": True,
            "stock_quantity": item.get("opening_stock", 0) or item.get("Opening Stock", 0),
            "regular_price": str(price_to_use),
        }
        wc = wc_map.get(sku)

        erp_imgs = await get_erp_image_list(item, get_erp_images)
        if item.get("image") and item.get("image") not in erp_imgs:
            erp_imgs = [item.get("image")] + erp_imgs
        erp_img_sizes = []
        for erp_img_url in erp_imgs:
            sz, _, _ = await get_image_size_with_fallback(erp_img_url)
            if sz:
                erp_img_sizes.append(sz)
        wc_img_sizes = []
        if wc and wc.get("images"):
            for img in wc.get("images", []):
                sz = await get_image_size(img.get("src"))
                if sz:
                    wc_img_sizes.append(sz)
        img_diff = set(erp_img_sizes) != set(wc_img_sizes)

        wc_payload["erp_img_sizes"] = erp_img_sizes
        wc_payload["wc_img_sizes"] = wc_img_sizes
        wc_payload["image_diff"] = img_diff

        if wc is None:
            preview["to_create"].append(wc_payload)
        else:
            changed = (
                wc.get("name") != name or
                str(wc.get("regular_price", "")) != str(price_to_use) or
                wc.get("description", "") != wc_payload["description"] or
                (wc_cat_id and [c["id"] for c in wc.get("categories", [])] != [wc_cat_id]) or
                img_diff
            )
            if changed:
                preview["to_update"].append({"current": wc, "new": wc_payload, "image_diff": img_diff})
            else:
                preview["already_synced"].append({"current": wc, "image_diff": img_diff})

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
        parent_name = parent_item.get("item_name") or parent_item.get("Item Name")
        wc_cat_id = wc_cat_id_by_name.get(cat_name)

        attr_options = defaultdict(set)
        for v in variants:
            attrs = parse_variant_attributes(v)
            for attr, val in attrs.items():
                attr_options[attr].add(val)
        wc_attributes = [
            {
                "name": attr,
                "visible": True,
                "variation": True,
                "options": sorted(list(options))
            }
            for attr, options in attr_options.items()
        ]

        wc_parent_payload = {
            "name": parent_name,
            "sku": parent_sku,
            "type": "variable",
            "categories": [{"id": wc_cat_id}] if wc_cat_id else [],
            "attributes": wc_attributes,
            "description": parent_item.get("description", "") or parent_item.get("Description", ""),
        }

        wc = wc_map.get(parent_sku)

        parent_imgs = await get_erp_image_list(parent_item, get_erp_images)
        if parent_item.get("image") and parent_item.get("image") not in parent_imgs:
            parent_imgs = [parent_item.get("image")] + parent_imgs
        erp_parent_img_sizes = []
        for erp_img_url in parent_imgs:
            sz, _, _ = await get_image_size_with_fallback(erp_img_url)
            if sz:
                erp_parent_img_sizes.append(sz)
        wc_img_sizes = []
        if wc and wc.get("images"):
            for img in wc.get("images", []):
                sz = await get_image_size(img.get("src"))
                if sz:
                    wc_img_sizes.append(sz)
        parent_img_diff = set(erp_parent_img_sizes) != set(wc_img_sizes)

        wc_parent_payload["erp_img_sizes"] = erp_parent_img_sizes
        wc_parent_payload["wc_img_sizes"] = wc_img_sizes
        wc_parent_payload["image_diff"] = parent_img_diff

        if wc is None:
            preview["variant_parents"].append({"new": wc_parent_payload, "status": "to_create"})
        else:
            changed = (
                wc.get("name") != parent_name or
                wc.get("description", "") != wc_parent_payload["description"] or
                (wc_cat_id and [c["id"] for c in wc.get("categories", [])] != [wc_cat_id]) or
                parent_img_diff
            )
            if changed:
                preview["variant_parents"].append({"current": wc, "new": wc_parent_payload, "status": "to_update", "image_diff": parent_img_diff})
            else:
                preview["variant_parents"].append({"current": wc, "status": "already_synced", "image_diff": parent_img_diff})

        for v in variants:
            v_sku = v.get("item_code")
            attrs = parse_variant_attributes(v)
            price = price_map.get(v_sku)
            price_to_use = price if price is not None else v.get("standard_rate", 0)
            var_payload = {
                "sku": v_sku,
                "attributes": [{"name": attr, "option": val} for attr, val in attrs.items()],
                "regular_price": str(price_to_use),
                "manage_stock": True,
                "stock_quantity": v.get("opening_stock", 0) or v.get("Opening Stock", 0),
            }
            # --- GALLERY for variant: own item image + own attached + template attached ---
            var_imgs = await get_variant_gallery_images(v, parent_item, get_erp_images)
            erp_var_img_sizes = []
            for erp_img_url in var_imgs:
                sz, _, _ = await get_image_size_with_fallback(erp_img_url)
                if sz:
                    erp_var_img_sizes.append(sz)
            var_payload["erp_img_sizes"] = erp_var_img_sizes
            var_payload["image_diff"] = "unknown"

            preview["variant_to_create"].append(var_payload)

    return preview
