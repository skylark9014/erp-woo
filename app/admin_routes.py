#=======================================================================================
# app/admin_routes.py
# Admin endpoints. These are protected via Basic Auth in main_app.py
# and mounted under /admin, so final paths are /admin/api/*.
#
# NOTE:
# - We intentionally do NOT define public /api/sync/* here (those live in app.routes).
# - This module focuses on admin-only operations: health, mapping edit, preview-file ops,
#   delete runner, and optional admin-facing sync shims under /admin/api/sync/*.
#=======================================================================================

import logging
import httpx
import json, os, time, asyncio
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel
from fastapi import APIRouter, Request, Body, HTTPException
from fastapi.responses import JSONResponse

from app.mapping.mapping_store import build_or_load_mapping, save_mapping_file
from app.sync.product_sync import (
    sync_products_partial,
    sync_products_full,
    sync_preview,
)
from app.config import settings
from app.woo.woocommerce import purge_wc_bin_products  # optional purge support

logger = logging.getLogger("uvicorn.error")

# This router already has prefix="/api". In main_app we mount it with prefix="/admin",
# so final paths are /admin/api/*.
router = APIRouter(prefix="/api", tags=["Admin API"])

# ---------------------------
# Helpers
# ---------------------------
async def _safe_json(req: Request) -> Dict[str, Any]:
    """Best-effort JSON parse: handles empty bodies and bad content-types."""
    try:
        return await req.json()
    except Exception:
        try:
            raw = (await req.body()).decode("utf-8", "ignore")
            return json.loads(raw) if raw.strip() else {}
        except Exception:
            return {}

def _normalize_skus(payload: Dict[str, Any]) -> List[str]:
    """Accepts { skus:[] } | { sku:"..." } | { selection:[]|csv } | csv string."""
    raw = payload.get("skus", None)
    if raw is None:
        raw = payload.get("sku", None)
    if raw is None:
        raw = payload.get("selection", None)

    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    if isinstance(raw, str):
        # allow CSV or newline/semi-separated
        return [s.strip() for s in raw.replace("\n", ",").replace(";", ",").split(",") if s.strip()]
    return []

def _get_bool(payload: Dict[str, Any], *keys: str, default: bool = False) -> bool:
    for k in keys:
        if k in payload:
            return bool(payload.get(k))
    return default

# --------------------------------------------------------------------
# Admin Health (for UI) — verifies ERPNext + WP/Woo reachability
# --------------------------------------------------------------------

@router.get("/integration/health")
async def admin_integration_health():
    """
    Admin-only health check used by the UI. Verifies reachability of ERPNext and WP/Woo.
    Returns 200 with per-target status, does NOT require secrets to succeed.
    """
    erp_url = (settings.ERP_URL or "").rstrip("/")
    wc_base = (settings.WC_BASE_URL or "").rstrip("/")
    wp_url = (getattr(settings, "WP_API_URL", "") or f"{wc_base}/wp-json").rstrip("/")

    checks = {}

    async def _ok(resp: httpx.Response | None, *, allow_status: set[int]) -> dict:
        if resp is None:
            return {"ok": False, "status": None}
        return {"ok": resp.status_code in allow_status, "status": resp.status_code}

    timeout = httpx.Timeout(10.0, connect=10.0, read=10.0)
    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
        # ERPNext ping endpoint
        erp_resp = None
        try:
            if erp_url:
                erp_resp = await client.get(f"{erp_url}/api/method/ping")
        except Exception as e:
            logger.debug("ERPNext ping failed: %s", e)
        checks["erpnext"] = await _ok(erp_resp, allow_status={200})

        # WordPress REST root
        wp_resp = None
        try:
            if wp_url:
                wp_resp = await client.get(wp_url)
        except Exception as e:
            logger.debug("WordPress REST root failed: %s", e)
        checks["wordpress"] = await _ok(wp_resp, allow_status={200, 401})

        # WooCommerce REST namespace (presence implies plugin active)
        wc_resp = None
        try:
            if wp_url:
                wc_resp = await client.get(f"{wp_url}/wc/v3")
        except Exception as e:
            logger.debug("Woo REST check failed: %s", e)
        checks["woocommerce"] = await _ok(wc_resp, allow_status={200, 401, 403, 404})

    ok = all(v.get("ok") for v in checks.values())
    return {"ok": ok, "checks": checks, "base": {"erp": erp_url, "wp": wp_url, "wc": wc_base}}

# --------------------------------------------------------------------
# Mapping file — load/save
# --------------------------------------------------------------------

@router.get("/mapping")
def get_mapping():
    """Admin: Load mapping file."""
    try:
        mapping = build_or_load_mapping()
        return {"mapping": mapping}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load mapping: {str(e)}")

