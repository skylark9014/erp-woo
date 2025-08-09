# app/sync/components/images.py
from __future__ import annotations

import httpx
import asyncio
import logging
import os
import re

from typing import Callable, Awaitable
from urllib.parse import urlparse, urljoin
from app.config import settings
from app.sync.components.util import maybe_await

logger = logging.getLogger(__name__)

ERP_BASE = (settings.ERP_URL or "").rstrip("/")
ERP_HOST = urlparse(ERP_BASE).netloc
ERP_AUTH_HEADER = {"Authorization": f"token {settings.ERP_API_KEY}:{settings.ERP_API_SECRET}"}

# ------------------------------------------------------------------------------
# ERP URL handling
# ------------------------------------------------------------------------------

def _ensure_abs_erp_url(u: str) -> str:
    """
    Best-effort absolutizer for ERPNext file URLs.
    Accepts:
      - absolute http(s) URLs -> returned as-is
      - "/files/..." or "files/..." -> joined to settings.ERP_URL when available
    """
    if not u:
        return u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    path = u if u.startswith("/") else f"/{u}"
    base = (settings.ERP_URL or "").rstrip("/")
    if base:
        return urljoin(base, path)
    # No base configured — return original path so the caller can decide what to do.
    logger.debug("settings.ERP_URL not set; cannot absolutize ERP image url %s", u)
    return path

def _dedupe_preserve_order(items):
    seen = set()
    out = []
    for x in items or []:
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out

async def _collect_primary_image_urls(variant: dict | None, template_item: dict | None) -> list[str]:
    urls: list[str] = []
    for src in (variant or {}), (template_item or {}):
        for field in ("website_image", "image"):
            val = (src or {}).get(field)
            if val:
                urls.append(_ensure_abs_erp_url(val))
    return _dedupe_preserve_order(urls)

async def _collect_attachment_urls(
    item_code: str | None,
    get_erp_images_func: Callable[[str], list | dict | Awaitable[list | dict]] | None
) -> list[str]:
    """
    Fetch ERPNext item attachments via provided function (sync or async).
    The fetcher should return either:
      - list[str or dict] (dicts may contain file_url/url/src)
      - or {"urls": [...]} wrapper
    """
    if not item_code or not get_erp_images_func:
        return []
    try:
        res = await maybe_await(get_erp_images_func(item_code))
    except Exception as e:
        logger.debug("Attachment fetch failed for %s: %s", item_code, e)
        return []

    if isinstance(res, dict) and "urls" in res:
        res = res.get("urls") or []

    urls: list[str] = []
    if isinstance(res, list):
        for it in res:
            if isinstance(it, str):
                urls.append(_ensure_abs_erp_url(it))
            elif isinstance(it, dict):
                url = it.get("file_url") or it.get("url") or it.get("src")
                if url:
                    urls.append(_ensure_abs_erp_url(url))
    return _dedupe_preserve_order(urls)

# ------------------------------------------------------------------------------
# HTTP size probing
# ------------------------------------------------------------------------------


    """
    Return Content-Length for a URL using HEAD; fall back to tiny ranged GET.
    """
    if not httpx:
        logger.debug("httpx not available; cannot probe size for %s", url)
        return None

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        # Try HEAD first
        try:
            r = await client.head(url)
            cl = r.headers.get("Content-Length") or r.headers.get("content-length")
            if cl and cl.isdigit():
                return int(cl)
            # Some servers don't set CL on HEAD; continue to range GET
        except Exception as e:
            logger.debug("HEAD failed for %s: %s", url, e)

        # Fallback: GET first byte (cheap-ish)
        try:
            r = await client.get(url, headers={"Range": "bytes=0-0"})
            # Content-Range: bytes 0-0/123456
            cr = r.headers.get("Content-Range") or r.headers.get("content-range")
            if cr and "/" in cr:
                total = cr.split("/")[-1].strip()
                if total.isdigit():
                    return int(total)
            cl = r.headers.get("Content-Length") or r.headers.get("content-length")
            if cl and cl.isdigit():
                return int(cl)
        except Exception as e:
            logger.debug("Range GET failed for %s: %s", url, e)

    return None

