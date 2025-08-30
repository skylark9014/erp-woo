#!/usr/bin/env python3
"""
Clear missing Item Images and Image Attachments in ERPNext.
For each Item:
- Check 'image' and 'website_image' fields for missing files (404).
- For each File attached to the Item, check if the file exists (404).
- If missing, clear the reference in ERPNext.
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

def file_exists(url):
    try:
        r = requests.get(url, timeout=5)
        return r.status_code == 200
    except Exception:
        return False




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
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            print(f"Error fetching {doctype}: {e}\nResponse: {resp.text}")
            raise
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
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"Error fetching {doctype} {name}: {e}\nResponse: {resp.text}")
        raise
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
    # Only permitted fields in list query
    items = get_all("Item", fields=["name", "image"])
    for item in items:
        name = item["name"]
        img_url = item.get("image")
        checked_items += 1
        # Get full Item doc for website_image
        item_doc = get_doc("Item", name)
        web_img_url = item_doc.get("website_image")
        # Check image field
        if img_url:
            test_url = img_url if img_url.startswith("http") else ERP_URL + img_url
            print(f"[Check] Item: {name} image: {img_url} -> {test_url}")
            if not file_exists(test_url):
                if update_doc("Item", name, {"image": ""}):
                    print(f"[Cleared] image for Item: {name} ({img_url})")
                    cleared_images += 1
                else:
                    print(f"[Failed] to clear image for Item: {name} ({img_url})")
        else:
            print(f"[Check] Item: {name} image: None")
        # Check website_image field
        if web_img_url:
            test_url = web_img_url if web_img_url.startswith("http") else ERP_URL + web_img_url
            print(f"[Check] Item: {name} website_image: {web_img_url} -> {test_url}")
            if not file_exists(test_url):
                if update_doc("Item", name, {"website_image": ""}):
                    print(f"[Cleared] website_image for Item: {name} ({web_img_url})")
                    cleared_website_images += 1
                else:
                    print(f"[Failed] to clear website_image for Item: {name} ({web_img_url})")
        else:
            print(f"[Check] Item: {name} website_image: None")
        # Check attached files
        files = get_all("File", fields=["name", "file_url"], filters={"attached_to_doctype": "Item", "attached_to_name": name})
        for f in files:
            checked_attachments += 1
            file_url = f["file_url"]
            if file_url:
                test_url = file_url if file_url.startswith("http") else ERP_URL + file_url
                print(f"[Check] Item: {name} attachment: {file_url} -> {test_url}")
                if not file_exists(test_url):
                    if update_doc("File", f["name"], {"file_url": ""}):
                        print(f"[Cleared] attachment for Item: {name} (File: {f['name']}, URL: {file_url})")
                        cleared_attachments += 1
                    else:
                        print(f"[Failed] to clear attachment for Item: {name} (File: {f['name']}, URL: {file_url})")
            else:
                print(f"[Check] Item: {name} attachment: None")
    print(f"Checked {checked_items} Items.")
    print(f"Checked {checked_attachments} Item attachments.")
    print(f"Cleared image for {cleared_images} Items.")
    print(f"Cleared website_image for {cleared_website_images} Items.")
    print(f"Cleared {cleared_attachments} Item image attachments.")

if __name__ == "__main__":
    main()
