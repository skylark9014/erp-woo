

# ERPNext Item image updater using REST API and .tar backup
# Usage: python copy_images.py
# Requires: requests, python-dotenv

import os
import requests
import json
import tarfile
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
        resp = session.get(url, params=params)
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
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()["data"]

def update_doc(doctype, name, data):
    url = f"{ERP_URL}/api/resource/{doctype}/{name}"
    resp = session.put(url, json=data)
    return resp.status_code == 200

def main():
    tar_path = input("Enter path to images .tar file (e.g. /home/jannie/ERPNext_backups/selected-files.tar): ").strip()
    if not os.path.isfile(tar_path):
        print(f"File not found: {tar_path}")
        return
    print(f"Extracting image filenames from {tar_path}...")
    with tarfile.open(tar_path, "r") as tar:
        image_files = [m.name for m in tar.getmembers() if m.isfile()]
    print(f"Found {len(image_files)} files in tar archive.")

    # Assume image filenames match Item names (with extension)
    items = get_all("Item", fields=["name", "image"])
    updated = 0
    for item in items:
        name = item["name"]
        # Try to find a matching image file
        match = None
        for img in image_files:
            # Match by exact name or by prefix (e.g. ItemName.jpg)
            if img.startswith(name):
                match = img
                break
        if match:
            # Construct ERPNext file URL (assume /files/ for public images)
            file_url = f"/files/{os.path.basename(match)}"
            if item.get("image") != file_url:
                if update_doc("Item", name, {"image": file_url}):
                    print(f"Updated Item: {name} -> {file_url}")
                    updated += 1
    print(f"Updated {updated} Item images from tar archive.")

if __name__ == "__main__":
    main()