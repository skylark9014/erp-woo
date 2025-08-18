# app/webhooks/inbox_api.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

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
        st = p.stat()
        out.append({"name": p.name, "path": str(p), "mtime": int(st.st_mtime), "size": st.st_size})
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
