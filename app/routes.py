#=======================================================================================
# app/routes.py
# FastAPI routes for ERPNext ↔ WooCommerce sync, utilities, and legacy compatibility.
#
# ✅ Canonical public API lives under /api/*
# ✅ NEW PIPELINE endpoints (product_sync.py) require HTTP Basic (admin)
#
# IMPORTANT: In main_app.py, include with NO extra prefix to avoid /api/api duplication:
#   from app.routes import router as api_router
#   app.include_router(api_router)   # <-- no prefix here
#=======================================================================================

import json
import httpx
import secrets
import asyncio
import uuid
import time
from typing import Any, Dict, List
import logging

from fastapi import APIRouter, Query, Request, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from app.config import settings

# NEW pipeline (keep)
from app.sync.product_sync import (
    sync_products_full,     # full sync with categories, prices, brand, variants, images
    sync_products_partial,  # partial sync by SKUs
    sync_preview,           # dry-run preview of new pipeline
)

# Utilities
from app.woo.woocommerce import (
    purge_wc_bin_products,
    purge_all_wc_products,
    purge_wc_product_variations,
    list_wc_bin_products,
)
from app.erp.erpnext import erpnext_ping

router = APIRouter(prefix="/api", tags=["Sync API"])
compat_router = APIRouter(tags=["Sync API (Compat)"])  # root-level aliases (/sync/*)

# ---------------------------
# HTTP Basic for NEW pipeline
# ---------------------------
security = HTTPBasic()

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username or "", settings.ADMIN_USER or "")
    ok_pass = secrets.compare_digest(credentials.password or "", settings.ADMIN_PASS or "")
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )

# ---------------------------
# Helpers
# ---------------------------
async def _safe_json(req: Request) -> Dict[str, Any]:
    """Best-effort JSON body parsing with fallbacks."""
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
        # allow CSV or whitespace-delimited
        return [s.strip() for s in raw.replace("\n", ",").replace(";", ",").split(",") if s.strip()]

    # Nothing usable
    return []

def _get_bool(payload: Dict[str, Any], *keys: str, default: bool = False) -> bool:
    for k in keys:
        if k in payload:
            return bool(payload.get(k))
    return default

def _now_ts() -> int:
    return int(time.time())

# ---------------------------
# Background job store (in-memory)
# ---------------------------
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = asyncio.Lock()
_JOBS_TTL_SECONDS = 60 * 60  # keep finished jobs 1 hour

async def _cleanup_jobs_now():
    """Remove finished jobs older than TTL."""
    cutoff = _now_ts() - _JOBS_TTL_SECONDS
    async with _JOBS_LOCK:
        to_del = [jid for jid, rec in _JOBS.items()
                  if rec.get("finished") and rec.get("finished") < cutoff]
        for jid in to_del:
            _JOBS.pop(jid, None)

async def _run_full_job(job_id: str, *, dry_run: bool, purge_bin: bool):
    """Background runner for full sync."""
    logger.info(f"[JOB][RUN] Job {job_id} starting (dry_run={dry_run}, purge_bin={purge_bin})")
    async with _JOBS_LOCK:
        rec = _JOBS.get(job_id) or {}
        rec.update({
            "status": "running",
            "started": rec.get("started") or _now_ts(),
        })
        _JOBS[job_id] = rec
        logger.debug(f"[JOB][RUN] Job {job_id} status set to running")

    try:
        result = await sync_products_full(dry_run=dry_run, purge_bin=purge_bin)
        async with _JOBS_LOCK:
            _JOBS[job_id].update({
                "status": "done",
                "finished": _now_ts(),
                "result": result,
            })
            logger.info(f"[JOB][COMPLETE] Job {job_id} finished successfully")
    except Exception as e:
        async with _JOBS_LOCK:
            _JOBS[job_id].update({
                "status": "error",
                "finished": _now_ts(),
                "error": str(e),
            })
            logger.error(f"[JOB][ERROR] Job {job_id} failed: {e}")

    # opportunistic cleanup
    logger.debug(f"[JOB][CLEANUP] Running job cleanup after job {job_id}")
    await _cleanup_jobs_now()

# ----------------------------------------------------------------------
# NEW PIPELINE (product_sync.py) — ✅ KEEP (now supports async jobs)
# ----------------------------------------------------------------------

@router.api_route("/sync/preview", methods=["GET", "POST"], dependencies=[Depends(verify_admin)])
async def api_sync_preview():
    """
    Dry-run preview of ERPNext → Woo sync (admin-only).
    """
    result = await sync_preview()
    return JSONResponse(content=result)