async def _sizes_for_urls(urls: list[str], *, concurrency: int = 8) -> list[int | None]:
    """
    Probe sizes for a list of URLs with limited concurrency.
    """
    sem = asyncio.Semaphore(concurrency)

    async def _one(u: str):
        async with sem:
            return await _head_size(u)

    tasks = [asyncio.create_task(_one(u)) for u in urls or []]
    if not tasks:
        return []
    return await asyncio.gather(*tasks)


    """
    Return Content-Length for a URL using HEAD; fall back to tiny ranged GET.
    Adds ERP auth automatically for ERP-hosted files (including /private/files).
    """
    if not httpx:
        logger.debug("httpx not available; cannot probe size for %s", url)
        return None

    erp_host = ""
    try:
        erp_host = urlparse(settings.ERP_URL or "").netloc
    except Exception:
        pass
    u_host = ""
    try:
        u_host = urlparse(url).netloc
    except Exception:
        pass

    headers = None
    if erp_host and u_host and (erp_host == u_host or not u_host):
        headers = {"Authorization": f"token {settings.ERP_API_KEY}:{settings.ERP_API_SECRET}"}

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, verify=False) as client:
        # Try HEAD first
        try:
            r = await client.head(url, headers=headers)
            cl = r.headers.get("Content-Length") or r.headers.get("content-length")
            if cl and cl.isdigit():
                return int(cl)
        except Exception as e:
            logger.debug("HEAD failed for %s: %s", url, e)

        # Fallback: GET first byte
        try:
            hdrs = {"Range": "bytes=0-0"}
            if headers:
                hdrs.update(headers)
            r = await client.get(url, headers=hdrs)
            cr = r.headers.get("Content-Range") or r.headers.get("content-range")
            if cr and "/" in cr:
                total = cr.split("/")[-1].strip()
                if total.isdigit():
                    return int(total)
            cl = r.headers.get("Content-Length") or r.headers.get("content-length")
            if cl and cl.isdigit():
                return int(cl)
        except Exception as e:
            logger.debug("Range GET failed for %s: %s", url, e)

    return None


    """
    Return Content-Length for a URL using HEAD; fall back to tiny ranged GET.
    Adds ERP auth automatically for ERP-hosted files (including /private/files).
    """
    if not httpx:
        logger.debug("httpx not available; cannot probe size for %s", url)
        return None

    erp_host = urlparse((settings.ERP_URL or "").rstrip("/")).netloc
    u = urlparse(url)
    same_host_or_relative = (not u.netloc) or (u.netloc == erp_host)

    headers = None
    if same_host_or_relative and settings.ERP_API_KEY and settings.ERP_API_SECRET:
        headers = {"Authorization": f"token {settings.ERP_API_KEY}:{settings.ERP_API_SECRET}"}

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, verify=False) as client:
        try:
            r = await client.head(url, headers=headers)
            cl = r.headers.get("Content-Length") or r.headers.get("content-length")
            if cl and cl.isdigit():
                return int(cl)
        except Exception as e:
            logger.debug("HEAD failed for %s: %s", url, e)

        try:
            hdrs = {"Range": "bytes=0-0"}
            if headers:
                hdrs.update(headers)
            r = await client.get(url, headers=hdrs)
            cr = r.headers.get("Content-Range") or r.headers.get("content-range")
            if cr and "/" in cr:
                total = cr.split("/")[-1].strip()
                if total.isdigit():
                    return int(total)
            cl = r.headers.get("Content-Length") or r.headers.get("content-length")
            if cl and cl.isdigit():
                return int(cl)
        except Exception as e:
            logger.debug("Range GET failed for %s: %s", url, e)

    return None


    """
    Return Content-Length for a URL using HEAD; fall back to tiny ranged GET.
    Adds ERP auth automatically for ERP-hosted files (including /private/files).
    """
    if not httpx:
        logger.debug("httpx not available; cannot probe size for %s", url)
        return None

    erp_host = urlparse((settings.ERP_URL or "").rstrip("/")).netloc
    u = urlparse(url)
    same_host_or_relative = (not u.netloc) or (u.netloc == erp_host)

    headers = None
    if same_host_or_relative and settings.ERP_API_KEY and settings.ERP_API_SECRET:
        headers = {"Authorization": f"token {settings.ERP_API_KEY}:{settings.ERP_API_SECRET}"}

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, verify=False) as client:
        # HEAD
        try:
            r = await client.head(url, headers=headers)
            cl = r.headers.get("Content-Length") or r.headers.get("content-length")
            if cl and cl.isdigit():
                return int(cl)
        except Exception as e:
            logger.debug("HEAD failed for %s: %s", url, e)

        # Range GET 0-0
        try:
            hdrs = {"Range": "bytes=0-0"}
            if headers:
                hdrs.update(headers)
            r = await client.get(url, headers=hdrs)
            cr = r.headers.get("Content-Range") or r.headers.get("content-range")
            if cr and "/" in cr:
                total = cr.split("/")[-1].strip()
                if total.isdigit():
                    return int(total)
            cl = r.headers.get("Content-Length") or r.headers.get("content-length")
            if cl and cl.isdigit():
                return int(cl)
        except Exception as e:
            logger.debug("Range GET failed for %s: %s", url, e)

    return None

