#!/usr/bin/env python3
"""
Clear all ERPNext Item image and File references that point to missing files (404).
Checks each image/file reference by making a GET request to the file URL.
If the file returns 404, clears the reference in ERPNext via REST API.
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
    "Authorization": f"token {API_KEY}:{API_SECRET}",
    "Content-Type": "application/json"
})

def file_exists(url):
    try:
        r = requests.get(url, timeout=5)
        return r.status_code == 200
    except Exception:
        return False

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

def update_doc(doctype, name, data):
    url = f"{ERP_URL}/api/resource/{doctype}/{name}"
    resp = session.put(url, json=data)
    return resp.status_code == 200

def main():
    cleared_items = 0
    cleared_files = 0
    # 1. Clear image field for Items with missing images
    items = get_all("Item", fields=["name", "image"])
    for item in items:
        img_url = item["image"]
        if img_url and not file_exists(img_url if img_url.startswith("http") else ERP_URL + img_url):
            if update_doc("Item", item["name"], {"image": ""}):
                cleared_items += 1
    # 2. Clear File records with missing file_url
    files = get_all("File", fields=["name", "file_url"])
    for f in files:
        file_url = f["file_url"]
        if file_url and not file_exists(file_url if file_url.startswith("http") else ERP_URL + file_url):
            if update_doc("File", f["name"], {"file_url": ""}):
                cleared_files += 1
    print(f"Cleared image field for {cleared_items} Items with missing images.")
    print(f"Cleared file_url for {cleared_files} File records with missing files.")

if __name__ == "__main__":
    main()
