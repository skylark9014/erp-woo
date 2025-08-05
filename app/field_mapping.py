# app/field_mapping.py
# ===================================================
# Central ERPNext → WooCommerce product field mapping
# ===================================================

import json
import os

MAPPING_FILE = os.path.join(os.path.dirname(__file__), "mapping", "sync_field_mapping.json")

def load_sync_field_mapping():
    with open(MAPPING_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

SYNC_FIELD_MAPPING = load_sync_field_mapping()

# ERPNext fields required for fetching
def get_erp_sync_fields():
    return list(SYNC_FIELD_MAPPING.keys())

# Woo fields for diffing/sync
def get_wc_sync_fields():
    return list(SYNC_FIELD_MAPPING.values())

def map_erp_to_wc_product(
    erp_item,
    category_map=None,
    brand_map=None,
    image_list=None,
):
    """
    Convert an ERPNext item dict to a WooCommerce product dict for API.
    Handles special fields (categories, brands, images).
    """
    wc_product = {}
    for erp_field, woo_field in SYNC_FIELD_MAPPING.items():
        value = erp_item.get(erp_field)
        if woo_field == "categories":
            if category_map and value:
                cat_id = category_map.get(value)
                if cat_id:
                    wc_product["categories"] = [{"id": cat_id}]
            continue
        if woo_field == "brands":
            if brand_map and value:
                brand_id = brand_map.get(value)
                if brand_id:
                    wc_product["brands"] = [{"id": brand_id}]
            continue
        if woo_field == "images":
            # Assume image_list provided externally (already uploaded)
            if image_list:
                wc_product["images"] = image_list
            continue
        if woo_field:
            wc_product[woo_field] = value
    return wc_product