@router.post("/mapping")
def post_mapping(payload: dict = Body(...)):
    """Admin: Save mapping file."""
    try:
        mapping = payload.get("mapping")
        if not isinstance(mapping, list):
            raise ValueError("Mapping must be a list of dicts")
        save_mapping_file(mapping)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save mapping: {str(e)}")

# --------------------------------------------------------------------
# Admin-facing sync shims (optional; UI can also use public /api/sync/*)
# Final paths: /admin/api/sync/*
# --------------------------------------------------------------------

@router.get("/sync/preview")
async def http_sync_preview():
    result = await sync_preview()
    return JSONResponse(result)

@router.post("/sync/full")
async def http_sync_full(req: Request):
    body = await _safe_json(req)
    dry_run = _get_bool(body, "dry_run", "dryRun", default=False)
    purge_bin = _get_bool(body, "purge_bin", "purgeBin", default=True)
    result = await sync_products_full(dry_run=dry_run, purge_bin=purge_bin)
    result.setdefault("request", {"dry_run": dry_run, "purge_bin": purge_bin})
    return JSONResponse(result)

@router.post("/sync/partial")
async def http_sync_partial(req: Request):
    body = await _safe_json(req)
    skus = _normalize_skus(body)
    dry_run = _get_bool(body, "dry_run", "dryRun", default=False)

    result = await sync_products_partial(skus_to_sync=skus, dry_run=dry_run)
    # Echo selection for debugging/visibility
    result["selection"] = {"requested": skus, "count": len(skus), "dry_run": dry_run}
    return JSONResponse(result)

# --------------------------------------------------------------------
# Woo Deletes — move to Trash by default; optional hard delete with force=true
# Final path: /admin/api/deletes/run
# Body: { ids: number[] | string (CSV), force?: boolean, purgeBin?: boolean }
# --------------------------------------------------------------------

@router.post("/deletes/run")
async def admin_deletes_run(req: Request):
    body = await _safe_json(req)
    ids_raw = body.get("ids", [])
    # Accept CSV / string too
    if isinstance(ids_raw, str):
        ids_raw = [s.strip() for s in ids_raw.replace("\n", ",").replace(";", ",").split(",") if s.strip()]

    # Coerce to ints and de-dupe
    ids: List[int] = []
    for v in (ids_raw or []):
        try:
            i = int(str(v).strip())
            if i not in ids:
                ids.append(i)
        except Exception:
            continue

    if not ids:
        raise HTTPException(status_code=400, detail="Provide product 'ids' to delete (array or CSV).")

    force = _get_bool(body, "force", "hard", "permanent", default=False)
    purge_bin = _get_bool(body, "purge_bin", "purgeBin", default=False)

    wc_base = (settings.WC_BASE_URL or "").rstrip("/")
    wc_api = f"{wc_base}/wp-json/wc/v3"
    auth = (settings.WC_API_KEY, settings.WC_API_SECRET)

    async def _delete_one(pid: int) -> Dict[str, Any]:
        url = f"{wc_api}/products/{pid}"
        params = {"force": "true" if force else "false"}
        try:
            async with httpx.AsyncClient(timeout=30.0, verify=False, auth=auth) as client:
                r = await client.delete(url, params=params)
                data: Dict[str, Any] = {}
                try:
                    if r.headers.get("content-type", "").startswith("application/json"):
                        data = r.json() or {}
                except Exception:
                    data = {}
                return {
                    "id": pid,
                    "ok": r.status_code in (200, 201),
                    "status": r.status_code,
                    "force": force,
                    "response": data if data else r.text[:2000],
                }
        except Exception as e:
            logger.error("[DELETE] id=%s failed: %s", pid, e)
            return {"id": pid, "ok": False, "status": None, "force": force, "error": str(e)}

    results = await asyncio.gather(*(_delete_one(pid) for pid in ids))

    # Optional purge of Trash (only makes sense when force=False)
    purged = False
    if purge_bin and not force:
        try:
            await purge_wc_bin_products()
            purged = True
        except Exception as e:
            logger.warning("Purge bin failed: %s", e)

    summary = {
        "requested": ids,
        "count": len(ids),
        "force": force,
        "purge_bin": purge_bin,
        "purged": purged,
        "ok": all(r.get("ok") for r in results),
    }
    return JSONResponse({"summary": summary, "results": results})

# --------------------------------------------------------------------
# Misc Admin — placeholder
# --------------------------------------------------------------------

@router.get("/stock-adjustment")
def get_stock_adjustment():
    """Admin: Placeholder stock-adjustment endpoint (no-op)."""
    return JSONResponse(content={}, status_code=200)

