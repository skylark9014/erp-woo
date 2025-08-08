# app/erp/erp_attribute_loader.py
# --------------------------------------------------------------------------------------
# Live Item Attribute/Value/Abbreviation mapping from ERPNext API, with Excel fallback.
# --------------------------------------------------------------------------------------

import requests
import os
import pandas as pd
from app.config import settings

class AttributeValueMapping:
    """
    Fast lookup between attribute values and abbreviations.
    """
    def __init__(self, normalize_case=True):
        self.abbr_to_value = {}
        self.value_to_abbr = {}
        self.normalize_case = normalize_case

    def _norm(self, s):
        if s is None: return None
        s = str(s).strip()
        if self.normalize_case:
            s = s.lower()
        return s

    def add(self, abbr, value):
        abbr_n = self._norm(abbr)
        value_n = self._norm(value)
        if abbr_n and value_n:
            self.abbr_to_value[abbr_n] = value
            self.value_to_abbr[value_n] = abbr

    def get_value(self, abbr):
        return self.abbr_to_value.get(self._norm(abbr))

    def get_abbr(self, value):
        return self.value_to_abbr.get(self._norm(value))

    def values(self):
        return list(self.value_to_abbr.keys())

    def abbreviations(self):
        return list(self.abbr_to_value.keys())

    def __getitem__(self, abbr):
        return self.get_value(abbr)

    def __contains__(self, abbr):
        return self._norm(abbr) in self.abbr_to_value

    def as_dict(self):
        return dict(self.abbr_to_value)  # Shallow copy

# ===== Live ERPNext API mapping =====

ERP_URL = settings.ERP_URL
API_KEY = settings.ERP_API_KEY
API_SECRET = settings.ERP_API_SECRET
AUTH_HEADER = {"Authorization": f"token {API_KEY}:{API_SECRET}"}

def get_erpnext_attribute_order() -> list:
    """
    Returns a list of attribute names, live from ERPNext.
    E.g. ["Size", "Colour", "Sheet Size", "Stone"]
    """
    url = f"{ERP_URL}/api/resource/Item%20Attribute"
    resp = requests.get(url, headers=AUTH_HEADER)
    resp.raise_for_status()
    return [a["name"] for a in resp.json()["data"]]

def get_erpnext_attribute_map(attribute_order: list) -> dict:
    """
    Returns { attribute_name: AttributeValueMapping } built live from ERPNext.
    """
    attr_map = {}
    for attr in attribute_order:
        url = f"{ERP_URL}/api/resource/Item%20Attribute/{attr.replace(' ', '%20')}"
        resp = requests.get(url, headers=AUTH_HEADER)
        resp.raise_for_status()
        data = resp.json()["data"]
        mapping = AttributeValueMapping()
        for v in data.get("item_attribute_values", []):
            mapping.add(v["abbr"], v["attribute_value"])
        attr_map[attr] = mapping
    return attr_map

# ===== Excel fallback =====

def load_attribute_mappings(filepath):
    """
    Loads the ERPNext Item Attribute mapping Excel into:
        { attribute_name: AttributeValueMapping }
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    df = pd.read_excel(filepath, engine="openpyxl")
    attr_col = "Attribute (Item Attribute Values)"
    value_col = "Attribute Value (Item Attribute Values)"
    abbr_col = "Abbreviation (Item Attribute Values)"
    for candidate in df.columns:
        if "Attribute" in candidate and "Value" in candidate:
            value_col = candidate
        elif "Attribute" in candidate and "(Item Attribute Values)" in candidate:
            attr_col = candidate
        elif "Abbreviation" in candidate:
            abbr_col = candidate

    mapping = {}
    for _, row in df.iterrows():
        attr = str(row.get(attr_col, "")).strip()
        value = str(row.get(value_col, "")).strip()
        abbr = str(row.get(abbr_col, "")).strip()
        if not attr or not abbr or not value:
            continue
        if attr not in mapping:
            mapping[attr] = AttributeValueMapping()
        mapping[attr].add(abbr, value)
    return mapping
