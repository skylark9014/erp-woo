# app/webhooks/archive.py
from __future__ import annotations
import base64, json, time
from pathlib import Path
from typing import Mapping, Any

try:
    from app.config import settings
    _BASE = getattr(settings, "WOO_INBOX_BASE", "").strip()
except Exception:
    _BASE = ""

# Stored under /code/data/inbox/... so it appears on host at ./data/inbox/...
BASE_DIR = Path(_BASE or "/code/data/inbox/woo_raw")

def _redact(headers: Mapping[str, str]) -> dict[str, str]:
    out = {}
    for k, v in headers.items():
        out[k] = "<redacted>" if k.lower() == "x-wc-webhook-signature" else v
    return out

def _ensure() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)

def archive_ingress(kind: str, headers: Mapping[str, str], body: bytes, *,
                    delivery_id: str | None, topic: str | None) -> str:
    """
    Persist raw webhook (headers + body) to /code/data/inbox/woo_raw.
    Returns the path written.
    """
    _ensure()
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    name = f"{ts}-{kind}-{(delivery_id or 'noid')}.json"
    path = BASE_DIR / name
    doc: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": kind,
        "topic": topic,
        "delivery_id": delivery_id,
        "headers": _redact(dict(headers)),
        "body_len": len(body or b""),
        "body_preview": (body[:256].decode("utf-8", "ignore") if body else ""),
        "body_b64": base64.b64encode(body or b"").decode("ascii"),
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
