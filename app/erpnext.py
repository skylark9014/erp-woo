#===========================================================================
# app/erpnext.py
# ERPNext API interface module.
# Provides functions to interact with ERPNext for products and categories.
#===========================================================================

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from app.config import settings
from app.field_mapping import get_erp_sync_fields

# --- Settings / globals ----------------------------------------------------

ERP_URL: str = settings.ERP_URL.rstrip("/")
ERP_API_KEY: str = settings.ERP_API_KEY
ERP_API_SECRET: str = settings.ERP_API_SECRET
ERP_SELLING_PRICE_LIST: Optional[str] = getattr(settings, "ERP_SELLING_PRICE_LIST", None)

# Optional: extra filters/groups if present in settings (both may be None)
SYNC_ITEM_GROUPS = getattr(settings, "SYNC_ITEM_GROUPS", None)  # list[str] or comma-str
ERP_ITEM_FILTERS_JSON = getattr(settings, "ERP_ITEM_FILTERS_JSON", None)  # JSON string of [["field","op","val"],...]

LAST_PRICE_LIST_USED: Optional[str] = None

logger = logging.getLogger("uvicorn.error")


# --- Helpers ---------------------------------------------------------------

def _headers() -> Dict[str, str]:
    return {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}

def _abs_url(u: str | None) -> str | None:
    if not u:
        return None
    if u.startswith("http://") or u.startswith("https://"):
        return u
    # ensure a single slash
    return f"{ERP_URL}{u if u.startswith('/') else '/' + u}"

def _normalize_groups(val) -> Optional[List[str]]:
    if not val:
        return None
    if isinstance(val, (list, tuple)):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        parts = [p.strip() for p in val.split(",")]
        return [p for p in parts if p]
    return None

def _build_item_filters() -> List[list]:
    filters: List[list] = [["disabled", "=", 0], ["is_sales_item", "=", 1]]
    groups = _normalize_groups(SYNC_ITEM_GROUPS)
    if groups:
        filters.append(["item_group", "in", groups])
    if ERP_ITEM_FILTERS_JSON:
        try:
            extra = json.loads(ERP_ITEM_FILTERS_JSON)
            if isinstance(extra, list):
                for row in extra:
                    if isinstance(row, list) and len(row) >= 3:
                        filters.append(row[:3])
        except Exception as e:
            logger.warning("Failed parsing ERP_ITEM_FILTERS_JSON: %s", e)
    return filters

