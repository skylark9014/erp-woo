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
    import logging
    logger = logging.getLogger("uvicorn.error")

    if not dirpath.exists():
        return []
    out: List[Dict[str, Any]] = []
    def extract_fields(obj):
        found = {"id": None, "customer": None, "total": None}
        def _set_if_valid(field, value):
            if found[field] is None and value not in (None, "", []):
                found[field] = value
        def _search(o):
            if not isinstance(o, dict):
                return
            # ID: Use customer id for customer payloads, order id for order payloads
            if o.get("resource") == "customer" or o.get("topic", "").startswith("customer"):
                cid = o.get("id")
                _set_if_valid("id", cid)
            elif o.get("resource") == "order" or o.get("topic", "").startswith("order"):
                oid = o.get("order_id") or o.get("id") or o.get("number")
                _set_if_valid("id", oid)
            # Customer name
            cust = o.get("customer") or o.get("billing") or o.get("shipping") or {}
            customer = None
            if isinstance(cust, dict):
                first = cust.get("first_name") or ""
                last = cust.get("last_name") or ""
                if first or last:
                    customer = f"{first} {last}".strip()
                else:
                    customer = cust.get("full_name") or cust.get("name") or cust.get("email")
            _set_if_valid("customer", customer)
            # Total
            total = o.get("total") or o.get("amount") or o.get("line_total")
            _set_if_valid("total", total)
            # Recurse
            for v in o.values():
                if isinstance(v, dict):
                    _search(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            _search(item)

        # Try to parse and search inside 'body_preview' if present
        import base64
        if isinstance(obj, dict):
            if 'body_preview' in obj and obj['body_preview']:
                try:
                    body_obj = json.loads(obj['body_preview'])
                    _search(body_obj)
                except Exception:
                    pass
            if not all(found.values()) and 'body_b64' in obj and obj['body_b64']:
                try:
                    decoded = base64.b64decode(obj['body_b64']).decode('utf-8')
                    body_obj = json.loads(decoded)
                    _search(body_obj)
                except Exception:
                    pass
        # Fallback: search the original object
        if not all(found.values()):
            _search(obj)
        return found["id"], found["customer"], found["total"]

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
        order_id = None
        customer = None
        total = None
        webshot_action = None
        try:
            with open(p, "r", encoding="utf-8") as f:
                payload = json.load(f)
            order_id, customer, total = extract_fields(payload)
            # Extract original webhook status for webshot_action
            webshot_action = None
            # Try to decode body_b64 if present
            if isinstance(payload, dict):
                raw_status = None
                if "body_b64" in payload and payload["body_b64"]:
                    import base64
                    try:
                        decoded = base64.b64decode(payload["body_b64"]).decode("utf-8")
                        decoded_json = json.loads(decoded)
                        raw_status = decoded_json.get("status")
                    except Exception:
                        pass
                # Fallback to top-level status if not found in decoded body
                if not raw_status:
                    raw_status = payload.get("status")
                webshot_action = raw_status
        except Exception:
            pass
        out.append({
            "name": p.name,
            "path": str(p),
            "mtime": int(st.st_mtime),
            "size": st.st_size,
            "status": status or {},
            "order_id": order_id,
            "customer": customer,
            "total": total,
            "webshot_action": webshot_action
        })

    return out

# List inbox files (raw)
@router.get("/inbox/list")
async def list_inbox(kind: str = Query("raw", description="Type of inbox to list: raw or orders")):
    if kind == "raw":
        base = BASE_RAW
    elif kind == "orders":
        base = BASE_ORD
    else:
        raise HTTPException(status_code=400, detail=f"Unknown kind: {kind}")
    out = _ls(base)
    return out

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
        logger.error(f"[INBOX][REPLAY] Path not under /code/data/inbox: {path}")
        raise HTTPException(status_code=400, detail="Path not under /code/data/inbox")
    if not p.exists():
        logger.error(f"[INBOX][REPLAY] File not found: {path}")
        raise HTTPException(status_code=404, detail="Not found")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"[INBOX][REPLAY] Invalid JSON in {path}: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")


    # If this is a wrapper (from get_inbox), unwrap to .json field
    if isinstance(payload, dict) and "json" in payload and isinstance(payload["json"], dict):
        logger.info(f"[INBOX][REPLAY] Unwrapping payload['json'] for handler")
        payload = payload["json"]
        logger.info(f"[INBOX][REPLAY] After unwrap, payload type: {type(payload).__name__}, keys: {list(payload.keys()) if isinstance(payload, dict) else 'n/a'}")

    # If payload is still missing resource/event, try decoding body_b64
    resource = payload.get("resource") if isinstance(payload, dict) else None
    event = payload.get("event") if isinstance(payload, dict) else None
    if resource is None and event is None and isinstance(payload, dict) and "body_b64" in payload:
        import base64
        decoded = base64.b64decode(payload["body_b64"])
        decoded_json = json.loads(decoded)
        logger.info(f"[INBOX][REPLAY] Decoded body_b64, keys: {list(decoded_json.keys()) if isinstance(decoded_json, dict) else 'n/a'}")
        logger.info(f"[INBOX][REPLAY] Decoded body_b64 full payload: {json.dumps(decoded_json, indent=2, ensure_ascii=False)}")
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

# Get raw JSON file content for Inbox 'View' button
@router.get("/inbox/get")
async def get_inbox_payload(path: str = Query(..., description="Absolute path under /code/data/inbox/woo_raw/*")):
    import logging
    logger = logging.getLogger("uvicorn.error")
    base = BASE_RAW
    p = Path(path)
    try:
        p.resolve().relative_to(base.resolve())
    except Exception:
        logger.error(f"[INBOX][GET] Path not under {base}: {path}")
        raise HTTPException(status_code=400, detail="Path not under inbox base")
    if not p.exists():
        logger.error(f"[INBOX][GET] File not found: {path}")
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"[INBOX][GET] Invalid JSON in {path}: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    return payload

# Set status for an inbox file (archive/unarchive)
@router.post("/inbox/set_status")
async def set_inbox_status(
    path: str = Query(..., description="Absolute path under /code/data/inbox/woo_raw/*"),
    status: str = Query(..., description="Status to set (archived, unarchived, etc.)")
):
    import logging
    logger = logging.getLogger("uvicorn.error")
    base = BASE_RAW
    p = Path(path)
    try:
        p.resolve().relative_to(base.resolve())
    except Exception:
        logger.error(f"[INBOX][SET_STATUS] Path not under {base}: {path}")
        raise HTTPException(status_code=400, detail="Path not under inbox base")
    if not p.exists():
        logger.error(f"[INBOX][SET_STATUS] File not found: {path}")
        raise HTTPException(status_code=404, detail="Not Found")
    meta_path = p.with_suffix('.status.json')
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"[INBOX][SET_STATUS] Failed to read {meta_path}: {e}")
            meta = {}
    meta["status"] = status
    import os
    tmp_file = str(meta_path) + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, meta_path)
        logger.info(f"[INBOX][SET_STATUS] Wrote status '{status}' to {meta_path}")
    except Exception as e:
        logger.error(f"[INBOX][SET_STATUS] Failed to write {meta_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to write status: {e}")
    return {"ok": True, "path": str(p), "status": status}