# --------------------------------------------------------------------
# Shipping Parameters editor endpoints
# --------------------------------------------------------------------

def _shipping_params_path() -> Path:
    # Fallback to the known location if not configured in settings
    p = getattr(settings, "SHIPPING_PARAMS_PATH", "/app/mapping/shipping_params.json")
    return Path(p)

@router.get("/config/shipping/params")
async def get_shipping_params():
    """
    Return the current shipping_params.json content + metadata.
    Always returns text content; also includes parsed JSON when valid.
    """
    p = _shipping_params_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")

    text = p.read_text(encoding="utf-8")
    valid = True
    parsed = None
    error = None
    try:
        parsed = json.loads(text)
    except Exception as e:
        valid = False
        error = str(e)

    st = p.stat()
    return {
        "ok": True,
        "path": str(p),
        "valid": valid,
        "error": error,
        "mtime": int(st.st_mtime),
        "size": st.st_size,
        "content": text,
        "json": parsed,
    }

class ShippingParamsUpsert(BaseModel):
    # You can send either raw string "content" OR already-parsed "data".
    content: str | None = None
    data: Any | None = None
    pretty: bool | None = True
    sort_keys: bool | None = True

def _atomic_write(path: Path, data: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)  # atomic on POSIX

@router.put("/config/shipping/params")
async def put_shipping_params(payload: ShippingParamsUpsert = Body(...)):
    """
    Save new shipping_params.json. Validates JSON first, writes atomically,
    and creates a timestamped .bak of the previous file if present.
    """
    p = _shipping_params_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    # Determine the object to store
    if payload.content is None and payload.data is None:
        raise HTTPException(status_code=400, detail="Provide either 'content' (string) or 'data' (object).")

    if payload.data is None:
        # Parse the provided text as JSON
        try:
            obj = json.loads(payload.content or "")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    else:
        obj = payload.data

    # Serialize (pretty by default)
    indent = 2 if (payload.pretty is not False) else None
    try:
        new_text = json.dumps(obj, ensure_ascii=False, indent=indent, sort_keys=(payload.sort_keys is not False))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not serialize JSON: {e}")

    # Backup existing file
    if p.exists():
        ts = time.strftime("%Y%m%d-%H%M%S")
        bak = p.with_suffix(p.suffix + f".{ts}.bak")
        try:
            bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            # Non-fatal; continue save even if backup fails
            pass

    # Atomic write
    _atomic_write(p, new_text)

    st = p.stat()
    return {
        "ok": True,
        "path": str(p),
        "mtime": int(st.st_mtime),
        "size": st.st_size,
        "content": new_text,
    }

# --------------------------------------------------------------------
# Admin: Woo deletes (soft-delete to Trash by default)
# --------------------------------------------------------------------
from pydantic import BaseModel

class DeleteRunReq(BaseModel):
    ids: List[int]
    force: bool | None = False        # false = move to Trash (safe), true = hard delete
    purge_bin: bool | None = False    # reserved (not used here)

@router.get("/deletes/preview")
async def http_deletes_preview():
    """
    Placeholder delete preview — UI primarily surfaces delete candidates
    from the main sync preview's `sync_report.to_delete`.
    """
    return JSONResponse({"ok": True, "candidates": []})

@router.post("/deletes/run")
async def http_deletes_run(payload: DeleteRunReq = Body(...)):
    """
    Delete (or trash) WooCommerce products by numeric ID.
    Uses Woo REST auth via consumer_key/consumer_secret.
    """
    base = (settings.WC_BASE_URL or "").rstrip("/")
    key = getattr(settings, "WC_API_KEY", None)
    secret = getattr(settings, "WC_API_SECRET", None)
    if not base or not key or not secret:
        raise HTTPException(status_code=400, detail="WooCommerce base URL or credentials missing.")

    results = []
    qp = {"consumer_key": key, "consumer_secret": secret}
    force = bool(payload.force)
    timeout = httpx.Timeout(30.0, connect=10.0, read=30.0)

    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
        for pid in payload.ids or []:
            try:
                url = f"{base}/wp-json/wc/v3/products/{int(pid)}"
                r = await client.delete(url, params={**qp, "force": str(force).lower()})
                try:
                    data = r.json()
                except Exception:
                    data = {"text": r.text}
                results.append({
                    "id": int(pid),
                    "status": r.status_code,
                    "ok": 200 <= r.status_code < 300,
                    "data": data,
                })
            except Exception as e:
                results.append({"id": int(pid), "status": None, "ok": False, "error": str(e)})

    return JSONResponse({"ok": all(x.get("ok") for x in results), "results": results})
