#=================================================================
# app/shipping_api.py
# Shipping parameters + sync endpoints used by Admin UI
# Exposed at /api/integration/shipping/*
#=================================================================

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
import logging

from app.config import settings

log = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/shipping", tags=["shipping"])

ADMIN_USER = settings.ADMIN_USER
ADMIN_PASS = settings.ADMIN_PASS
security = HTTPBasic()

def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username, ADMIN_USER)
    ok_pass = secrets.compare_digest(credentials.password, ADMIN_PASS)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

def _configured_path() -> str:
    return getattr(settings, "SHIPPING_PARAMS_PATH", None) or os.getenv(
        "SHIPPING_PARAMS_PATH", "app/mapping/shipping_params.json"
    )

def _resolve_path(p: Optional[str] = None) -> Path:
    rel = p or _configured_path()
    path = Path(rel)
    return path if path.is_absolute() else Path.cwd() / rel

class SavePayload(BaseModel):
    content: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    pretty: bool = True
    sort_keys: bool = True

def _safe_parse(s: str) -> Dict[str, Any]:
    try:
        obj = json.loads(s) if s else {}
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

def _file_info(p: Path, content: str, parsed: Dict[str, Any], error: Optional[str]) -> Dict[str, Any]:
    try:
        st = p.stat()
        mtime = int(st.st_mtime)
        size = int(st.st_size)
    except FileNotFoundError:
        mtime = 0
        size = len(content.encode("utf-8")) if content else 0

    return {
        "ok": error is None,
        "path": _configured_path(),
        "valid": error is None,
        "error": error,
        "mtime": mtime,
        "size": size,
        "content": content,
        "json": parsed,
    }

def _preview(s: str, n=240) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s[:n] + ("…" if len(s) > n else "")

@router.get("/params")
def get_params(_: HTTPBasicCredentials = Depends(require_admin)):
    path = _resolve_path()
    log.info("GET /shipping/params | resolved=%s exists=%s", str(path), path.exists())

    if not path.exists():
        empty = "{}"
        log.info("GET /shipping/params | file missing, returning empty JSON (size=%d)", len(empty))
        return _file_info(path, empty, {}, error=None)

    try:
        text = path.read_text("utf-8")
        log.info(
            "GET /shipping/params | read size=%d preview=%r",
            len(text.encode("utf-8")),
            _preview(text),
        )
    except Exception as e:
        log.error("GET /shipping/params | read failed: %s", e)
        return _file_info(path, "", {}, error=f"Read failed: {e}")

    try:
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            parsed = {}
        log.info("GET /shipping/params | parsed keys=%d", len(parsed.keys()))
    except Exception as e:
        log.error("GET /shipping/params | JSON parse error: %s", e)
        return _file_info(path, text, {}, error=f"JSON parse error: {e}")

    return _file_info(path, text, parsed, error=None)

@router.post("/params")
def save_params(payload: SavePayload, _: HTTPBasicCredentials = Depends(require_admin)):
    path = _resolve_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    log.info(
        "POST /shipping/params | resolved=%s | using %s | pretty=%s sort_keys=%s",
        str(path),
        "data" if payload.data is not None else "content",
        payload.pretty,
        payload.sort_keys,
    )

    if payload.data is not None:
        try:
            text = json.dumps(
                payload.data,
                indent=2 if payload.pretty else None,
                sort_keys=bool(payload.sort_keys),
                ensure_ascii=False,
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to serialize data: {e}")
    else:
        raw = payload.content or ""
        parsed = _safe_parse(raw)
        try:
            text = json.dumps(
                parsed,
                indent=2 if payload.pretty else None,
                sort_keys=bool(payload.sort_keys),
                ensure_ascii=False,
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON content: {e}")

    try:
        path.write_text(text, encoding="utf-8")
        log.info("POST /shipping/params | wrote size=%d", len(text.encode("utf-8")))
    except Exception as e:
        log.error("POST /shipping/params | write failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Write failed: {e}")

    saved_text = path.read_text("utf-8")
    parsed = _safe_parse(saved_text)
    info = _file_info(path, saved_text, parsed, error=None)
    info["saved"] = True
    log.info(
        "POST /shipping/params | saved ok | size=%d keys=%d preview=%r",
        info["size"],
        len(parsed.keys()),
        _preview(saved_text),
    )
    return info

@router.post("/sync")
def sync_shipping(body: Dict[str, Any] = None, _: HTTPBasicCredentials = Depends(require_admin)):
    opts = body or {}
    log.info("POST /shipping/sync | dry_run=%s", bool(opts.get("dry_run", False)))
    return {
        "ok": True,
        "accepted": True,
        "dry_run": bool(opts.get("dry_run", False)),
        "message": "Shipping sync accepted (stub). Implement actual sync logic here.",
        "ts": int(time.time()),
    }
