#==========================================================================================
# # woocommerce.py
# WooCommerce API interface module.
# Functions to interact with WooCommerce for products, categories, images, and maintenance.
#==========================================================================================
import httpx
import base64
import logging
import hashlib
from app.config import settings
from fastapi import Header

WC_BASE_URL = settings.WC_BASE_URL
WC_API_KEY = settings.WC_API_KEY
WC_API_SECRET = settings.WC_API_SECRET
WP_USERNAME = settings.WP_USERNAME
WP_PASSWORD = settings.WP_PASSWORD
WC_BASIC_USER = settings.WC_BASIC_USER
WC_BASIC_PASS = settings.WC_BASIC_PASS

logger = logging.getLogger("uvicorn.error")

# ---- Products ----

async def get_wc_products():
    """Fetch all WooCommerce products (paginated, unlimited)."""
    auth = (WC_API_KEY, WC_API_SECRET)
    products = []
    page = 1
    while True:
        url = f"{WC_BASE_URL}/wp-json/wc/v3/products?per_page=100&page={page}"
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            try:
                resp = await client.get(url, auth=auth)
            except Exception as e:
                print(f"Error fetching WooCommerce products: {e}")
                break
        if resp.status_code != 200:
            break
        batch = resp.json()
        if not batch:
            break
        products.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return products

async def create_wc_product(product_data):
    """Create a new product in WooCommerce."""
    url = f"{WC_BASE_URL}/wp-json/wc/v3/products"
    auth = (WC_API_KEY, WC_API_SECRET)
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        try:
            resp = await client.post(url, auth=auth, json=product_data)
            return {"status_code": resp.status_code, "data": resp.json() if resp.content else None}
        except Exception as e:
            return {"error": str(e)}

async def update_wc_product(product_id, product_data):
    """Update a WooCommerce product by ID."""
    url = f"{WC_BASE_URL}/wp-json/wc/v3/products/{product_id}"
    auth = (WC_API_KEY, WC_API_SECRET)
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        try:
            resp = await client.put(url, auth=auth, json=product_data)
            return {"status_code": resp.status_code, "data": resp.json() if resp.content else None}
        except Exception as e:
            return {"error": str(e)}

# ---- Categories ----

async def get_wc_categories():
    """Fetch all WooCommerce product categories."""
    url = f"{WC_BASE_URL}/wp-json/wc/v3/products/categories?per_page=100"
    auth = (WC_API_KEY, WC_API_SECRET)
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        try:
            resp = await client.get(url, auth=auth)
            return resp.json() if resp.status_code == 200 else []
        except Exception as e:
            print("Error fetching WooCommerce categories:", e)
            return []

async def create_wc_category(name, parent_id=None):
    """Create a WooCommerce product category."""
    url = f"{WC_BASE_URL}/wp-json/wc/v3/products/categories"
    auth = (WC_API_KEY, WC_API_SECRET)
    payload = {"name": name}
    if parent_id:
        payload["parent"] = parent_id
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        try:
            resp = await client.post(url, auth=auth, json=payload)
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

# ---- Maintenance Utilities ----

async def purge_wc_bin_products():
    """Force-deletes all WooCommerce products in the BIN (Trash)."""
    url = f"{WC_BASE_URL}/wp-json/wc/v3/products?status=trash&per_page=100"
    auth = (WC_API_KEY, WC_API_SECRET)
    try:
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            resp = await client.get(url, auth=auth)
        trashed = resp.json() if resp.status_code == 200 else []
        results = []
        for product in trashed:
            del_url = f"{WC_BASE_URL}/wp-json/wc/v3/products/{product['id']}?force=true"
            async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
                del_resp = await client.delete(del_url, auth=auth)
            results.append({
                "id": product["id"],
                "name": product.get("name"),
                "deleted": del_resp.status_code == 200,
                "status_code": del_resp.status_code,
                "response": del_resp.json() if del_resp.content else None
            })
        return {"count_deleted": len(results), "results": results}
    except Exception as e:
        return {"error": str(e)}

