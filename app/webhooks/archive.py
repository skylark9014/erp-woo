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
    ts_short = time.strftime("%y%m%d", time.gmtime())
    topic_str = topic or ""
    resource = None
    event = None
    if topic_str:
        parts = topic_str.split('.')
        if len(parts) == 2:
            resource, event = parts
        elif len(parts) > 2:
            resource, event = parts[-2], parts[-1]

    # If resource/event not found in topic, try extracting from payload
    if (resource is None or event is None) and body:
        try:
            payload = json.loads(body)
            if isinstance(payload, dict):
                if resource is None and 'resource' in payload:
                    resource = payload['resource']
                if event is None and 'event' in payload:
                    event = payload['event']
        except Exception:
            pass
    # Find next sequential number for today/topic
    seq = 1
    base_pattern = f"{ts_short}-"
    if resource and event:
        base_pattern += f"{resource}.{event}"
    elif topic_str:
        base_pattern += topic_str
    else:
        base_pattern += f"{kind}-{(delivery_id or 'noid')}"
    # Scan for existing files
    existing = list(BASE_DIR.glob(f"{base_pattern}-*.json"))
    if existing:
        nums = []
        for f in existing:
            try:
                num = int(f.stem.split('-')[-1].split('.')[0])
                nums.append(num)
            except Exception:
                continue
        if nums:
            seq = max(nums) + 1
    name = f"{base_pattern}-{seq}.json"
    path = BASE_DIR / name
    doc: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": kind,
        "topic": topic,
        "delivery_id": delivery_id,
        "resource": resource,
        "event": event,
        "headers": _redact(dict(headers)),
        "body_len": len(body or b""),
        "body_preview": (body[:256].decode("utf-8", "ignore") if body else ""),
        "body_b64": base64.b64encode(body or b"").decode("ascii"),
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