@router.post("/sync/full", dependencies=[Depends(verify_admin)])
async def api_sync_full(request: Request):
    """
    Full ERPNext → Woo sync (admin-only).

    Body:
      {
        "dry_run" | "dryRun": bool,
        "purge_bin" | "purgeBin": bool (default True),
        "blocking": bool (default False)  # when true, run synchronously (legacy behavior)
      }

    Default is **non-blocking**: returns { job_id, status } immediately (202 Accepted),
    use GET /api/sync/status/{job_id} to poll until "done" or "error".
    """
    payload = await _safe_json(request)
    dry_run = _get_bool(payload, "dry_run", "dryRun", default=False)
    purge_bin = _get_bool(payload, "purge_bin", "purgeBin", default=True)
    blocking = _get_bool(payload, "blocking", default=False)

    if blocking:
        # Legacy "wait-for-result" behavior
        logger.info("[JOB][SYNC] Starting blocking sync job (legacy mode)")
        result = await sync_products_full(dry_run=dry_run, purge_bin=purge_bin)
        result.setdefault("request", {"dry_run": dry_run, "purge_bin": purge_bin, "blocking": True})
        logger.info("[JOB][SYNC] Blocking sync job finished")
        return JSONResponse(content=result)

    # Non-blocking background job
    job_id = uuid.uuid4().hex
    logger.info(f"[JOB][REGISTER] Registering new job: {job_id} (dry_run={dry_run}, purge_bin={purge_bin})")
    async with _JOBS_LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "started": None,
            "finished": None,
            "request": {"dry_run": dry_run, "purge_bin": purge_bin},
        }
        logger.debug(f"[JOB][REGISTER] Job {job_id} added to _JOBS store")

    # Fire and forget
    logger.info(f"[JOB][RUN] Launching background job: {job_id}")
    asyncio.create_task(_run_full_job(job_id, dry_run=dry_run, purge_bin=purge_bin))

    # 202 Accepted + Location to status endpoint
    logger.info(f"[JOB][RESPONSE] Returning job_id {job_id} to client (status=queued)")
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "status": "queued"},
        headers={"Location": f"/api/sync/status/{job_id}"},
    )


# ----------------------------------------------------------------------
# List all jobs (admin-only)
# ----------------------------------------------------------------------
@router.get("/sync/jobs", dependencies=[Depends(verify_admin)])
async def api_sync_jobs():
    """Return all jobs in the background job store (admin-only)."""
    async with _JOBS_LOCK:
        jobs = list(_JOBS.values())
    # Sort by started time (descending), fallback to job_id if missing
    jobs.sort(key=lambda j: -(j.get("started") or 0) if j.get("started") else j.get("id", ""), reverse=True)
    return JSONResponse(content={"jobs": jobs})


@router.get("/sync/status/{job_id}", dependencies=[Depends(verify_admin)])
async def api_sync_status(job_id: str):
    """Poll background full-sync job status."""
    async with _JOBS_LOCK:
        rec = _JOBS.get(job_id)
    if not rec:
        raise HTTPException(status_code=404, detail="job not found")
    # return full record (contains result only when status == done)
    return JSONResponse(content=rec)

# ----------------------------------------------------------------------
# Retry a job by job_id (admin-only)
# ----------------------------------------------------------------------
@router.post("/sync/retry/{job_id}", dependencies=[Depends(verify_admin)])
async def api_sync_retry(job_id: str):
    """Retry a job by re-queuing it with the same parameters."""
    async with _JOBS_LOCK:
        rec = _JOBS.get(job_id)
        if not rec:
            raise HTTPException(status_code=404, detail="job not found")
        # Only allow retry for jobs that are done or errored
        if rec.get("status") not in ("done", "error"):
            raise HTTPException(status_code=400, detail="Job not finished or errored")
        # Extract original parameters
        params = rec.get("request", {})
        dry_run = params.get("dry_run", False)
        purge_bin = params.get("purge_bin", True)
    # Create new job
    new_job_id = uuid.uuid4().hex
    async with _JOBS_LOCK:
        _JOBS[new_job_id] = {
            "id": new_job_id,
            "status": "queued",
            "started": None,
            "finished": None,
            "request": {"dry_run": dry_run, "purge_bin": purge_bin, "retry_of": job_id},
        }
    asyncio.create_task(_run_full_job(new_job_id, dry_run=dry_run, purge_bin=purge_bin))
    return JSONResponse(content={"ok": True, "job_id": new_job_id, "retry_of": job_id})

@router.post("/sync/partial", dependencies=[Depends(verify_admin)])
async def api_sync_partial(request: Request):
    """
    Partial sync by SKUs (admin-only).
    Body: { "skus": [ "SKU1", ... ] | "sku": "CSV or single", "dry_run" | "dryRun": bool }
    """
    payload = await _safe_json(request)
    skus = _normalize_skus(payload)
    dry_run = _get_bool(payload, "dry_run", "dryRun", default=False)

    result = await sync_products_partial(skus_to_sync=skus, dry_run=dry_run)
    # echo selection (non-breaking; useful for UI/diagnostics)
    result["selection"] = {
        "requested": skus,
        "count": len(skus),
        "dry_run": dry_run,
    }
    return JSONResponse(content=result)

# ----------------------------------------------------------------------
# COMPATIBILITY ALIASES (root-level) — same handlers & auth
# These let the UI call /sync/* instead of /api/sync/*
# ----------------------------------------------------------------------

@compat_router.api_route("/sync/preview", methods=["GET", "POST"], dependencies=[Depends(verify_admin)])
async def compat_sync_preview():
    return await api_sync_preview()

