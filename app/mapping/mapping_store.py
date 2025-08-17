#===========================================================================
# app/mapping/mapping_store.py
# Create JSON mapping file for ERPNext and WooCommerce products.
#===========================================================================

import json
import os
import logging

from typing import List, Dict, Any
from datetime import datetime, timezone
from app.sync.sync_utils import _mapping_dir

logger = logging.getLogger("uvicorn.error")

MAPPING_JSON_FILE = os.path.join(_mapping_dir(), "mapping_store.json")

def build_product_mapping(erp_items, wc_products):
    wc_by_sku = {p.get("sku"): p for p in wc_products}
    mapping = []
    for item in erp_items:
        sku = item.get("item_code") or item.get("sku")
        wc = wc_by_sku.get(sku)
        if wc:
            mapping.append({
                "erp_item_code": sku,
                "woo_product_id": wc.get("id"),
                "sku": sku,
                "erp_item_name": item.get("item_name"),
                "woo_product_name": wc.get("name"),
                "woo_type": wc.get("type"),
                "woo_status": wc.get("status"),
                "erp_item_group": item.get("item_group") or item.get("Item Group"),
                # "last_synced" is NOT stored per row, but added by save_mapping_file
            })
    return mapping

def build_or_load_mapping() -> Dict[str, Any]:
    if not os.path.exists(MAPPING_JSON_FILE):
        return {"products": [], "last_synced": ""}
    with open(MAPPING_JSON_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            # Support legacy format (just a list)
            if isinstance(data, list):
                return {"products": data, "last_synced": ""}
            return data
        except Exception:
            return {"products": [], "last_synced": ""}

def save_mapping_file(mapping: List[Dict]):
    record = {
        "products": mapping,
        "last_synced": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    }
    tmp_file = MAPPING_JSON_FILE + ".tmp"
    
    logger.info(f"Saving ERPNext-Woocommerce product mapping file '{MAPPING_JSON_FILE}'")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    os.replace(tmp_file, MAPPING_JSON_FILE)
