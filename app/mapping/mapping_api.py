# app/mapping_api.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

# Environment-configurable path; default aligns with your repo layout
MAPPING_STORE_PATH = os.getenv("MAPPING_STORE_PATH", "app/mapping/mapping_store.json")


def read_text_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {
            "ok": True,
            "path": str(path),
            "valid": True,
            "error": None,
            "mtime": None,
            "size": 0,
            "content": "{}",
            "json": {},
        }

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return {
            "ok": False,
            "path": str(path),
            "valid": False,
            "error": f"Read error: {e}",
            "mtime": None,
            "size": None,
            "content": None,
            "json": None,
        }

    j: Optional[dict] = None
    err: Optional[str] = None
    try:
        j = json.loads(content) if content.strip() else {}
    except Exception as e:
        err = f"JSON parse error: {e}"

    stat = path.stat()
    return {
        "ok": True,
        "path": str(path),
        "valid": err is None,
        "error": err,
        "mtime": int(stat.st_mtime),
        "size": stat.st_size,
        "content": content,
        "json": j if err is None else None,
    }


def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


@router.get("/api/integration/mapping/store")
async def get_mapping_store():
    p = Path(MAPPING_STORE_PATH)
    out = read_text_file(p)
    return JSONResponse(content=out, status_code=200 if out.get("ok") else 500)


@router.post("/api/integration/mapping/store")
async def post_mapping_store(payload: Dict[str, Any]):
    """
    Accepts:
      {
        "content": string|null,  # if provided, write as-is
        "data": object|null,     # if provided (and content is null), json.dumps
        "pretty": bool,          # default True
        "sort_keys": bool        # default True
      }
    """
    p = Path(MAPPING_STORE_PATH)
    ensure_parent(p)

    content = payload.get("content", None)
    data = payload.get("data", None)
    pretty = payload.get("pretty", True)
    sort_keys = payload.get("sort_keys", True)

    if content is None:
        # build content from data
        if data is None:
            # nothing to write; just return current state
            out = read_text_file(p)
            return JSONResponse(content=out, status_code=200 if out.get("ok") else 500)
        try:
            content = json.dumps(
                data,
                indent=2 if pretty else None,
                ensure_ascii=False,
                sort_keys=bool(sort_keys),
            )
        except Exception as e:
            return JSONResponse(
                content={"ok": False, "error": f"Serialize error: {e}"},
                status_code=400,
            )

    try:
        p.write_text(content, encoding="utf-8")
    except Exception as e:
        return JSONResponse(
            content={"ok": False, "error": f"Write error: {e}"},
            status_code=500,
        )

    out = read_text_file(p)
    out["saved"] = True
    return JSONResponse(content=out, status_code=200 if out.get("ok") else 500)