@compat_router.post("/sync/full", dependencies=[Depends(verify_admin)])
async def compat_sync_full(request: Request):
    return await api_sync_full(request)

@compat_router.get("/sync/status/{job_id}", dependencies=[Depends(verify_admin)])
async def compat_sync_status(job_id: str):
    return await api_sync_status(job_id)

@compat_router.post("/sync/partial", dependencies=[Depends(verify_admin)])
async def compat_sync_partial(request: Request):
    return await api_sync_partial(request)

# ----------------------------------------------------------------------
# Aliases for /api/integration/* — forward to existing sync endpoints
# ----------------------------------------------------------------------

@router.api_route("/integration/preview", methods=["GET", "POST"], dependencies=[Depends(verify_admin)])
async def api_integration_preview():
    return await api_sync_preview()

@router.post("/integration/full", dependencies=[Depends(verify_admin)])
async def api_integration_full(request: Request):
    return await api_sync_full(request)

@router.post("/integration/partial", dependencies=[Depends(verify_admin)])
async def api_integration_partial(request: Request):
    return await api_sync_partial(request)

@router.get("/integration/status/{job_id}", dependencies=[Depends(verify_admin)])
async def api_integration_status(job_id: str):
    return await api_sync_status(job_id)
    
# ----------------------------------------------------------------------
# WooCommerce utilities — ✅ KEEP
# ----------------------------------------------------------------------

@router.post("/woocommerce/purge-bin")
async def purge_woocommerce_bin():
    """Utility: Purge (force-delete) all WooCommerce products in the BIN (Trash)."""
    return await purge_wc_bin_products()

@router.post("/woocommerce/purge-all")
async def purge_woocommerce_all():
    """Utility: Permanently delete ALL WooCommerce products. ⚠️ Use with caution!"""
    return await purge_all_wc_products()

@router.post("/woocommerce/purge-variations")
async def purge_woocommerce_variations(product_id: int = Query(...)):
    """Utility: Delete all variations for a given WooCommerce product (by product_id)."""
    return await purge_wc_product_variations(product_id)

@router.get("/woocommerce/list-bin")
async def list_woocommerce_bin():
    """Utility: List all WooCommerce products currently in the BIN (Trash)."""
    return await list_wc_bin_products()

# ----------------------------------------------------------------------
# ERPNext healthcheck — ✅ KEEP
# ----------------------------------------------------------------------

@router.get("/erpnext/ping")
async def ping_erpnext():
    """Healthcheck: ERPNext credentials + API reachability."""
    return await erpnext_ping()

@router.get("/health")
async def api_health():
    """
    Health check used by the Admin UI:
    - ERPNext: GET {ERP_URL}/api/method/ping   (expects 200)
    - WordPress: GET {WP_API_URL or WC_BASE_URL/wp-json}   (expects 200)
    - Optionally pings Woo REST root to confirm reachability (auth may not be required)
    """
    erp_url = (getattr(settings, "ERP_URL", "") or "").strip()
    wc_base = (getattr(settings, "WC_BASE_URL", "") or "").strip()
    wp_api = (getattr(settings, "WP_API_URL", None) or (wc_base.rstrip("/") + "/wp-json") if wc_base else None)

    result = {
        "ok": True,
        "integration": {"ok": True},
        "erpnext": {},
        "woocommerce": {},
    }

    async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
        # ---- ERPNext ping ----
        if not erp_url:
            result["erpnext"] = {"ok": False, "error": "ERP_URL not set"}
            result["ok"] = False
        else:
            erp_ping_url = erp_url.rstrip("/") + "/api/method/ping"
            try:
                r = await client.get(erp_ping_url)
                erp_ok = (r.status_code == 200)
                result["erpnext"] = {
                    "ok": erp_ok,
                    "status": r.status_code,
                    "url": erp_ping_url,
                }
                result["ok"] = result["ok"] and erp_ok
            except Exception as e:
                result["erpnext"] = {"ok": False, "error": str(e), "url": erp_ping_url}
                result["ok"] = False

        # ---- WordPress / Woo reachability ----
        if not wp_api:
            result["woocommerce"] = {"ok": False, "error": "WP_API_URL and WC_BASE_URL not set"}
            result["ok"] = False
        else:
            try:
                r = await client.get(wp_api.rstrip("/"))
                wp_ok = (r.status_code == 200)
                result["woocommerce"] = {
                    "ok": wp_ok,
                    "status": r.status_code,
                    "url": wp_api.rstrip("/"),
                }
                # Optional: ping Woo REST root (no auth required to just confirm route)
                if wc_base:
                    try:
                        r2 = await client.get(wc_base.rstrip("/") + "/wp-json/wc/v3")
                        result["woocommerce"]["rest_status"] = r2.status_code
                    except Exception as ee:
                        result["woocommerce"]["rest_status"] = None
                        result["woocommerce"]["rest_error"] = str(ee)
                result["ok"] = result["ok"] and wp_ok
            except Exception as e:
                result["woocommerce"] = {"ok": False, "error": str(e), "url": wp_api}
                result["ok"] = False

    return JSONResponse(content=result)