async def purge_all_wc_products():
    """Force-delete ALL WooCommerce products (use with caution!)."""
    try:
        products = await get_wc_products()
        auth = (WC_API_KEY, WC_API_SECRET)
        results = []
        for product in products:
            del_url = f"{WC_BASE_URL}/wp-json/wc/v3/products/{product['id']}?force=true"
            async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
                del_resp = await client.delete(del_url, auth=auth)
            results.append({
                "id": product["id"],
                "name": product.get("name"),
                "deleted": del_resp.status_code == 200,
                "status_code": del_resp.status_code
            })
        return {"count_deleted": len(results), "results": results}
    except Exception as e:
        return {"error": str(e)}

async def purge_wc_product_variations(product_id):
    """Delete all variations for a specific product."""
    url = f"{WC_BASE_URL}/wp-json/wc/v3/products/{product_id}/variations?per_page=100"
    auth = (WC_API_KEY, WC_API_SECRET)
    try:
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            resp = await client.get(url, auth=auth)
            variations = resp.json() if resp.status_code == 200 else []
            results = []
            for var in variations:
                del_url = f"{WC_BASE_URL}/wp-json/wc/v3/products/{product_id}/variations/{var['id']}?force=true"
                del_resp = await client.delete(del_url, auth=auth)
                results.append({
                    "id": var["id"],
                    "deleted": del_resp.status_code == 200,
                    "status_code": del_resp.status_code
                })
            return {"count_deleted": len(results), "results": results}
    except Exception as e:
        return {"error": str(e)}

async def list_wc_bin_products():
    """Lists all WooCommerce products in the BIN (Trash)."""
    url = f"{WC_BASE_URL}/wp-json/wc/v3/products?status=trash&per_page=100"
    auth = (WC_API_KEY, WC_API_SECRET)
    try:
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            resp = await client.get(url, auth=auth)
            return resp.json() if resp.status_code == 200 else []
    except Exception as e:
        return {"error": str(e)}

# ---- Image Upload (WordPress Auth, WP App Password) ----

# -------------------------------------------------------------------
# 1) Download from ERPNext and upload via App Password + site-Basic Auth
# -------------------------------------------------------------------
async def upload_wc_image_from_erpnext(image_url: str, filename: str,
                                        erp_api_key: str, erp_api_secret: str):
    """
    Download an ERPNext image (token auth) then upload it to WP via
    Basic auth (WP_USERNAME + WP_PASSWORD).
    Returns the WP media object or an error dict.
    """
    # 1) Fetch from ERPNext
    headers_erp = {"Authorization": f"token {erp_api_key}:{erp_api_secret}"}
    if not image_url.lower().startswith(("http://", "https://")):
        image_url = settings.ERP_URL.rstrip("/") + image_url

    async with httpx.AsyncClient(timeout=30.0, verify=False) as erp_client:
        img_resp = await erp_client.get(image_url, headers=headers_erp)
        if img_resp.status_code != 200:
            return {"error": "Failed to download image", "status": img_resp.status_code}
        img_bytes    = img_resp.content
        content_type = img_resp.headers.get("Content-Type", "application/octet-stream")

    # 2) Upload to WP
    media_url = f"{WC_BASE_URL}/wp-json/wp/v2/media"
    auth      = (WP_USERNAME, WP_PASSWORD)
    upload_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": content_type,
    }

    async with httpx.AsyncClient(timeout=20.0, verify=False, auth=auth) as wp:
        up_resp = await wp.post(media_url, content=img_bytes, headers=upload_headers)
        if up_resp.status_code not in (200, 201):
            return {
                "error": "Failed to upload image",
                "status": up_resp.status_code,
                "detail": up_resp.text
            }
        return up_resp.json()
    

