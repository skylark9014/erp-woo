# app/erp/erp_customers.py
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger("uvicorn.error")

# Defaults (fall back if not in .env)
DEFAULT_CUSTOMER_GROUP = getattr(settings, "ERP_DEFAULT_CUSTOMER_GROUP", "All Customer Groups")
DEFAULT_TERRITORY = getattr(settings, "ERP_DEFAULT_TERRITORY", "All Territories")
DEFAULT_CUSTOMER_TYPE = getattr(settings, "ERP_DEFAULT_CUSTOMER_TYPE", "Individual")


# ---------------------------
# HTTP helpers
# ---------------------------

def _erp_base() -> str:
    return (settings.ERP_URL or "").rstrip("/")

def _auth_headers() -> Dict[str, str]:
    return {
        "Authorization": f"token {settings.ERP_API_KEY}:{settings.ERP_API_SECRET}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def _json_or_text(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text

async def _get(client: httpx.AsyncClient, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    r = await client.get(f"{_erp_base()}{path}", params=params)
    if r.status_code >= 400:
        raise httpx.HTTPStatusError(f"GET {path} failed: {_json_or_text(r)}", request=r.request, response=r)
    return r.json()

async def _post(client: httpx.AsyncClient, path: str, data: Dict[str, Any]) -> Any:
    r = await client.post(f"{_erp_base()}{path}", json=data)
    if r.status_code >= 400:
        raise httpx.HTTPStatusError(f"POST {path} failed: {_json_or_text(r)}", request=r.request, response=r)
    return r.json()

async def _put(client: httpx.AsyncClient, path: str, data: Dict[str, Any]) -> Any:
    r = await client.put(f"{_erp_base()}{path}", json=data)
    if r.status_code >= 400:
        raise httpx.HTTPStatusError(f"PUT {path} failed: {_json_or_text(r)}", request=r.request, response=r)
    return r.json()

def _filters_param(filters: List[Any], fields: Optional[List[str]] = None) -> Dict[str, Any]:
    import json as _json
    return {
        "filters": _json.dumps(filters),
        "fields": _json.dumps(fields or ["name"]),
        "limit_page_length": 1,
        "order_by": "modified desc",
    }

async def _find_one(client: httpx.AsyncClient, doctype: str, filters: List[Any], fields: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    try:
        data = await _get(client, f"/api/resource/{doctype}", params=_filters_param(filters, fields))
        results = (data or {}).get("data") or []
        return results[0] if results else None
    except httpx.HTTPStatusError as e:
        logger.debug("find_one %s %s failed: %s", doctype, filters, e)
        return None

async def _insert_doc(client: httpx.AsyncClient, doc: Dict[str, Any]) -> Dict[str, Any]:
    payload = {"doc": doc}
    out = await _post(client, "/api/method/frappe.client.insert", payload)
    return out["message"] if "message" in out else out

async def _update_doc(client: httpx.AsyncClient, doctype: str, name: str, values: Dict[str, Any]) -> Dict[str, Any]:
    # ERPNext set_value expects: doctype, name, fieldname, value
    results = {}
    for field, value in values.items():
        payload = {"doctype": doctype, "name": name, "fieldname": field, "value": value}
        out = await _post(client, "/api/method/frappe.client.set_value", payload)
        if isinstance(out, dict) and "message" in out:
            results[field] = out["message"]
    return results


# ---------------------------
# Field helpers
# ---------------------------

def _coalesce(*vals: Optional[str]) -> Optional[str]:
    for v in vals:
        if v:
            v = str(v).strip()
            if v:
                return v
    return None

def _addr_fields_from(src: Dict[str, Any]) -> Dict[str, Any]:
    # Dynamic country mapping: fetch valid country names from ERPNext
    import httpx
    import threading
    from app.config import settings

    class CountryCache:
        _lock = threading.Lock()
        _map = None

        @classmethod
        def get_map(cls):
            with cls._lock:
                if cls._map is not None:
                    return cls._map
                url = (settings.ERP_URL or "").rstrip("/") + "/api/resource/Country"
                headers = {
                    "Authorization": f"token {settings.ERP_API_KEY}:{settings.ERP_API_SECRET}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                try:
                    with httpx.Client(timeout=15.0, headers=headers, verify=False) as client:
                        iso_map = {}
                        limit = 200
                        start = 0
                        while True:
                            params = {"limit_start": start, "limit_page_length": limit}
                            resp = client.get(url, params=params)
                            resp.raise_for_status()
                            countries = [c["name"] for c in resp.json().get("data", [])]
                            if not countries:
                                break
                            for name in countries:
                                detail_url = f"{url}/{name}"
                                detail_resp = client.get(detail_url)
                                detail_resp.raise_for_status()
                                data = detail_resp.json().get("data", {})
                                code = data.get("code")
                                country_name = data.get("country_name") or data.get("name")
                                if code and country_name:
                                    iso_map[code.upper()] = country_name
                            if len(countries) < limit:
                                break
                            start += limit
                        cls._map = iso_map
                        return iso_map
                except Exception as e:
                    import logging
                    logging.getLogger("uvicorn.error").warning(f"[ERPNext] Could not build country map: {e}")
                    cls._map = {}
                    return cls._map

    import logging
    logger = logging.getLogger("uvicorn.error")
    country_raw = _coalesce(src.get("country")) or ""
    country_map = CountryCache.get_map()
    code_upper = country_raw.upper()
    country_name = country_map.get(code_upper)
    if not country_name:
        for k, v in country_map.items():
            if code_upper == k or code_upper == v.upper():
                country_name = v
                break
    if not country_name:
        country_name = country_raw
    return {
        "address_line1": _coalesce(src.get("address_1"), src.get("line1"), src.get("address1")) or "—",
        "address_line2": _coalesce(src.get("address_2"), src.get("line2"), src.get("address2")) or "",
        "city": _coalesce(src.get("city")) or "",
        "state": _coalesce(src.get("state"), src.get("province")) or "",
        "pincode": _coalesce(src.get("postcode"), src.get("postal_code"), src.get("zip")) or "",
        "country": country_name,
        "phone": _coalesce(src.get("phone")) or "",
        "email_id": _coalesce(src.get("email")) or "",
    }


# ---------------------------
# Public entry
# ---------------------------

async def ensure_online_customer_group(client: httpx.AsyncClient) -> None:
    from app.config import settings
    base_url = getattr(settings, "ERP_URL", None)
    if not base_url:
        raise RuntimeError("ERP_URL not configured in settings")
    url = f"{base_url.rstrip('/')}/api/resource/Customer Group/Online Customer"
    resp = await client.get(url)
    if resp.status_code == 200:
        return
    doc = {
        "doctype": "Customer Group",
        "customer_group_name": "Online Customer",
        "parent_customer_group": "All Customer Groups",
        "is_group": 0,
    }
    post_url = f"{base_url.rstrip('/')}/api/resource/Customer Group"
    await client.post(post_url, json=doc)

async def set_customer_group(client: httpx.AsyncClient, customer_name: str) -> None:
    from app.config import settings
    base_url = getattr(settings, "ERP_URL", None)
    if not base_url:
        raise RuntimeError("ERP_URL not configured in settings")
    url = f"{base_url.rstrip('/')}/api/resource/Customer/{customer_name}"
    await client.put(url, json={"customer_group": "Online Customer"})

async def upsert_customer_from_woo(cust: Dict[str, Any]) -> tuple[str, Optional[str], Optional[str]]:
    from app.models.audit_log import add_audit_entry
    import logging
    logger = logging.getLogger("uvicorn.error")
    """
    Given a Woo customer payload, upsert ERPNext Customer, Contact, and Billing/Shipping Address.
    Returns (customer_name, billing_address_name, shipping_address_name)
    """
    first = _coalesce(cust.get("first_name"))
    last = _coalesce(cust.get("last_name"))
    company = _coalesce(cust.get("billing", {}).get("company"))
    email = _coalesce(cust.get("email"), cust.get("billing", {}).get("email"))
    phone = _coalesce(cust.get("billing", {}).get("phone"))
    display_name = _coalesce(company, " ".join([p for p in [first, last] if p]).strip(), "Woo Customer")

    billing = cust.get("billing") or {}
    shipping = cust.get("shipping") or {}

    async with httpx.AsyncClient(timeout=45.0, headers=_auth_headers(), verify=False) as client:
        cust_name = await _find_customer(client, email=email, customer_name=display_name)
        if cust_name:
            cust_name = await _update_customer(client, cust_name, email=email, phone=phone)
        else:
            cust_name = await _create_customer(client, display_name, email=email, phone=phone)

        # Ensure Online Customer group exists and set customer group
        await ensure_online_customer_group(client)
        await set_customer_group(client, cust_name)

        await _upsert_contact(client, cust_name, first=first, last=last, email=email, phone=phone)

        bill_name = await _upsert_address(client, cust_name, address_type="Billing", src=billing)
        ship_name = await _upsert_address(client, cust_name, address_type="Shipping", src=shipping)

        return cust_name, bill_name, ship_name


# ---------------------------
# Internals (find/create/update/contact/address)
# ---------------------------

async def _find_customer(client: httpx.AsyncClient, *, email: Optional[str], customer_name: Optional[str]) -> Optional[str]:
    # Prefer lookup by email (field exists on Customer)
    if email:
        found = await _find_one(client, "Customer", [["email_id", "=", email]], fields=["name"])
        if found:
            return found["name"]
    # Else exact customer_name
    if customer_name:
        found = await _find_one(client, "Customer", [["customer_name", "=", customer_name]], fields=["name"])
        if found:
            return found["name"]
    return None


async def _create_customer(client: httpx.AsyncClient, customer_name: str, *, email: Optional[str], phone: Optional[str]) -> str:
    doc: Dict[str, Any] = {
        "doctype": "Customer",
        "customer_name": customer_name,
        "customer_group": DEFAULT_CUSTOMER_GROUP,
        "territory": DEFAULT_TERRITORY,
        "customer_type": DEFAULT_CUSTOMER_TYPE,
    }
    if email:
        doc["email_id"] = email
    if phone:
        doc["mobile_no"] = phone
    created = await _insert_doc(client, doc)
    return created["name"]


async def _update_customer(client: httpx.AsyncClient, name: str, *, email: Optional[str], phone: Optional[str]) -> str:
    # Best-effort updates; ignore failures quietly
    try:
        if email:
            await _update_doc(client, "Customer", name, {"email_id": email})
        if phone:
            await _update_doc(client, "Customer", name, {"mobile_no": phone})
    except Exception as e:
        logger.debug("Customer update best-effort failed for %s: %s", name, e)
    return name


async def _upsert_contact(
    client: httpx.AsyncClient,
    customer_name: str,
    *,
    first: Optional[str],
    last: Optional[str],
    email: Optional[str],
    phone: Optional[str],
) -> Optional[str]:
    """
    Best-effort idempotency by primary email; if not found, create a Contact linked to the Customer.
    """
    contact_name: Optional[str] = None

    # Try to find by email (many ERPNext builds expose email_id on Contact)
    if email:
        found = await _find_one(client, "Contact", [["email_id", "=", email]], fields=["name"])
        if found:
            contact_name = found["name"]

    if contact_name:
        # Update basic fields best-effort
        try:
            vals: Dict[str, Any] = {}
            if first:
                vals["first_name"] = first
            if last:
                vals["last_name"] = last
            if phone:
                vals["phone"] = phone
            if email:
                vals["email_id"] = email
            if vals:
                await _update_doc(client, "Contact", contact_name, vals)
        except Exception as e:
            logger.debug("Contact update best-effort failed for %s: %s", contact_name, e)
        return contact_name

    # Create new Contact
    doc: Dict[str, Any] = {
        "doctype": "Contact",
        "first_name": first or customer_name,
        "last_name": last or "",
        "email_id": email or "",
        "phone": phone or "",
        "links": [{"link_doctype": "Customer", "link_name": customer_name}],
        # child tables (optional but nice if your site uses them)
        "email_ids": [{"email_id": email, "is_primary": 1}] if email else [],
        "phone_nos": [{"phone": phone, "is_primary_phone": 1}] if phone else [],
    }
    try:
        created = await _insert_doc(client, doc)
        return created.get("name")
    except Exception as e:
        logger.debug("Contact insert failed for customer=%s: %s", customer_name, e)
        return None


async def _upsert_address(
    client: httpx.AsyncClient,
    customer_name: str,
    *,
    address_type: str,
    src: Dict[str, Any],
) -> Optional[str]:
    if not src:
        return None

    fields = _addr_fields_from(src)
    import logging
    logger = logging.getLogger("uvicorn.error")

    # Idempotency heuristic: same title (customer), type, line1, pincode
    title = _coalesce(src.get("address_title"), src.get("name"), customer_name) or customer_name
    line1 = fields.get("address_line1") or ""
    pincode = fields.get("pincode") or ""

    filters = [
        ["address_title", "=", title],
        ["address_type", "=", address_type],
    ]
    if line1:
        filters.append(["address_line1", "=", line1])
    if pincode:
        filters.append(["pincode", "=", pincode])

    found = await _find_one(client, "Address", filters, fields=["name"])
    if found:
        name = found["name"]
        # Best-effort update of details (city/phone/etc)
        try:
            vals = {**fields}
            await _update_doc(client, "Address", name, vals)
        except Exception as e:
            logger.debug("Address update best-effort failed for %s: %s", name, e)
        return name

    # Create new Address
    doc = {
        "doctype": "Address",
        "address_title": title,
        "address_type": address_type,
        **fields,
        "links": [{"link_doctype": "Customer", "link_name": customer_name}],
    }
    created = await _insert_doc(client, doc)
    return created.get("name")
