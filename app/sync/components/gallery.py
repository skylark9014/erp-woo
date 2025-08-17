# app/sync/components/gallery.py
from __future__ import annotations

import inspect
import httpx

from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urljoin
from app.config import settings
from app.sync.sync_utils import get_erp_image_list  # tolerant wrapper around ERP item image(s)
from app.erp.erpnext import get_erp_images         # fallback direct fetch

ERP_BASE = settings.ERP_URL.rstrip("/")
ERP_HOST = urlparse(ERP_BASE).netloc
ERP_AUTH_HEADER = {
    "Authorization": f"token {settings.ERP_API_KEY}:{settings.ERP_API_SECRET}"
}

async def _maybe_await(x):
    return await x if inspect.isawaitable(x) else x

def _normalize_gallery_return(g) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
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
                if url:
                    out.append({"url": url.strip(), "size": int(item.get("size") or 0)})
            elif isinstance(item, str):
                if item.strip():
                    out.append({"url": item.strip(), "size": 0})
    return out

def _extract_image_urls_from_item(item: dict) -> List[Dict[str, Any]]:
    urls: List[Dict[str, Any]] = []
    for key in ("website_image", "image", "thumbnail", "image_url", "img"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            urls.append({"url": v.strip(), "size": 0})
    for i in range(1, 6):
        v = item.get(f"image_{i}")
        if isinstance(v, str) and v.strip():
            urls.append({"url": v.strip(), "size": 0})
    # dedup
    seen = set()
    out = []
    for d in urls:
        u = d["url"]
        if u not in seen:
            seen.add(u)
            out.append(d)
    return out

def _is_erp_url(u: str) -> bool:
    if not u:
        return False
    if u.startswith("/"):
        return True
    p = urlparse(u)
    return bool(p.netloc) and p.netloc == ERP_HOST

def _full_url(u: str) -> str:
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return urljoin(ERP_BASE + "/", u.lstrip("/"))

async def _fetch_content_length(client: httpx.AsyncClient, url: str, use_erp_auth: bool) -> int:
    headers = ERP_AUTH_HEADER if use_erp_auth else None
    try:
        r = await client.head(url, headers=headers)
        if r.status_code == 200 and "content-length" in r.headers:
            return int(r.headers["content-length"])
    except Exception:
        pass
    # fallback GET if HEAD didn’t return size
    try:
        r = await client.get(url, headers=headers)
        if r.status_code == 200 and r.content is not None:
            return len(r.content)
    except Exception:
        pass
    return 0

async def _enrich_gallery_with_sizes(gallery: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not gallery:
        return []
    out: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        for item in gallery:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            size = int(item.get("size") or 0)
            full = _full_url(url)
            if size <= 0:
                size = await _fetch_content_length(client, full, use_erp_auth=_is_erp_url(url))
            out.append({"url": full, "size": size})
    return out

def normalize_gallery_from_wc_product(wc_prod: Dict[str, Any]) -> List[Dict[str, Any]]:
    imgs = wc_prod.get("images") or []
    out = []
    for i in imgs:
        src = (i.get("src") or "").strip()
        if src:
            out.append({"url": src, "size": 0})  # we leave WC sizes 0 (optional)
    return out

def gallery_images_equal(erp_gallery, wc_gallery) -> bool:
    erp_set = {(img.get("url") or "").strip() for img in (erp_gallery or [])}
    wc_set  = {(img.get("url") or "").strip() for img in (wc_gallery or [])}
    return erp_set == wc_set