# -------------------------------------------------------------------
# 2) List all WP media (with size details) using site-Basic Auth + App Password
# -------------------------------------------------------------------
async def wp_list_media():
    """
    Fetch all images from WP media library (paginated) using
    Basic auth (WP_USERNAME + WP_PASSWORD).
    Returns list of media dicts.
    """
    media_url = f"{WC_BASE_URL}/wp-json/wp/v2/media?per_page=100"
    auth      = (WP_USERNAME, WP_PASSWORD)

    media = []
    page  = 1
    async with httpx.AsyncClient(timeout=20.0, verify=False, auth=auth) as wp:
        while True:
            resp = await wp.get(f"{media_url}&page={page}")
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not batch:
                break
            media.extend(batch)
            if len(batch) < 100:
                break
            page += 1

    return media

# -------------------------------------------------------------------
# 3) Upload an arbitrary URL to WP media library (same auth pattern)
# -------------------------------------------------------------------
async def wp_upload_image_from_url(url: str, filename: str):
    """
    Download a public URL then upload to WP media (Basic auth).
    Returns the new image's WP media dict.
    """
    media_url = f"{WC_BASE_URL}/wp-json/wp/v2/media"
    auth      = (WP_USERNAME, WP_PASSWORD)

    # 1) Download source
    async with httpx.AsyncClient(timeout=20.0, verify=False) as down:
        img_resp = await down.get(url)
        if img_resp.status_code == 404:
            logger.warning(f"[IMG] Source missing (404): {url}")
            return None
        img_resp.raise_for_status()
        img_bytes    = img_resp.content
        content_type = img_resp.headers.get("Content-Type", "application/octet-stream")

    # 2) Upload to WP
    upload_headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": content_type,
    }
    async with httpx.AsyncClient(timeout=20.0, verify=False, auth=auth) as wp:
        upload_resp = await wp.post(media_url, content=img_bytes, headers=upload_headers)
        upload_resp.raise_for_status()
        data = upload_resp.json()
        return {
            "id":         data["id"],
            "source_url": data["source_url"],
            "size":       len(img_bytes),
        }


async def ensure_wp_image_uploaded(erp_img_url, filename, size_hint=None):
    """
    Checks WP media for an image matching ERPNext's (by size or SHA256 hash).
    If not found, uploads it. Returns WP media ID.
    """
    media = await wp_list_media()
    found_id = None

    #logger.info(f"[IMG] downloading ERP image from {erp_img_url!r}")

    # Download ERPNext image
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        img_resp = await client.get(erp_img_url)
        img_bytes = img_resp.content
        img_size = len(img_bytes)
        img_hash = hashlib.sha256(img_bytes).hexdigest()
        
    for m in media:
        # WP media sometimes gives size under 'media_details' > 'filesize'
        m_size = m.get("media_details", {}).get("filesize")
        if m_size and int(m_size) == img_size:
            found_id = m["id"]
            break
        # If you want extra certainty, download and hash here (not usually needed for perf)

    if found_id:
        return found_id
    # Not found, upload
    result = await wp_upload_image_from_url(erp_img_url, filename)
    if result and "id" in result:
        return result["id"]
    else:
        # Image upload failed (404, etc.), skip and return None
        return None


async def set_wc_variant_image(parent_id, variant_id, media_id):
    """
    Sets the image for a WooCommerce variant.
    """
    url = f"{WC_BASE_URL}/wp-json/wc/v3/products/{parent_id}/variations/{variant_id}"
    auth = (WC_API_KEY, WC_API_SECRET)
    payload = {"image": {"id": media_id}}
    async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
        try:
            resp = await client.put(url, auth=auth, json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

async def get_wc_variations(parent_id):
    """
    Fetches all variations for a given parent variable product ID from WooCommerce.
    Returns a list of dicts (each a variation, includes images and SKU).
    """
    url = f"{WC_BASE_URL}/wp-json/wc/v3/products/{parent_id}/variations?per_page=100"
    auth = (WC_API_KEY, WC_API_SECRET)
    async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
        try:
            resp = await client.get(url, auth=auth)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return []
