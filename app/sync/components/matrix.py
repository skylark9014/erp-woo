# app/sync/components/matrix.py
from __future__ import annotations

from typing import List, Dict, Any, Tuple, DefaultDict, TYPE_CHECKING
from collections import defaultdict, Counter

# Only import for type-checkers; avoid runtime import chains breaking the module load
if TYPE_CHECKING:
    from app.erp.erp_attribute_loader import AttributeValueMapping

from app.erp.erp_sku_parser import parse_erp_sku


def _norm(s: str | None) -> str:
    return "" if s is None else str(s).strip()

def _lower(s: str | None) -> str:
    return _norm(s).lower()

def _parse_sku_tokens(sku: str) -> Tuple[str, List[str]]:
    parts = [p for p in _norm(sku).split("-") if p]
    if not parts:
        return "", []
    return parts[0], parts[1:]

def _abbr_index(attribute_map: Dict[str, "AttributeValueMapping"]) -> Dict[str, List[str]]:
    """Map lower(abbr) -> [attribute_names...]"""
    idx: Dict[str, List[str]] = defaultdict(list)
    for attr, mapping in (attribute_map or {}).items():
        for abbr in mapping.abbreviations():
            a = _lower(abbr)
            if attr not in idx[a]:
                idx[a].append(attr)
    return idx

def infer_attribute_order_for_group(
    skus: List[str],
    attribute_map: Dict[str, "AttributeValueMapping"],
    fallback_order: List[str],
) -> List[str]:
    """Infer attribute order by analyzing tokens at each position across a family of SKUs."""
    if not skus:
        return []

    idx = _abbr_index(attribute_map)
    tokens_by_pos: DefaultDict[int, List[str]] = defaultdict(list)
    max_len = 0
    for sku in skus:
        _, toks = _parse_sku_tokens(sku)
        max_len = max(max_len, len(toks))
        for i, t in enumerate(toks):
            tokens_by_pos[i].append(_lower(t))

    chosen: List[str] = []
    used = set()

    for pos in range(max_len):
        tokens_here = tokens_by_pos.get(pos, [])
        if not tokens_here:
            continue

        score = Counter()
        for tok in tokens_here:
            for a in idx.get(tok, []):
                score[a] += 1

        # remove already-used attrs
        for a in list(score.keys()):
            if a in used:
                del score[a]

        if score:
            best_attr, _ = score.most_common(1)[0]
            chosen.append(best_attr)
            used.add(best_attr)
        else:
            # Heuristic fallback
            plausible = None
            for a in fallback_order:
                if a in used:
                    continue
                amap = attribute_map.get(a)
                if not amap:
                    continue
                for tok in tokens_here:
                    if amap.get_value(tok) is not None:
                        plausible = a
                        break
                if plausible:
                    break
            if plausible:
                chosen.append(plausible)
                used.add(plausible)

    return chosen

def guess_parent_code_from_sku(sku: str) -> str | None:
    parts = (sku or "").split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else None

def merge_simple_items_into_matrix(
    erp_items: List[Dict[str, Any]],
    template_variant_matrix: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    matrix = dict(template_variant_matrix or {})
    for item in erp_items or []:
        has_variants = item.get("has_variants", 0)
        variant_of = item.get("variant_of")
        if not has_variants and not variant_of:
            code = item.get("item_code")
            if not code or code in matrix:
                continue
            matrix[code] = {
                "template_item": item,
                "variants": [item],
                "attribute_matrix": [{}],
            }
    return matrix

def filter_variant_matrix_by_sku(
    variant_matrix: Dict[str, Dict[str, Any]], skus: List[str]
) -> Dict[str, Dict[str, Any]]:
    if not skus:
        return variant_matrix
    filtered = {}
    for template_code, data in (variant_matrix or {}).items():
        variants = data["variants"]
        attr_matrix = data.get("attribute_matrix") or [{} for _ in variants]
        keep = [i for i, v in enumerate(variants) if v.get("item_code") in skus]
        if keep:
            filtered[template_code] = {
                "template_item": data["template_item"],
                "variants": [variants[i] for i in keep],
                "attribute_matrix": [attr_matrix[i] for i in keep],
            }
    return filtered

def build_fallback_variant_matrix(erp_items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for it in erp_items or []:
        parent = it.get("variant_of")
        if parent:
            by_parent.setdefault(parent, []).append(it)
    matrix: Dict[str, Dict[str, Any]] = {}
    for parent_code, children in by_parent.items():
        template_item = next((i for i in erp_items if i.get("item_code") == parent_code), None) or children[0]
        matrix[parent_code] = {
            "template_item": template_item,
            "variants": children,
            "attribute_matrix": [(i.get("attributes") or {}) for i in children],
        }
    return matrix

def build_fallback_variant_matrix_by_base(
    erp_items: List[Dict[str, Any]],
    attribute_order_global: List[str],
    attribute_map: Dict[str, "AttributeValueMapping"],
) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in erp_items or []:
        base = guess_parent_code_from_sku(it.get("item_code") or "")
        if base:
            groups.setdefault(base, []).append(it)

    matrix: Dict[str, Dict[str, Any]] = {}
    for base, items in groups.items():
        skus = [i.get("item_code") or "" for i in items if (i.get("item_code") or "").strip()]
        # Infer order for this family
        inferred_order = infer_attribute_order_for_group(skus, attribute_map, attribute_order_global)

        attr_matrix = []
        for v in items:
            sku = v.get("item_code") or ""
            parsed = parse_erp_sku(sku, inferred_order, attribute_map) or {}
            entry = {}
            for attr_name in inferred_order:
                pr = parsed.get(attr_name) or {}
                entry[attr_name] = {"abbr": pr.get("abbr"), "value": pr.get("value")}
            attr_matrix.append(entry)

        template_item = items[0]
        matrix[base] = {
            "template_item": template_item,
            "variants": items,
            "attribute_matrix": attr_matrix,
        }
    return matrix

def infer_global_attribute_order_from_skus(
    erp_items: List[Dict[str, Any]],
    attribute_map: Dict[str, "AttributeValueMapping"],
    erp_attr_order: List[str],
) -> List[str]:
    groups: Dict[str, List[str]] = defaultdict(list)
    for it in erp_items or []:
        sku = (it.get("item_code") or "").strip()
        if not sku:
            continue
        base = guess_parent_code_from_sku(sku)
        if base:
            groups.setdefault(base, []).append(sku)

    pos_votes: Dict[str, Counter] = defaultdict(Counter)
    any_found = False

    for base, skus in groups.items():
        order = infer_attribute_order_for_group(skus, attribute_map, erp_attr_order)
        if not order:
            continue
        any_found = True
        for pos, attr in enumerate(order):
            pos_votes[attr][pos] += 1

    if not any_found:
        return list(erp_attr_order or [])

    scored: List[tuple[float, int, str]] = []
    for attr, ctr in pos_votes.items():
        total_votes = sum(ctr.values())
        weighted_sum = sum(p * c for p, c in ctr.items())
        avg_pos = weighted_sum / max(total_votes, 1)
        scored.append((avg_pos, -total_votes, attr))

    scored.sort()
    return [attr for _avg, _negvotes, attr in scored]