async def _head_size(url: str, *, timeout: float = 8.0) -> int | None:
    if not httpx:
        logger.debug("httpx not available; cannot probe size for %s", url)
        return None

    def _headers_for(u: str):
        try:
            p = urlparse(u)
            if (p.netloc and p.netloc == ERP_HOST) or u.startswith("/files/") or u.startswith("/private/files/"):
                return ERP_AUTH_HEADER
        except Exception:
            pass
        return None

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        # HEAD
        try:
            r = await client.head(url, headers=_headers_for(url))
            cl = r.headers.get("Content-Length") or r.headers.get("content-length")
            if cl and cl.isdigit():
                return int(cl)
        except Exception as e:
            logger.debug("HEAD failed for %s: %s", url, e)

        # Range GET 0-0
        try:
            r = await client.get(url, headers={**(_headers_for(url) or {}), "Range": "bytes=0-0"})
            cr = r.headers.get("Content-Range") or r.headers.get("content-range")
            if cr and "/" in cr:
                total = cr.split("/")[-1].strip()
                if total.isdigit():
                    return int(total)
            cl = r.headers.get("Content-Length") or r.headers.get("content-length")
            if cl and cl.isdigit():
                return int(cl)
        except Exception as e:
            logger.debug("Range GET failed for %s: %s", url, e)

    return None

# ------------------------------------------------------------------------------
# ERP gallery: primary + attachments
# ------------------------------------------------------------------------------

async def safe_get_erp_gallery_for_sku(
    sku: str,
    variant: dict | None,
    template_item: dict | None,
    get_erp_images_func: Callable[[str], list | dict | Awaitable[list | dict]] | None = None,
) -> list[dict]:
    """
    Build the ERP gallery for a SKU:
      order = variant primary → variant attachments → template primary → template attachments

    Returns list of dicts: [{ "url": str, "size": int }, ...]
    If a size couldn't be determined, that entry is omitted.
    """
    try:
        v_code = (variant or {}).get("item_code") or (variant or {}).get("name") or sku
        t_code = (template_item or {}).get("item_code") or (template_item or {}).get("name") or v_code

        primary_variant = await _collect_primary_image_urls(variant, None)
        primary_template = await _collect_primary_image_urls(None, template_item)

        attach_variant = await _collect_attachment_urls(v_code, get_erp_images_func)
        attach_template = []
        if t_code and t_code != v_code:
            attach_template = await _collect_attachment_urls(t_code, get_erp_images_func)

        urls = _dedupe_preserve_order(primary_variant + attach_variant + primary_template + attach_template)

        sizes = await _sizes_for_urls(urls)
        out: list[dict] = []
        for u, s in zip(urls, sizes):
            if isinstance(s, int) and s > 0:
                out.append({"url": u, "size": s})
        return out
    except Exception as e:
        logger.exception("safe_get_erp_gallery_for_sku failed for %s: %s", sku, e)
        return []

# Back-compat alias
async def get_erp_gallery_for_sku(
    sku: str,
    variant: dict | None,
    template_item: dict | None,
    get_erp_images_func: Callable[[str], list | dict | Awaitable[list | dict]] | None = None,
) -> list[dict]:
    return await safe_get_erp_gallery_for_sku(sku, variant, template_item, get_erp_images_func)

# ------------------------------------------------------------------------------
# Woo gallery normalization & sizing
# ------------------------------------------------------------------------------