async def _http_get(path: str, params: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> httpx.Response:
    url = path if path.startswith("http") else f"{ERP_URL}{path}"
    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
        return await client.get(url, headers=_headers(), params=params or {})

async def _http_post(path: str, payload: Dict[str, Any], timeout: float = 30.0) -> httpx.Response:
    url = path if path.startswith("http") else f"{ERP_URL}{path}"
    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
        return await client.post(url, headers=_headers(), json=payload)

# Small concurrency limiter for per-item fetches
class _Limiter:
    def __init__(self, limit: int = 8):
        self._sem = asyncio.Semaphore(limit)
    async def __aenter__(self):
        await self._sem.acquire()

async def __aexit__(self, exc_type, exc, tb):
    self._sem.release()

async def get_price_map(price_list: str | None = None, return_name: bool = False):
    """
    Build {item_code: price} from the *best* Price List.

    Selection:
      1) Consider ONLY lists with enabled=1 first (highest Item Price row count wins).
      2) If none have rows, consider disabled lists (highest count wins).
      3) Final fallback: explicit arg/settings or "Standard Selling".

    Returns (map, name) if return_name=True, else just the map.
    """
    headers = {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        explicit = price_list or settings.ERP_SELLING_PRICE_LIST

        # Fetch all price lists so we can partition by enabled flag
        params = {
            "fields": '["name","enabled","selling"]',
            "order_by": "creation desc",
            "limit_page_length": 200,
        }
        r = await client.get(f"{ERP_URL}/api/resource/Price%20List", headers=headers, params=params)
        data = r.json().get("data", []) if r.status_code == 200 else []

        list_info = {
            row["name"]: {
                "enabled": 1 if (row.get("enabled", 1) in (1, True)) else 0,
                "selling": 1 if (row.get("selling", 0) in (1, True)) else 0,
            }
            for row in data if row.get("name")
        }

        # Candidate order: explicit (if any), all discovered, then a safety "Standard Selling"
        candidates: list[str] = []
        def add(name: str | None):
            if name and name not in candidates:
                candidates.append(name)

        add(explicit)
        for n in list_info.keys():
            add(n)
        add("Standard Selling")

        # Count Item Price rows per candidate
        counts: dict[str, int] = {}
        for pl in candidates:
            try:
                count_url = (
                    f"{ERP_URL}/api/method/frappe.client.get_count"
                    f"?doctype=Item%20Price&filters={json.dumps([['price_list','=',pl]])}"
                )
                rc = await client.get(count_url, headers=headers)
                counts[pl] = int((rc.json().get("message") if rc.status_code == 200 else 0) or 0)
            except Exception:
                counts[pl] = 0

        # Prefer enabled lists first
        enabled_first = [pl for pl in candidates if list_info.get(pl, {}).get("enabled", 1) == 1]
        disabled_then = [pl for pl in candidates if pl not in enabled_first]

        def pick_best(group: list[str]) -> tuple[str | None, int]:
            best, maxc = None, 0
            for pl in group:
                c = counts.get(pl, 0)
                if c > maxc:
                    best, maxc = pl, c
            return best, maxc

        chosen, max_count = pick_best(enabled_first)
        if not chosen or max_count == 0:
            chosen, max_count = pick_best(disabled_then)
        if not chosen:
            chosen = explicit or "Standard Selling"

        # Fetch actual prices for the chosen list
        params = {
            "fields": '["item_code","price_list_rate"]',
            "filters": json.dumps([["price_list", "=", chosen]]),
            "limit_page_length": 5000,
        }
        r = await client.get(f"{ERP_URL}/api/resource/Item%20Price", headers=headers, params=params)
        rows = r.json().get("data", []) if r.status_code == 200 else []

        pm: dict[str, float] = {}
        for row in rows:
            code, rate = row.get("item_code"), row.get("price_list_rate")
            if code is None or rate is None:
                continue
            try:
                pm[str(code)] = float(rate)
            except Exception:
                pass

        globals()["LAST_PRICE_LIST_USED"] = chosen
        return (pm, chosen) if return_name else pm

# --- Items -----------------------------------------------------------------
async def get_erpnext_items():
    """
    Fetch sales-enabled, not-disabled Items in one call using the mapping fields.
    Avoid fields that aren't permitted in list queries (e.g. website_image).
    """
    try:
        erp_fields = get_erp_sync_fields()
        if not erp_fields or not isinstance(erp_fields, (list, tuple)):
            erp_fields = [
                "name", "item_code", "item_group", "brand",
                "has_variants", "image", "description"
            ]

        # Some ERPNext installs disallow certain fields in list queries.
        # Drop website_image (and any obvious empties/dupes).
        banned = {"website_image"}
        safe_fields = []
        seen = set()
        for f in erp_fields:
            if not f or f in banned or f in seen:
                continue
            seen.add(f)
            safe_fields.append(f)
        # Ensure we at least have these basics:
        for must in ("name", "item_code", "item_group", "brand", "has_variants", "image"):
            if must not in seen:
                safe_fields.append(must)

        # Base filters
        filters = [["disabled", "=", 0], ["is_sales_item", "=", 1]]

        # Optional group filter from settings
        groups = getattr(settings, "SYNC_ITEM_GROUPS", None)
        if groups:
            if isinstance(groups, str):
                try:
                    groups = json.loads(groups)
                except Exception:
                    groups = [groups]
            if isinstance(groups, (list, tuple)) and groups:
                filters.append(["item_group", "in", list(groups)])

        # Optional extra filters JSON
        extra_json = getattr(settings, "ERP_ITEM_FILTERS_JSON", None)
        if extra_json:
            try:
                extra = json.loads(extra_json)
                if isinstance(extra, list):
                    filters.extend(extra)
            except Exception as e:
                logger.warning("Ignoring ERP_ITEM_FILTERS_JSON parse error: %s", e)

        params = {
            "fields": json.dumps(safe_fields),
            "filters": json.dumps(filters),
            "order_by": "modified desc",
            "limit_page_length": 5000,
        }
        headers = {"Authorization": f"token {ERP_API_KEY}:{ERP_API_SECRET}"}

        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            r = await client.get(f"{ERP_URL}/api/resource/Item", headers=headers, params=params)

        if r.status_code != 200:
            logger.error("get_erpnext_items failed status=%s body=%s", r.status_code, r.text)
            return []

        data = r.json().get("data", []) or []
        logger.info("Fetched %d ERP Items", len(data))
        return data

    except Exception as e:
        logger.exception("get_erpnext_items crash: %s", e)
        return []

# --- Categories ------------------------------------------------------------

async def get_erpnext_categories() -> List[Dict[str, Any]]:
    """
    Fetch all item groups (categories) from ERPNext.
    Returns: list of {"name": ..., "parent_item_group": ...}
    """
    try:
        r = await _http_get('/api/resource/Item Group?fields=["name","parent_item_group"]')
        return r.json().get("data", []) if r.status_code == 200 else []
    except Exception as e:
        logger.error("Error fetching ERPNext categories: %s", e)
        return []

# --- Ping ------------------------------------------------------------------

async def erpnext_ping() -> Dict[str, Any]:
    """
    Checks if the ERPNext server is reachable and credentials work.
    Returns: { "success": bool, "status_code": int, "data": ... }
    """
    try:
        r = await _http_get("/api/method/ping", timeout=10.0)
        return {
            "success": r.status_code == 200,
            "status_code": r.status_code,
            "data": r.json() if r.content else None,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# --- Stock -----------------------------------------------------------------

async def get_stock_map() -> Dict[tuple, float]:
    """
    Returns a dict { (item_code, warehouse): actual_qty }
    """
    fields = ["item_code", "warehouse", "actual_qty"]
    params = {"fields": json.dumps(fields), "limit_page_length": 5000}
    r = await _http_get("/api/resource/Bin", params)
    stock_map: Dict[tuple, float] = {}
    if r.status_code == 200 and r.json().get("data"):
        for row in r.json()["data"]:
            key = (row.get("item_code"), row.get("warehouse"))
            if key[0] and key[1]:
                try:
                    stock_map[key] = float(row.get("actual_qty") or 0)
                except Exception:
                    stock_map[key] = 0.0
    return stock_map

# --- Images (per-item) -----------------------------------------------------

async def _get_featured_image(item_code: str) -> Optional[str]:
    """
    Reliably get Item.image via frappe.client.get_value (avoids needing full doc).
    """
    try:
        r = await _http_get(
            "/api/method/frappe.client.get_value",
            {"doctype": "Item", "fieldname": "image", "filters": json.dumps({"name": item_code})},
        )
        if r.status_code == 200:
            featured = ((r.json().get("message") or {}).get("image")) or None
            return featured
    except Exception as e:
        logger.debug("Featured image lookup failed for %s: %s", item_code, e)
    return None

async def _get_item_files(item_code: str) -> List[Dict[str, Any]]:
    """
    Get File rows attached to this Item (ordered by creation asc).
    """
    params = {
        "fields": json.dumps(["file_url", "attached_to_field", "attached_to_name"]),
        "filters": json.dumps([["attached_to_doctype", "=", "Item"], ["attached_to_name", "=", item_code]]),
        "order_by": "creation asc",
        "limit_page_length": 1000,
    }
    r = await _http_get("/api/resource/File", params)
    return r.json().get("data", []) if r.status_code == 200 else []

async def get_erp_images(item_or_code: str | Dict[str, Any]) -> Dict[str, Any]:
    """
    Return image info for a SINGLE ERPNext Item (simple or variant). This function
    does NOT attempt to find "common across siblings" — that logic belongs in the
    caller (e.g., safe_get_erp_gallery_for_sku).

    Returns:
        {
          "featured": "/files/....jpg" or None,
          "attachments": ["/files/....jpg", ...]   # excluding image/website_image attachments and the featured itself
        }

    NOTE: Callers may absolutize and/or compute sizes as needed.
    """
    # determine item_code
    if isinstance(item_or_code, dict):
        item_code = (
            item_or_code.get("item_code")
            or item_or_code.get("name")
            or item_or_code.get("Item Code")
            or item_or_code.get("sku")
        )
    else:
        item_code = str(item_or_code or "").strip()

    if not item_code:
        return {"featured": None, "attachments": []}

    # featured via API (most reliable)
    featured = await _get_featured_image(item_code)

    # all files on that Item
    files = await _get_item_files(item_code)

    # attachments = NOT explicitly attached to image/website_image AND not equal to featured
    attachments_rel = []
    for row in files:
        fu = row.get("file_url")
        if not fu:
            continue
        if row.get("attached_to_field") in ("image", "website_image"):
            continue
        if featured and fu == featured:
            continue
        attachments_rel.append(fu)

    # de-dupe while preserving order
    seen = set()
    attachments_rel_unique: List[str] = []
    for fu in attachments_rel:
        if fu not in seen:
            seen.add(fu)
            attachments_rel_unique.append(fu)

    # Return relative URLs (callers can absolutize), but we’ll also include absolute
    # to make downstream usage (e.g., WP upload) easier.
    return {
        "featured": featured,
        "attachments": attachments_rel_unique,
        "featured_abs": _abs_url(featured) if featured else None,
        "attachments_abs": [_abs_url(u) for u in attachments_rel_unique],
    }
