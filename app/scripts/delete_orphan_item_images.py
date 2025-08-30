#!/usr/bin/env python3
"""
Delete orphan Item images and attachments in ERPNext via REST API.
Requires: requests, python-dotenv
"""

import os
import requests
from dotenv import load_dotenv
import json

load_dotenv("/home/jannie/erp-woo/.env")

ERP_URL = os.getenv("ERP_URL")
API_KEY = os.getenv("ERP_API_KEY")
API_SECRET = os.getenv("ERP_API_SECRET")

session = requests.Session()
session.headers.update({
    "Authorization": f"token {API_KEY}:{API_SECRET}",
    "Content-Type": "application/json"
})

def get_all(doctype, fields=None, filters=None):
    url = f"{ERP_URL}/api/resource/{doctype}"
    params = {}
    if fields:
        params["fields"] = json.dumps(fields)
    if filters:
        params["filters"] = json.dumps(filters)
    resp = session.get(url, params=params)
    resp.raise_for_status()
    return resp.json()["data"]

def delete_doc(doctype, name):
    url = f"{ERP_URL}/api/resource/{doctype}/{name}"
    resp = session.delete(url)
    return resp.status_code == 202

def update_doc(doctype, name, data):
    url = f"{ERP_URL}/api/resource/{doctype}/{name}"
    resp = session.put(url, json=data)
    return resp.status_code == 200

def main():
    deleted_files = 0
    cleared_items = 0

    # 1. Delete File records attached to non-existent Items
    files = get_all("File", fields=["name", "attached_to_name", "file_url"], filters={"attached_to_doctype": "Item"})
    for f in files:
        # Check if Item exists
        item_url = f"{ERP_URL}/api/resource/Item/{f['attached_to_name']}"
        r = session.get(item_url)
        if r.status_code == 404:
            if delete_doc("File", f["name"]):
                deleted_files += 1

    # 2. Clear image field for Items pointing to missing files
    items = get_all("Item", fields=["name", "image"])
    for item in items:
        img_url = item["image"]
        if img_url:
            files = get_all("File", fields=["name"], filters={"file_url": img_url})
            if not files:
                # Clear image field
                if update_doc("Item", item["name"], {"image": ""}):
                    cleared_items += 1

    print(f"Deleted {deleted_files} orphan File records.")
    print(f"Cleared image field for {cleared_items} Items with missing images.")

if __name__ == "__main__":
    main()