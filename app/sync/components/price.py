# app/sync/components/price.py
from __future__ import annotations
from typing import Callable, Dict, Any, Tuple

async def resolve_price_map(
    get_price_map_fn: Callable[..., Any],
    preferred_name: str | None = None
) -> Tuple[Dict[str, Any], str | None, int]:
    """
    Calls your get_price_map with return_name=True (if supported) and
    returns (price_map, real_name_or_None, numeric_count).
    """
    # Try with return_name flag
    try:
        pm, nm = await get_price_map_fn(preferred_name, return_name=True)  # new signature
        if not isinstance(pm, dict):
            pm, nm = {}, None
    except TypeError:
        # Fallback: no return_name support
        res = await get_price_map_fn(preferred_name)
        pm = res if isinstance(res, dict) else {}
        nm = (pm.get("_meta", {}) or {}).get("price_list") or None

    # Count numeric-like values
    count = 0
    for v in pm.values():
        if isinstance(v, (int, float)):
            count += 1
        elif isinstance(v, str):
            try:
                float(v)
                count += 1
            except Exception:
                pass

    return pm, nm, count