def normalize_gallery_from_wc_product(wc_product: dict | None) -> list[str]:
    """
    Extract an ordered list of image URLs from a Woo product object
    (the /wp-json/wc/v3/products/{id} shape). Tolerates odd shapes.
    """
    if not isinstance(wc_product, dict):
        return []

    urls: list[str] = []
    images = wc_product.get("images") or []
    if isinstance(images, list):
        try:
            images = sorted(
                images,
                key=lambda x: (
                    x.get("position", 0) if isinstance(x, dict) else 0,
                    x.get("id", 0) if isinstance(x, dict) else 0,
                ),
            )
        except Exception:
            # keep original order if sorting fails
            pass

        for img in images:
            if isinstance(img, dict):
                src = img.get("src") or img.get("url")
                if src:
                    urls.append(src)
            elif isinstance(img, str):
                urls.append(img)

    # Some themes stash a single 'image' object (featured)
    img_obj = wc_product.get("image")
    if isinstance(img_obj, dict):
        src = img_obj.get("src") or img_obj.get("url")
        if src:
            urls.insert(0, src)

    return _dedupe_preserve_order(urls)

async def get_wc_gallery_sizes_for_product(wc_product: dict | None) -> list[int]:
    """
    Return Content-Lengths for the Woo product gallery image URLs.
    """
    urls = normalize_gallery_from_wc_product(wc_product)
    if not urls:
        return []
    sizes = await _sizes_for_urls(urls)
    return [int(s) for s in sizes if isinstance(s, int) and s > 0]

# Back-compat alias
async def get_wc_gallery_sizes_from_product(wc_product: dict | None) -> list[int]:
    return await get_wc_gallery_sizes_for_product(wc_product)

# ------------------------------------------------------------------------------
# Compare ERP vs Woo galleries
# ------------------------------------------------------------------------------

def _looks_like_sizes(seq) -> bool:
    return isinstance(seq, list) and all(
        isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit())
        for x in seq
    )

def _coerce_sizes(seq) -> list[int]:
    out: list[int] = []
    for x in seq or []:
        try:
            v = int(x)
            if v > 0:
                out.append(v)
        except Exception:
            pass
    return out

def _as_url_list(gallery) -> list[str]:
    urls: list[str] = []
    if isinstance(gallery, list):
        for g in gallery:
            if isinstance(g, str):
                urls.append(g)
            elif isinstance(g, dict):
                u = g.get("src") or g.get("url")
                if u:
                    urls.append(u)
    return urls

# Strip WP size suffixes like "-600x600" before extension
_size_suffix_re = re.compile(r"-\d+x\d+(?=\.\w{3,4}$)", re.IGNORECASE)

def _norm_wp_filename(u: str) -> str:
    try:
        p = urlparse(u)
        base = os.path.basename(p.path or u)
        base = _size_suffix_re.sub("", base)
        return base.lower()
    except Exception:
        return u.lower()

def gallery_images_equal(
    erp_gallery,
    wc_gallery,
    *,
    tolerance_bytes: int = 4096,
    consider_order: bool = True
) -> bool:
    """
    Return True if two galleries are effectively the same.

    - If both are numeric (sizes), bucket by tolerance to ignore small deltas.
    - Else, compare normalized filenames (ignoring WP -WxH suffixes).
    """
    # Numeric path
    if _looks_like_sizes(erp_gallery) and _looks_like_sizes(wc_gallery):
        a = _coerce_sizes(erp_gallery)
        b = _coerce_sizes(wc_gallery)
        if len(a) != len(b):
            return False
        a_b = [v // max(1, tolerance_bytes) for v in a]
        b_b = [v // max(1, tolerance_bytes) for v in b]
        return (a_b == b_b) if consider_order else (sorted(a_b) == sorted(b_b))

    # URL / dict path
    a_urls = _as_url_list(erp_gallery)
    b_urls = _as_url_list(wc_gallery)
    if len(a_urls) != len(b_urls):
        return False
    a_keys = [_norm_wp_filename(u) for u in a_urls]
    b_keys = [_norm_wp_filename(u) for u in b_urls]
    return (a_keys == b_keys) if consider_order else (sorted(a_keys) == sorted(b_keys))
