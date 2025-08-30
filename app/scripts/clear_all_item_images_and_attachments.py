#!/usr/bin/env python3
"""
Clear ALL Item Images and Item Image Attachments in ERPNext (no existence check).
For each Item:
- Set 'image' and 'website_image' fields to blank.
- For each File attached to the Item, set 'file_url' to blank.
Logs progress for each action.
Requires: requests, python-dotenv
"""

import os
import requests
import json
from dotenv import load_dotenv

load_dotenv("/home/jannie/erp-woo/.env")

ERP_URL = os.getenv("ERP_URL")
API_KEY = os.getenv("ERP_API_KEY")
API_SECRET = os.getenv("ERP_API_SECRET")

session = requests.Session()
session.headers.update({
    "Authorization": f"token {API_KEY}:{API_SECRET}"
})

def get_all(doctype, fields=None, filters=None):
    url = f"{ERP_URL}/api/resource/{doctype}"
    all_data = []
    page_length = 20
    start = 0
    while True:
        params = {"limit_start": start, "limit_page_length": page_length}
        if fields:
            params["fields"] = json.dumps(fields)
        if filters:
            params["filters"] = json.dumps(filters)
        headers = {"Authorization": f"token {API_KEY}:{API_SECRET}"}
        resp = session.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()["data"]
        if not data:
            break
        all_data.extend(data)
        if len(data) < page_length:
            break
        start += page_length
    return all_data

def get_doc(doctype, name):
    url = f"{ERP_URL}/api/resource/{doctype}/{name}"
    headers = {"Authorization": f"token {API_KEY}:{API_SECRET}"}
    resp = session.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()["data"]

def update_doc(doctype, name, data):
    url = f"{ERP_URL}/api/resource/{doctype}/{name}"
    resp = session.put(url, json=data)
    return resp.status_code == 200

def main():
    cleared_images = 0
    cleared_website_images = 0
    cleared_attachments = 0
    checked_items = 0
    checked_attachments = 0
    items = get_all("Item", fields=["name", "image"])
    for item in items:
        name = item["name"]
        checked_items += 1
        # Clear image field
        if update_doc("Item", name, {"image": ""}):
            print(f"[Cleared] image for Item: {name}")
            cleared_images += 1
        # Get full Item doc for website_image
        item_doc = get_doc("Item", name)
        if update_doc("Item", name, {"website_image": ""}):
            print(f"[Cleared] website_image for Item: {name}")
            cleared_website_images += 1
        # Clear attached files
        files = get_all("File", fields=["name", "file_url"], filters={"attached_to_doctype": "Item", "attached_to_name": name})
        for f in files:
            checked_attachments += 1
            if update_doc("File", f["name"], {"file_url": ""}):
                print(f"[Cleared] attachment for Item: {name} (File: {f['name']})")
                cleared_attachments += 1
    print(f"Checked {checked_items} Items.")
    print(f"Checked {checked_attachments} Item attachments.")
    print(f"Cleared image for {cleared_images} Items.")
    print(f"Cleared website_image for {cleared_website_images} Items.")
    print(f"Cleared {cleared_attachments} Item image attachments.")

if __name__ == "__main__":
    main()
