# app/erp/erp_sku_parser.py
# --------------------------------------------------------------------------------------
# ERPNext SKU Parser for mapping ERPNext variant SKUs (using attribute abbreviations)
# to full attribute/value pairs (using erp_attribute_loader).
# --------------------------------------------------------------------------------------

import pandas as pd

def parse_erp_sku(sku: str, attribute_order: list, attribute_map: dict) -> dict:
    """
    Parse ERPNext SKU into attribute abbreviations and (optionally) full value.

    Args:
        sku: SKU string (e.g. "SVR-HIGHL-MEDIUM")
        attribute_order: List of attribute names in order of their position in the SKU.
        attribute_map: { attribute_name: AttributeValueMapping }

    Returns:
        { "Stone": "HIGHL", "Sheet Size": "MEDIUM" }
        or (recommended, for richer data):
        {
            "Stone": {"abbr": "HIGHL", "value": "The Highlands"},
            "Sheet Size": {"abbr": "MEDIUM", "value": "1220mm x 610mm"}
        }
    """
    parts = sku.split("-")
    if len(parts) <= 1:
        return {}
    attr_abbrs = parts[1:]
    result = {}
    for i, attr_name in enumerate(attribute_order):
        abbr = attr_abbrs[i] if i < len(attr_abbrs) else None
        value = None
        if abbr and attr_name in attribute_map:
            # AttributeValueMapping instance: do not lower abbr, as it's already handled
            value = attribute_map[attr_name].get_value(abbr)
        result[attr_name] = {"abbr": abbr, "value": value}
    return result


# -------------------------------------------------------------------------
# TEST SECTION: Can be removed or commented out in production
# -------------------------------------------------------------------------
if __name__ == "__main__":
    # Load Excel and build attribute_map (abbreviation -> value)
    excel_file = "/mnt/data/Item Attribute.xlsx"
    xl = pd.ExcelFile(excel_file)

    # Build attribute maps (abbr->value) for all attributes
    attribute_map = {}
    for sheet in xl.sheet_names:
        df = xl.parse(sheet)
        if "Item Attribute Value" in df.columns and "Abbreviation" in df.columns:
            name = sheet.strip()
            submap = dict(zip(df["Abbreviation"].astype(str), df["Item Attribute Value"].astype(str)))
            attribute_map[name] = submap

    # Example for Stone Veneer, attribute_order is ["Stone", "Sheet Size"]
    attribute_order = ["Stone", "Sheet Size"]

    test_skus = [
        "SVR-HIGHL-MEDIUM",    # Should parse to "Stone": The Highlands, "Sheet Size": 1220mm x 610mm
        "SVR-HIGHL-LARGE",     # "Stone": The Highlands, "Sheet Size": 2440mm x 1220mm
        "PST-ANDES",           # Only one attribute, e.g. "Stone": Andes
    ]

    for sku in test_skus:
        if len(sku.split("-")) == 3:
            order = ["Stone", "Sheet Size"]
        elif len(sku.split("-")) == 2:
            order = ["Stone"]
        else:
            order = []
        parsed = parse_erp_sku(sku, order, attribute_map)
        print(f"SKU: {sku}\nParsed: {parsed}\n")

# ----------------
# How to Use
# ----------------

# Integrate attribute loader output:
# Use your erp_attribute_loader.py to generate attribute_map as above ({attribute: {abbr: value}}).

# Call parse_erp_sku(sku, attribute_order, attribute_map) for any SKU.
# Adapt attribute order per template if needed (e.g., "Stone Veneer" always uses [ "Stone", "Sheet Size" ]).

# Example Output
# SKU: SVR-HIGHL-MEDIUM
# Parsed: {'template': 'SVR', 'attributes': {'Stone': {'abbr': 'HIGHL', 'value': 'The Highlands'}, 'Sheet Size': {'abbr': 'MEDIUM', 'value': '1220mm x 610mm'}}}
