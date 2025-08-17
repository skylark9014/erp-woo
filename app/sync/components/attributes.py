from __future__ import annotations
from typing import Any, Dict, List
from collections import defaultdict


def collect_used_attribute_values(variant_matrix: Dict[str, Dict[str, Any]]) -> Dict[str, set]:
    """
    Collect the set of attribute values used per attribute name from the variant_matrix.
    Returns: { "Stone": {"Sierra", ...}, "Sheet Size": {"2440mm x 1220mm", ...}, ... }
    """
    used: Dict[str, set] = defaultdict(set)
    for _, data in (variant_matrix or {}).items():
        for rec in (data.get("attribute_matrix") or []):
            if not isinstance(rec, dict):
                continue
            for aname, v in rec.items():
                val = v.get("value") if isinstance(v, dict) else None
                if val is not None and str(val).strip():
                    used[aname].add(str(val).strip())
    return used


async def bootstrap_wc_attributes_if_possible(
    used_attribute_values: Dict[str, set],
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Preview-first ensure for global product attributes & their terms.
    - dry_run: only report.
    - live: ensure via Woo helpers.
    """
    if dry_run:
        created_rows = []
        for attr_name in sorted(used_attribute_values.keys()):
            values = sorted(used_attribute_values[attr_name])
            created_rows.append({"attribute": attr_name, "values": values, "wc_response": {"dry_run": True}})
        return {"created": created_rows, "total_attributes": len(used_attribute_values)}

    # Live mode: use Woo helper (lazy import)
    try:
        from app.woo.woocommerce import ensure_wc_attributes_and_terms
    except Exception:
        ensure_wc_attributes_and_terms = None

    if not ensure_wc_attributes_and_terms:
        created_rows = []
        for attr_name in sorted(used_attribute_values.keys()):
            values = sorted(used_attribute_values[attr_name])
            created_rows.append({"attribute": attr_name, "values": values, "wc_response": {"dry_run": True}})
        return {
            "created": created_rows,
            "total_attributes": len(used_attribute_values),
            "warning": "Woo attribute ensure helper not available",
        }

    try:
        return await ensure_wc_attributes_and_terms(used_attribute_values)
    except Exception as e:
        return {"error": str(e), "total_attributes": len(used_attribute_values)}
