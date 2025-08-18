# app/mapping/customer_map_store.py
from __future__ import annotations
import json, time
from pathlib import Path
from typing import Dict, Optional, TypedDict, Any

try:
    # Optional; if missing, we’ll use a sane default path below.
    from app.config import settings
    _CFG_PATH = getattr(settings, "CUSTOMER_MAP_PATH", "").strip()
except Exception:
    _CFG_PATH = ""

# Keep file alongside other mapping JSON files.
DEFAULT_PATH = Path(_CFG_PATH or "/code/app/mapping/customer_map.json")

class CustomerMapEntry(TypedDict, total=False):
    woo_id: int
    erp_customer: str
    email: str | None
    updated_at: str

def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _blank() -> Dict[str, Any]:
    return {"version": 1, "updated": _now_iso(), "map": {}}

def _load_raw(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _blank()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _blank()

def _save_raw(path: Path, obj: Dict[str, Any]) -> None:
    obj = dict(obj or {})
    obj["updated"] = _now_iso()
    _ensure_parent(path)
    _atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2))

# -------- Public API --------

def load_map(path: Path = DEFAULT_PATH) -> Dict[str, CustomerMapEntry]:
    raw = _load_raw(path)
    m = raw.get("map") or {}
    # normalize keys to strings
    out: Dict[str, CustomerMapEntry] = {}
    for k, v in m.items():
        out[str(k)] = {
            "woo_id": int(v.get("woo_id") or int(k)),
            "erp_customer": str(v.get("erp_customer") or "").strip(),
            "email": (v.get("email") or None),
            "updated_at": str(v.get("updated_at") or ""),
        }
    return out

def save_map(map_obj: Dict[str, CustomerMapEntry], path: Path = DEFAULT_PATH) -> None:
    raw = {"version": 1, "updated": _now_iso(), "map": map_obj or {}}
    _save_raw(path, raw)

def get_entry(woo_id: int, path: Path = DEFAULT_PATH) -> Optional[CustomerMapEntry]:
    m = load_map(path)
    return m.get(str(int(woo_id)))

def upsert_entry(woo_id: int, erp_customer: str, email: str | None = None,
                 path: Path = DEFAULT_PATH) -> CustomerMapEntry:
    m = load_map(path)
    key = str(int(woo_id))
    entry: CustomerMapEntry = {
        "woo_id": int(woo_id),
        "erp_customer": erp_customer.strip(),
        "email": (email or None),
        "updated_at": _now_iso(),
    }
    m[key] = entry
    save_map(m, path)
    return entry

def delete_entry(woo_id: int, path: Path = DEFAULT_PATH) -> bool:
    m = load_map(path)
    key = str(int(woo_id))
    if key in m:
        m.pop(key, None)
        save_map(m, path)
        return True
    return False
