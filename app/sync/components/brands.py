from __future__ import annotations
from typing import Any, Dict, List


def extract_brand_from_attrlist(attr_list) -> str:
    """Find 'Brand' in ERPNext attributes child-table shapes."""
    if not isinstance(attr_list, (list, tuple)):
        return ""
    for row in attr_list:
        if not isinstance(row, dict):
            continue
        name_candidates = [row.get("attribute"), row.get("attribute_name"), row.get("name")]
        is_brand = any(isinstance(n, str) and n.strip().lower() == "brand" for n in name_candidates)
        if not is_brand:
            continue
        val = row.get("attribute_value") or row.get("value") or row.get("brand") or row.get("abbr")
        if isinstance(val, dict):
            val = val.get("value") or val.get("name") or val.get("abbr")
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)):
            return str(val)
    return ""


def extract_brand(variant: Dict[str, Any], template_item: Dict[str, Any], attributes: Dict[str, Any]) -> str:
    """
    Robust brand lookup:
      1) top-level item fields (variant, then template)
      2) attributes child table (variant, then template)
      3) attributes dict from matrix (if 'Brand' present)
    """
    for src in (variant, template_item):
        if not isinstance(src, dict):
            continue
        for k in ("brand", "item_brand", "brand_name", "manufacturer"):
            v = src.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)):
                return str(v)

    b = extract_brand_from_attrlist(variant.get("attributes"))
    if b:
        return b
    b = extract_brand_from_attrlist(template_item.get("attributes"))
    if b:
        return b

    for key in ("Brand", "brand"):
        v = (attributes or {}).get(key)
        if isinstance(v, dict):
            v = v.get("value") or v.get("abbr") or v.get("name")
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)

    return ""


def collect_erp_brands_from_items(items: List[Dict[str, Any]]) -> List[str]:
    """Unique brand names found on items (variant→template fallback)."""
    brands: set[str] = set()
    template_brand: Dict[str, str] = {}
    for it in items or []:
        if it.get("has_variants") == 1:
            b = it.get("brand")
            if isinstance(b, str) and b.strip():
                template_brand[it.get("item_code")] = b.strip()

    for it in items or []:
        b = it.get("brand")
        if isinstance(b, str) and b.strip():
            brands.add(b.strip())
            continue
        parent = it.get("variant_of")
        if parent:
            pb = template_brand.get(parent)
            if pb:
                brands.add(pb)

    return sorted(brands)


async def bootstrap_wc_brands_if_possible(
    erp_items: List[Dict[str, Any]],
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Preview-first 'ensure' for brands.
    - In dry_run: just report what would be ensured.
    - In live mode: ensure 'Brand' attribute + terms via Woo API helper (if available).
    """
    brands = collect_erp_brands_from_items(erp_items)

    if dry_run or not brands:
        return {
            "total_erp_brands": len(brands),
            "created": [{"brand": b, "wc_response": {"dry_run": True}} for b in brands],
        }

    # Live mode: use Woo helper (lazy import to avoid cycles)
    try:
        from app.woocommerce import ensure_wc_brand_attribute_and_terms
    except Exception:
        ensure_wc_brand_attribute_and_terms = None

    if not ensure_wc_brand_attribute_and_terms:
        return {
            "total_erp_brands": len(brands),
            "created": [{"brand": b, "wc_response": {"dry_run": True}} for b in brands],
            "warning": "Woo brand ensure helper not available",
        }

    try:
        rep = await ensure_wc_brand_attribute_and_terms(brands)
        return {
            "total_erp_brands": len(brands),
            "attribute": rep.get("attribute"),
            "terms_report": rep.get("terms_report"),
        }
    except Exception as e:
        return {"total_erp_brands": len(brands), "error": str(e), "brands": brands}
