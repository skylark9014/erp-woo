# app/erp/erp_variant_matrix.py
# --------------------------------------------------------------------------------------
# Utility to build a variant matrix from ERPNext items and attributes.
# This is the canonical source for creating Woo variable products with attributes.
# Serves as the “ERPNext → WooCommerce bridge”
# The ERP items listed (templates + variants) as dictionaries (ERPNext API shape).
# Provides access to the attribute/abbreviation map loaded via erp_attribute_loader.py.
# Provides the ability to parse SKUs using erp_sku_parser.py.
# This builder gives all the data needed to:
# Create Woo variable products with correct attribute structures.
# Display possible options in your admin UI or logs.
# Map ERP attribute abbreviations to Woo attribute labels/values.
# --------------------------------------------------------------------------------------

# app/erp/erp_variant_matrix.py
# ---------------------------------------------------------
# Utility to build a variant matrix from ERPNext items/attributes.
# This is used as the bridge for variant mapping ERPNext→Woo.
# ---------------------------------------------------------

from typing import List, Dict, Any
from app.erp.erp_attribute_loader import AttributeValueMapping
from app.erp.erp_sku_parser import parse_erp_sku

def build_variant_matrix(
    erp_items: List[Dict[str, Any]],
    attribute_map: Dict[str, AttributeValueMapping],
    attribute_order: List[str],
) -> Dict[str, Dict[str, Any]]:
    """
    For each Item Template (has_variants == 1), group all its child items (variants)
    and construct a matrix of attribute value/abbreviation pairs.

    Returns:
        {
            "TEMPLATE_CODE": {
                "template_item": {...},
                "variants": [{...}, ...],
                "attribute_matrix": [
                    {
                        "Stone": {"abbr": "HIGHL", "value": "The Highlands"},
                        "Sheet Size": {"abbr": "LARGE", "value": "2440mm x 1220mm"}
                        # ...all attributes in attribute_order
                    },
                    ...
                ]
            },
            ...
        }
    """
    # 1. Group templates and their children
    templates = {}
    variants_by_template = {}

    for item in erp_items:
        if item.get("has_variants") == 1:
            templates[item["item_code"]] = item

    for item in erp_items:
        parent = item.get("variant_of")
        if parent and parent in templates:
            variants_by_template.setdefault(parent, []).append(item)

    # 2. Build attribute matrix for each template
    variant_matrix = {}
    for template_code, template_item in templates.items():
        children = variants_by_template.get(template_code, [])
        matrix = []
        for v in children:
            sku = v.get("item_code")
            # Parse the SKU into attribute abbreviations and values
            parsed = parse_erp_sku(sku, attribute_order, attribute_map) or {}

            entry = {}
            for attr_name in attribute_order:
                # parsed[attr_name] should be a dict: {"abbr":..., "value":...}
                parsed_attr = parsed.get(attr_name, {})
                abbr = parsed_attr.get("abbr")
                value = parsed_attr.get("value")
                entry[attr_name] = {"abbr": abbr, "value": value}
            matrix.append(entry)

        variant_matrix[template_code] = {
            "template_item": template_item,
            "variants": children,
            "attribute_matrix": matrix,
        }
    return variant_matrix

# ----------------- TEST SECTION (copy-paste ready) -----------------
if __name__ == "__main__":
    from pprint import pprint
    from app.erp.erp_attribute_loader import AttributeValueMapping

    def make_attr_map(raw: dict) -> dict:
        out = {}
        for attr, abbr_map in raw.items():
            m = AttributeValueMapping()
            for abbr, value in abbr_map.items():
                m.add(abbr, value)
            out[attr] = m
        return out

    # Example mock ERP item data (replace with real API data)
    items = [
        {"item_code": "SVR", "has_variants": 1, "item_name": "Stone Veneer"},
        {"item_code": "SVR-HIGHL-LARGE", "variant_of": "SVR", "has_variants": 0},
        {"item_code": "SVR-HIGHL-MEDIUM", "variant_of": "SVR", "has_variants": 0},
        {"item_code": "SVR-ANDES-LARGE", "variant_of": "SVR", "has_variants": 0},
        {"item_code": "SVR-ANDES-MEDIUM", "variant_of": "SVR", "has_variants": 0},
    ]
    attribute_map = make_attr_map({
        "Stone": {"HIGHL": "The Highlands", "ANDES": "Andes"},
        "Sheet Size": {"LARGE": "2440mm x 1220mm", "MEDIUM": "1220mm x 610mm"},
    })
    attribute_order = ["Stone", "Sheet Size"]

    pprint(build_variant_matrix(items, attribute_map, attribute_order))
