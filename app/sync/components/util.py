# app/sync/components/util.py
from __future__ import annotations

import inspect
import re
import os
from urllib.parse import urlparse
from typing import Any, List, Dict

async def maybe_await(x):
    if inspect.isawaitable(x):
        return await x
    return x

def strip_html(text: str | None) -> str:
    return re.sub(r"<[^>]+>", "", text or "")

def basename(url_or_path: str) -> str:
    try:
        if url_or_path.startswith(("http://", "https://")):
            return os.path.basename(urlparse(url_or_path).path) or "image.jpg"
        return os.path.basename(url_or_path)
    except Exception:
        return "image.jpg"

def gallery_images_equal(erp_gallery, wc_gallery):
    erp_set = {(img.get("url") or "").strip() for img in (erp_gallery or [])}
    wc_set = {(img.get("url") or "").strip() for img in (wc_gallery or [])}
    return erp_set == wc_set
