# app/mapping/customer_map_api.py
from __future__ import annotations
import json, time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from app.mapping.customer_map_store import (
    DEFAULT_PATH,
    load_map, save_map,
    get_entry, upsert_entry, delete_entry,
)

router = APIRouter(prefix="/api/integration/customers/map", tags=["Customer Map"])

def _stat_blob(p: Path) -> Dict[str, Any]:
    if not p.exists():
        return {"exists": False}
    st = p.stat()
    return {"exists": True, "mtime": int(st.st_mtime), "size": st.st_size}

@router.get("/file")
def get_customer_map_file():
    """
    Returns current map JSON (text + parsed) and metadata.
    """
    p = DEFAULT_PATH
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": 1, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "map": {}}, indent=2), encoding="utf-8")
    text = p.read_text(encoding="utf-8")
    parsed = None
    err = None
    try:
        parsed = json.loads(text)
    except Exception as e:
        err = str(e)
    return {"ok": True, "path": str(p), "content": text, "json": parsed, "parse_error": err, "stat": _stat_blob(p)}

class MapFileUpsert(BaseModel):
    content: Optional[str] = None
    data: Optional[Dict[str, Any]] = None  # entire JSON object (with "map" key)
    pretty: bool | None = True
    sort_keys: bool | None = True

@router.put("/file")
def put_customer_map_file(payload: MapFileUpsert = Body(...)):
    """
    Save the entire map file (validates JSON first).
    """
    p = DEFAULT_PATH
    if payload.data is None and payload.content is None:
        raise HTTPException(status_code=400, detail="Provide 'data' or 'content'")
    obj = payload.data
    if obj is None:
        try:
            obj = json.loads(payload.content or "")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    if not isinstance(obj, dict) or "map" not in obj:
        raise HTTPException(status_code=400, detail="JSON must contain a top-level 'map' field")

    indent = 2 if (payload.pretty is not False) else None
    try:
        text = json.dumps(obj, ensure_ascii=False, indent=indent, sort_keys=(payload.sort_keys is not False))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not serialize JSON: {e}")

    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)

    return {"ok": True, "path": str(p), "stat": _stat_blob(p)}

@router.get("/by-woo/{woo_id}")
def get_by_woo(woo_id: int):
    e = get_entry(woo_id)
    return {"found": bool(e), "entry": e}

class SingleUpsert(BaseModel):
    woo_id: int
    erp_customer: str
    email: Optional[str] = None

@router.post("/upsert")
def api_upsert_entry(payload: SingleUpsert):
    e = upsert_entry(payload.woo_id, payload.erp_customer, payload.email)
    return {"ok": True, "entry": e}

@router.delete("/by-woo/{woo_id}")
def api_delete_entry(woo_id: int):
    ok = delete_entry(woo_id)
    return {"ok": ok}
