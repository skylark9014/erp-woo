import html
import httpx
import os
from urllib.parse import urlparse, quote

from app.erpnext import (
    get_erpnext_items,
    get_erp_images,
    get_erpnext_categories,
    get_price_map,
    ERP_URL,
    ERP_API_KEY,
    ERP_API_SECRET,
)
from app.woocommerce import (
    get_wc_products,
    create_wc_product,
    update_wc_product,
    get_wc_categories,
    create_wc_category,
    upload_wc_image_from_erpnext,
)

logger = __import__('logging').getLogger("uvicorn.error")


def normalize_category_name(name):
    if not name:
        return ""
    name = html.unescape(name)
    name = name.replace('\xa0', ' ')
    return name.strip().lower()

def build_wc_cat_map(wc_categories):
    return {normalize_category_name(cat["name"]): cat["id"] for cat in wc_categories}

def normalize_woo_image_url(src):
    base = os.getenv("WC_BASE_URL", "").rstrip("/")
    parsed = urlparse(src)
    return base + parsed.path

async def get_image_size_with_fallback(erp_url):
    """
    Try to get image size from ERPNext (HEAD request to public or private /files/ URLs).
    Always URL-encodes the path and sends authentication.
    Returns (size:int, full_url:str, headers:dict)
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

    # Only log if comparison will happen
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.head(url, headers=headers)
            if resp.status_code == 200 and "content-length" in resp.headers:
                return int(resp.headers["content-length"]), url, headers
    except Exception as e:
        logger.warning(f"[ERP IMG FETCH] Exception: {e} for {url}")

    return None, None, None


async def get_image_size(url, headers=None):
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

# --- Product Sync ---

async def sync_products():
    erp_items = await get_erpnext_items()
    wc_products = await get_wc_products()
    wc_categories = await get_wc_categories()
    price_map = await get_price_map()
    wc_cat_id_by_name = build_wc_cat_map(wc_categories)

    erp_map = {item["item_code"]: item for item in erp_items}
    wc_map = {prod.get("sku"): prod for prod in wc_products if prod.get("sku")}

    to_create = []
    to_update = []
    results_create = []
    results_update = []

    for code, item in erp_map.items():
        wc = wc_map.get(code)
        erp_cat_name_raw = item.get("item_group", "")
        erp_cat_name = normalize_category_name(erp_cat_name_raw)
        cat_id = wc_cat_id_by_name.get(erp_cat_name)
        price = price_map.get(item["item_code"])
        price_to_use = price if price is not None else item.get("standard_rate", 0)
        wc_payload = {
            "name": item["item_name"],
            "regular_price": str(price_to_use),
            "sku": item["item_code"],
            "description": item.get("description", ""),
            "categories": [{"id": cat_id}] if cat_id else [],
        }
        if wc is None:
            to_create.append(item)
        else:
            changed = (
                wc.get("name") != item["item_name"] or
                str(wc.get("regular_price", "")) != str(price_to_use) or
                wc.get("description", "") != item.get("description", "") or
                (cat_id and [c["id"] for c in wc.get("categories", [])] != [cat_id])
            )
            if changed:
                to_update.append((wc["id"], wc_payload))

    for item in to_create:
        erp_cat_name_raw = item.get("item_group", "")
        erp_cat_name = normalize_category_name(erp_cat_name_raw)
        cat_id = wc_cat_id_by_name.get(erp_cat_name)
        price = price_map.get(item["item_code"])
        price_to_use = price if price is not None else item.get("standard_rate", 0)
        payload = {
            "name": item["item_name"],
            "type": "simple",
            "regular_price": str(price_to_use),
            "sku": item["item_code"],
            "description": item.get("description", ""),
            "manage_stock": False,
            "categories": [{"id": cat_id}] if cat_id else [],
        }
        result = await create_wc_product(payload)
        results_create.append({
            "item_code": item["item_code"],
            "status": result.get("status_code"),
            "response": result.get("data"),
        })

    for product_id, wc_payload in to_update:
        result = await update_wc_product(product_id, wc_payload)
        results_update.append({
            "product_id": product_id,
            "status": result.get("status_code"),
            "response": result.get("data"),
        })

    return {
        "created": results_create,
        "updated": results_update,
        "count_created": len(results_create),
        "count_updated": len(results_update),
        "skipped": len(erp_items) - len(to_create) - len(to_update)
    }

# --- Sync Preview (unchanged) ---

async def sync_products_preview():
    erp_items = await get_erpnext_items()
    wc_products = await get_wc_products()
    wc_categories = await get_wc_categories()
    price_map = await get_price_map()
    wc_cat_id_by_name = build_wc_cat_map(wc_categories)

    erp_map = {item["item_code"]: item for item in erp_items}
    wc_map = {prod.get("sku"): prod for prod in wc_products if prod.get("sku")}

    to_create = []
    to_update = []
    already_synced = []

    for code, item in erp_map.items():
        wc = wc_map.get(code)
        erp_cat_name_raw = item.get("item_group", "")
        erp_cat_name = normalize_category_name(erp_cat_name_raw)
        cat_id = wc_cat_id_by_name.get(erp_cat_name)
        price = price_map.get(item["item_code"])
        price_to_use = price if price is not None else item.get("standard_rate", 0)
        if wc is None:
            to_create.append(item)
        else:
            changed = (
                wc.get("name") != item["item_name"] or
                str(wc.get("regular_price", "")) != str(price_to_use) or
                wc.get("description", "") != item.get("description", "") or
                (cat_id and [c["id"] for c in wc.get("categories", [])] != [cat_id])
            )
            if changed:
                to_update.append({"erp": item, "wc": wc})
            else:
                already_synced.append({"erp": item, "wc": wc})

    return {
        "to_create": to_create,
        "to_update": to_update,
        "already_synced": already_synced,
        "erp_count": len(erp_items),
        "wc_count": len(wc_products),
    }

# --- Category Sync (unchanged) ---

async def sync_categories():
    erp_cats = await get_erpnext_categories()
    wc_cats = await get_wc_categories()
    wc_cat_map = {normalize_category_name(cat["name"]): cat for cat in wc_cats}
    created = []

    for erp_cat in erp_cats:
        name = erp_cat["name"]
        name_normalized = normalize_category_name(name)
        if name_normalized not in wc_cat_map:
            resp = await create_wc_category(name)
            if resp.get("code") == "term_exists":
                wc_cats = await get_wc_categories()
                wc_cat_map = {normalize_category_name(cat["name"]): cat for cat in wc_cats}
            created.append({"erp_category": name, "wc_response": resp})

    return {
        "created": created,
        "total_erp_categories": len(erp_cats),
        "total_wc_categories": len(wc_cats)
    }

# --- Image Sync ---

async def sync_product_images():
    erp_items = await get_erpnext_items()
    wc_products = await get_wc_products()
    wc_map = {prod.get("sku"): prod for prod in wc_products if prod.get("sku")}

    results = []

    for item in erp_items:
        erp_imgs = list(dict.fromkeys(await get_erp_images(item)))
        sku = item["item_code"]

        if not erp_imgs:
            results.append({
                "sku": sku,
                "synced": False,
                "reason": "No ERPNext images for product"
            })
            continue

        wc = wc_map.get(sku)
        if not wc:
            results.append({
                "sku": sku,
                "synced": False,
                "reason": "Product missing in WooCommerce"
            })
            continue

        woo_images = wc.get("images", [])
        woo_size_to_id = {}
        for img in woo_images:
            img_url = img.get("src")
            if img_url:
                size = await get_image_size(img_url)
                if size:
                    woo_size_to_id.setdefault(size, []).append(img.get("id"))

        # Map: erp_img_url → (size, url, headers)
        erp_size_url = []
        for erp_url in erp_imgs:
            erp_size, download_url, headers = await get_image_size_with_fallback(erp_url)
            erp_size_url.append((erp_size, download_url, erp_url, headers))

        # Compare ERP/Woo image sizes
        erp_sizes = [sz for sz, _, _, _ in erp_size_url if sz]
        woo_sizes = list(woo_size_to_id.keys())
        logger.info(f"[IMG SYNC] {sku}: ERP sizes {erp_sizes}, Woo sizes {woo_sizes}")

        new_img_ids = []
        any_uploaded = False
        failed_uploads = []

        for erp_size, download_url, orig_erp_url, headers in erp_size_url:
            img_id = None
            if erp_size and erp_size in woo_size_to_id and woo_size_to_id[erp_size]:
                img_id = woo_size_to_id[erp_size].pop(0)
            elif erp_size and download_url:
                filename = orig_erp_url.split("/")[-1]
                upload_resp = await upload_wc_image_from_erpnext(
                    download_url, filename, ERP_API_KEY, ERP_API_SECRET
                )
                # Always log failures
                if not upload_resp or not upload_resp.get("id"):
                    logger.error(f"[IMG UPLOAD ERROR] Upload failed for {filename}: {upload_resp}")
                    failed_uploads.append({
                        "erp_url": orig_erp_url,
                        "reason": upload_resp,
                        "download_url": download_url
                    })
                else:
                    img_id = upload_resp["id"]
            if img_id:
                new_img_ids.append(img_id)

        # If nothing matched or uploaded, leave unchanged and show what we tried
        if not new_img_ids:
            results.append({
                "sku": sku,
                "updated_gallery": False,
                "media_ids": [],
                "status": "no_images_uploaded_or_matched",
                "failed_uploads": failed_uploads
            })
            continue

        # Only update Woo if the gallery is actually different!
        existing_img_ids = [img.get("id") for img in woo_images]
        if new_img_ids and set(new_img_ids) != set(existing_img_ids):
            images_payload = [{"id": img_id} for img_id in new_img_ids]
            update_resp = await update_wc_product(wc["id"], {"images": images_payload})
            status = update_resp.get("status_code")
            results.append({
                "sku": sku,
                "updated_gallery": True,
                "media_ids": new_img_ids,
                "status": status,
                "failed_uploads": failed_uploads
            })
        else:
            results.append({
                "sku": sku,
                "updated_gallery": False,
                "media_ids": new_img_ids,
                "status": "no_change_needed",
                "failed_uploads": failed_uploads
            })

    return {"synced_images": results, "count": len(results)}
