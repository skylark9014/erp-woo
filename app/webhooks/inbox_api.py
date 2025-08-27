# app/webhooks/inbox_api.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
import asyncio

try:
    from app.config import settings
    _RAW_BASE = getattr(settings, "WOO_INBOX_BASE", "").strip()
except Exception:
    _RAW_BASE = ""

BASE_RAW = Path(_RAW_BASE or "/code/data/inbox/woo_raw")
BASE_ORD = Path("/code/data/inbox/woo_orders")  # worker output (audit)

router = APIRouter(prefix="/api/integration/webhooks", tags=["Webhooks Admin"])

def _ls(dirpath: Path) -> List[Dict[str, Any]]:
    if not dirpath.exists():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(dirpath.glob("*.json")):
        # Skip .status.json files
        if p.name.endswith('.status.json'):
            continue
        st = p.stat()
        meta_path = p.with_suffix('.status.json')
        status = None
        if meta_path.exists():
            try:
                status = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                status = None
        out.append({
            "name": p.name,
            "path": str(p),
            "mtime": int(st.st_mtime),
            "size": st.st_size,
            "status": status or {}
        })
    return out

@router.get("/inbox/list")
def list_inbox(kind: Optional[str] = Query(None, description="raw|orders|all")):
    kind = (kind or "all").lower()
    if kind == "raw":
        return {"raw": _ls(BASE_RAW)}
    if kind == "orders":
        return {"orders": _ls(BASE_ORD)}
    return {"raw": _ls(BASE_RAW), "orders": _ls(BASE_ORD)}

@router.get("/inbox/get")
def get_inbox(path: str = Query(..., description="Absolute path under /code/data/inbox/*")):
    p = Path(path)
    root = Path("/code/data/inbox")
    try:
        p.resolve().relative_to(root.resolve())
    except Exception:
        raise HTTPException(status_code=400, detail="Path not under /code/data/inbox")
    if not p.exists():
        raise HTTPException(status_code=404, detail="Not found")
    text = p.read_text(encoding="utf-8")
    obj = None
    try:
        obj = json.loads(text)
    except Exception:
        obj = None
    return {"path": str(p), "content": text, "json": obj}


# ----------------------------------------------------------------------
@router.post("/inbox/set_status")
def set_inbox_status(path: str = Query(..., description="Absolute path under /code/data/inbox/*"), status: str = Query(..., description="Status label")):
    p = Path(path)
    root = Path("/code/data/inbox")
    try:
        p.resolve().relative_to(root.resolve())
    except Exception:
        raise HTTPException(status_code=400, detail="Path not under /code/data/inbox")
    if not p.exists():
        raise HTTPException(status_code=404, detail="Not found")
    import logging
    logger = logging.getLogger("uvicorn.error")
    meta_path = p.with_suffix('.status.json')
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            logger.error(f"[STATUS] Failed to read {meta_path}: {e}")
            meta = {}
    import os
    meta["status"] = status
    tmp_file = str(meta_path) + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, meta_path)
        logger.info(f"[STATUS] Wrote status '{status}' to {meta_path}")
    except Exception as e:
        logger.error(f"[STATUS] Failed to write {meta_path}: {e}")
    return {"ok": True, "path": str(p), "status": status}
# Replay archived webhook payload (internal re-processing)
# ----------------------------------------------------------------------
@router.post("/inbox/replay")
async def replay_inbox(path: str = Query(..., description="Absolute path under /code/data/inbox/*")):
    """Re-process an archived webhook payload as if it just arrived."""
    import logging
    logger = logging.getLogger("uvicorn.error")
    from app.woo_handlers import handle_woo_webhook
    p = Path(path)
    root = Path("/code/data/inbox")
    try:
        p.resolve().relative_to(root.resolve())
    except Exception:
        #logger.error(f"[INBOX][REPLAY] Path not under /code/data/inbox: {path}")
        raise HTTPException(status_code=400, detail="Path not under /code/data/inbox")
    if not p.exists():
        #logger.error(f"[INBOX][REPLAY] File not found: {path}")
        raise HTTPException(status_code=404, detail="Not found")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        #logger.error(f"[INBOX][REPLAY] Invalid JSON in {path}: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")


    # If this is a wrapper (from get_inbox), unwrap to .json field
    if isinstance(payload, dict) and "json" in payload and isinstance(payload["json"], dict):
        #logger.info(f"[INBOX][REPLAY] Unwrapping payload['json'] for handler")
        payload = payload["json"]
        #logger.info(f"[INBOX][REPLAY] After unwrap, payload type: {type(payload).__name__}, keys: {list(payload.keys()) if isinstance(payload, dict) else 'N/A'}")

    # If payload is still missing resource/event, try decoding body_b64
    resource = payload.get("resource") if isinstance(payload, dict) else None
    event = payload.get("event") if isinstance(payload, dict) else None
    if resource is None and event is None and isinstance(payload, dict) and "body_b64" in payload:
        import base64
        decoded = base64.b64decode(payload["body_b64"])
        decoded_json = json.loads(decoded)
        #logger.info(f"[INBOX][REPLAY] Decoded body_b64, keys: {list(decoded_json.keys()) if isinstance(decoded_json, dict) else 'N/A'}")
        #logger.info(f"[INBOX][REPLAY] Decoded body_b64 full payload: {json.dumps(decoded_json, indent=2, ensure_ascii=False)}")
        payload = decoded_json
        resource = payload.get("resource") if isinstance(payload, dict) else None
        event = payload.get("event") if isinstance(payload, dict) else None
        
    # removed verbose replay log

    # Call the internal handler (async) and check result
    try:
        result = await handle_woo_webhook(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Handler error: {e}")
    # If replay succeeded, mark status as completed
    import logging
    logger = logging.getLogger("uvicorn.error")
    meta_path = p.with_suffix('.status.json')
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"[STATUS] Failed to read {meta_path}: {e}")
            meta = {}
    logger.info(f"[STATUS][DEBUG] replay_inbox result: {result!r}")
    import os
    if result is not None:
        if isinstance(result, dict) and not result.get("success", False):
            meta["status"] = "failed"
            meta["error"] = result.get("error", "Unknown error")
            logger.info(f"[STATUS][DEBUG] Attempting to write status 'failed' to {meta_path}")
        else:
            meta["status"] = "completed"
            meta.pop("error", None)
            logger.info(f"[STATUS][DEBUG] Attempting to write status 'completed' to {meta_path}")
        tmp_file = str(meta_path) + ".tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
            os.replace(tmp_file, meta_path)
            logger.info(f"[STATUS] Wrote status '{meta['status']}' to {meta_path}")
        except Exception as e:
            logger.error(f"[STATUS] Failed to write {meta_path}: {e}")
    return {"ok": True, "path": str(p)}
