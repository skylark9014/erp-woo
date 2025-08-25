# ----------------------------
# app/erp/erp_orders.py
# ----------------------------

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import date, timedelta, datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger("uvicorn.error")

# ---- Tunables / sane defaults (adjust to your site if needed) ----------------
DEFAULT_CUSTOMER_GROUP = "All Customer Groups"
DEFAULT_TERRITORY = "All Territories"

# Some sites require a default Company/Warehouse; leave empty to rely on ERPNext defaults.
DEFAULT_COMPANY = None     # e.g., "My Company"
DEFAULT_WAREHOUSE = None   # e.g., "Stores - MYC"
# -----------------------------------------------------------------------------


def _erp_base() -> str:
    return (settings.ERP_URL or "").rstrip("/")


def _auth_headers() -> Dict[str, str]:
    key = settings.ERP_API_KEY or ""
    secret = settings.ERP_API_SECRET or ""
    return {
        "Authorization": f"token {key}:{secret}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _json_or_text(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text


async def _get(
    client: httpx.AsyncClient,
    path: str,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    r = await client.get(f"{_erp_base()}{path}", params=params)
    if r.status_code >= 400:
        raise httpx.HTTPStatusError(f"GET {path} failed: {_json_or_text(r)}", request=r.request, response=r)
    return r.json()


async def _post(
    client: httpx.AsyncClient,
    path: str,
    data: Dict[str, Any],
) -> Any:
    r = await client.post(f"{_erp_base()}{path}", json=data)
    if r.status_code >= 400:
        raise httpx.HTTPStatusError(f"POST {path} failed: {_json_or_text(r)}", request=r.request, response=r)
    return r.json()


async def _put(
    client: httpx.AsyncClient,
    path: str,
    data: Dict[str, Any],
) -> Any:
    r = await client.put(f"{_erp_base()}{path}", json=data)
    if r.status_code >= 400:
        raise httpx.HTTPStatusError(f"PUT {path} failed: {_json_or_text(r)}", request=r.request, response=r)
    return r.json()


def _filters_param(filters: List[Any]) -> Dict[str, Any]:
    return {
        "filters": json.dumps(filters),
        "fields": json.dumps(["name"]),
        "limit_page_length": 1,
        "order_by": "modified desc",
    }


def _as_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if is_dataclass(obj):
        return asdict(obj)
    # generic object with attributes
    out = {}
    for k in dir(obj):
        if k.startswith("_"):
            continue
        try:
            v = getattr(obj, k)
        except Exception:
            continue
        if callable(v):
            continue
        out[k] = v
    return out


def _parse_mop_map() -> Dict[str, str]:
    """Safely parse WOO_MODE_OF_PAYMENT_MAP from settings whether dict or JSON string."""
    raw = getattr(settings, "WOO_MODE_OF_PAYMENT_MAP", {}) or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            # very loose "k:v,k:v" fallback
            out: Dict[str, str] = {}
            for pair in raw.split(","):
                if ":" in pair:
                    k, v = pair.split(":", 1)
                    out[k.strip().strip('"\'{} ')] = v.strip().strip('"\'{} ')
            return out
    return {}


def _iso_date(d: date | str | None) -> str:
    if not d:
        return date.today().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    # try parse looser strings
    try:
        # common Woo formats: "2025-08-18T10:33:11", "2025-08-18"
        return datetime.fromisoformat(d.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return date.today().isoformat()


def _lower(s: str | None) -> str:
    return (s or "").strip().lower()


# ================ Public API (used by jobs_worker) ============================

async def upsert_sales_order_from_woo(norm: Any) -> Tuple[str, str | None, str | None]:
    """
    Creates or reuses:
      - Customer (by email, else by name)
      - Billing/Shipping Address (linked to Customer)
      - Sales Order (idempotent via po_no="WOO-<order_id>")
    Returns: (so_name, billing_address_name, shipping_address_name)
    """

    # Validate and normalize input using Pydantic
    from app.erp.erp_sync_models import ERPOrderSyncPayload
    try:
        validated = ERPOrderSyncPayload.parse_obj(norm)
    except Exception as e:
        logger.error(f"[ERP-SYNC] payload validation error: {e}")
        raise AssertionError(f"ERPNext sync payload validation failed: {e}")

    n = validated.dict()
    order_id = n.get("order_id")
    assert order_id is not None, "normalized order must have order_id"

    po_no = f"WOO-{order_id}"
    cust = n.get("customer") or {}
    items = n.get("items") or []

    # Customer identity
    customer_name = (cust.get("customer_name") or cust.get("name") or cust.get("first_name") or "Woo Customer").strip()
    email = (cust.get("email") or "").strip() or None
    phone = (cust.get("phone") or "").strip() or None

    billing = _as_dict(n.get("billing") or n.get("billing_address"))
    shipping = _as_dict(n.get("shipping") or n.get("shipping_address"))

    async with httpx.AsyncClient(timeout=30.0, headers=_auth_headers(), verify=False) as client:
        # 1) Customer (find by email if available, else by exact name)
        customer_docname = await _ensure_customer(client, customer_name, email, phone)

        # 2) Addresses (optional)
        bill_name = await _ensure_address(
            client,
            customer_docname,
            address_type="Billing",
            src=billing,
        )
        ship_name = await _ensure_address(
            client,
            customer_docname,
            address_type="Shipping",
            src=shipping,
        )

        # 3) Sales Order — idempotent by po_no
        so_name = await _ensure_sales_order(
            client,
            po_no=po_no,
            customer=customer_docname,
            items=items,
            billing_address=bill_name,
            shipping_address=ship_name,
            company=DEFAULT_COMPANY,
        )

    return so_name, bill_name, ship_name


async def create_sales_invoice_from_so(norm: Any, so_name: str) -> str:
    """
    Creates (or finds) a Sales Invoice from the Sales Order, idempotent by po_no="WOO-<order_id>".
    Applies order-level discount_amount if present in normalized payload.
    """
    n = _as_dict(norm)
    order_id = n.get("order_id")
    assert order_id is not None, "normalized order must have order_id"
    po_no = f"WOO-{order_id}"

    discount_total = float(n.get("discount_total") or 0.0)

    async with httpx.AsyncClient(timeout=60.0, headers=_auth_headers(), verify=False) as client:
        # Reuse if an SI already exists for this PO No
        existing = await _find_one(client, "Sales Invoice", [["po_no", "=", po_no]])
        if existing:
            return existing["name"]

        # Use server method to generate SI from SO
        si_doc = await _make_sales_invoice_from_so(client, so_name)

        # Idempotency & discounts
        si_doc["po_no"] = po_no
        if discount_total > 0:
            # Apply as order-level discount on Net Total for consistency with Woo totals.
            si_doc["apply_discount_on"] = "Net Total"
            si_doc["discount_amount"] = discount_total

        inserted = await _insert_doc(client, si_doc)
        # Submit the SI
        submitted = await _submit_doc(client, "Sales Invoice", inserted["name"])
        return submitted["name"]


async def create_payment_entry(norm: Any, si_name: str) -> str:
    """
    Create and SUBMIT a Payment Entry for the given Sales Invoice, idempotently.
    Strategy:
      1) Ask ERPNext to generate a draft PE via get_payment_entry("Sales Invoice", si_name)
      2) Apply Mode of Payment using the .env WOO_MODE_OF_PAYMENT_MAP (by gateway)
      3) Set reference_no and reference_date from Woo payment data
      4) Allocate full outstanding against SI
      5) POST /api/resource/Payment Entry and then submit

    Returns Payment Entry name.
    """
    base = _erp_base()
    if not base:
        raise RuntimeError("ERP_URL not configured")

    # Derive payment metadata from normalized order
    gw = _lower(getattr(norm, "gateway", None) or getattr(norm, "payment_method", None))
    mop_map = _parse_mop_map()
    mode_of_payment = mop_map.get(gw) or mop_map.get("default") or "Bank"

    # Reference number / date
    ref_no = (
        getattr(norm, "transaction_id", None)
        or getattr(norm, "payment_id", None)
        or getattr(norm, "order_key", None)
        or f"WOO-{getattr(norm, 'order_id', '')}".strip("-")
    )
    ref_date = _iso_date(getattr(norm, "paid_at", None) or getattr(norm, "created_at", None))

    gen_url = f"{base}/api/method/erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry"
    async with httpx.AsyncClient(timeout=30.0, verify=False, headers=_auth_headers()) as client:
        gen = await client.post(gen_url, json={"dt": "Sales Invoice", "dn": si_name})
        gen.raise_for_status()
        payload = gen.json() or {}
        doc = payload.get("message") or payload.get("data") or payload

        if not isinstance(doc, dict) or doc.get("doctype") != "Payment Entry":
            raise RuntimeError(f"Unexpected response from get_payment_entry for {si_name}: {payload!r}")

        # Apply MoP + references
        doc["mode_of_payment"] = mode_of_payment
        doc["reference_no"] = ref_no
        doc["reference_date"] = ref_date

        if not doc.get("party_type"):
            doc["party_type"] = "Customer"

        # Allocate full outstanding against our SI
        refs = doc.get("references") or []
        for r in refs:
            if r.get("reference_doctype") == "Sales Invoice" and r.get("reference_name") == si_name:
                outstanding = r.get("outstanding_amount")
                if outstanding:
                    r["allocated_amount"] = outstanding

        # Create the PE (docstatus 0)
        create_url = f"{base}/api/resource/Payment Entry"
        created = await client.post(create_url, json=doc)
        created.raise_for_status()
        created_doc = (created.json() or {}).get("data") or {}
        pe_name = created_doc.get("name") or doc.get("name")
        if not pe_name:
            raise RuntimeError(f"Payment Entry create did not return a name for SI={si_name}")

        # Submit the PE
        submit_url = f"{base}/api/resource/Payment Entry/{pe_name}"
        submitted = await client.put(submit_url, json={"docstatus": 1})
        submitted.raise_for_status()

        return pe_name


async def create_delivery_note_from_si(norm_or_si: Any, maybe_si_name: Optional[str] = None) -> str:
    """
    Create and SUBMIT a Delivery Note from a Sales Invoice, idempotently.
    Accepts either:
      - (norm, si_name)  → will use norm.order_id to set po_no=WOO-<id> and ensure idempotency
      - (si_name)        → will try to read po_no from the SI and reuse; else create without po_no

    Returns Delivery Note name.
    """
    # Normalize arguments
    if maybe_si_name is None and isinstance(norm_or_si, str):
        norm: Dict[str, Any] | None = None
        si_name = norm_or_si
        order_id = None
        po_no = None
    else:
        norm = _as_dict(norm_or_si)
        si_name = str(maybe_si_name or "")
        order_id = norm.get("order_id") if isinstance(norm, dict) else None
        po_no = f"WOO-{order_id}" if order_id is not None else None

    async with httpx.AsyncClient(timeout=60.0, headers=_auth_headers(), verify=False) as client:
        # If we don't have a po_no from norm, try to fetch it off the SI
        if not po_no:
            try:
                si = await _get(client, f"/api/resource/Sales Invoice/{si_name}", params={"fields": '["name","po_no"]'})
                po_no = (si.get("data") or {}).get("po_no") or None
            except Exception:
                po_no = None

        # Idempotency: reuse DN by po_no if available
        if po_no:
            dn_existing = await _find_one(client, "Delivery Note", [["po_no", "=", po_no]])
            if dn_existing:
                return dn_existing["name"]

        # Build DN from SI via whitelisted method
        dn_doc = await _make_delivery_note_from_si(client, si_name)

        # Set po_no for idempotency if we have it
        if po_no:
            dn_doc["po_no"] = po_no

        # Insert & submit
        inserted = await _insert_doc(client, dn_doc)
        submitted = await _submit_doc(client, "Delivery Note", inserted["name"])
        return submitted["name"]


# ======== Phase 5: Refunds & Cancellations – helpers and APIs ================

async def get_sales_invoice(si_name: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, headers=_auth_headers(), verify=False) as client:
        data = await _get(client, f"/api/resource/Sales Invoice/{si_name}", params={"fields": '["*"]'})
        return (data or {}).get("data") or data or {}


async def find_sales_invoice_by_po_no(po_no: str) -> Optional[str]:
    async with httpx.AsyncClient(timeout=30.0, headers=_auth_headers(), verify=False) as client:
        existing = await _find_one(client, "Sales Invoice", [["po_no", "=", po_no]])
        return existing["name"] if existing else None


async def find_sales_order_by_po_no(po_no: str) -> Optional[str]:
    async with httpx.AsyncClient(timeout=30.0, headers=_auth_headers(), verify=False) as client:
        existing = await _find_one(client, "Sales Order", [["po_no", "=", po_no]])
        return existing["name"] if existing else None


async def build_return_items_from_si(si_name: str) -> List[Dict[str, Any]]:
    """Build return items list from an SI's existing items."""
    si = await get_sales_invoice(si_name)
    rows: List[Dict[str, Any]] = []
    for it in (si.get("items") or []):
        code = (it.get("item_code") or "").strip()
        if not code:
            continue
        qty = float(it.get("qty") or 0.0) or 0.0
        rate = float(it.get("rate") or 0.0) or 0.0
        warehouse = it.get("warehouse")
        row = {"item_code": code, "qty": qty, "rate": rate}
        if warehouse:
            row["warehouse"] = warehouse
        rows.append(row)
    return rows


async def create_sales_invoice_return(
    *,
    si_name: str,
    return_items: List[Dict[str, Any]],
    posting_date: Optional[str] = None,
    update_stock: bool = False,
) -> str:
    """
    Create and SUBMIT a Sales Invoice Return (credit note) against an SI.
    `return_items` expects positive qty; we'll negate for the return.
    """
    base = _erp_base()
    if not base:
        raise RuntimeError("ERP_URL not configured")

    # Fetch original SI for context
    si_doc = await get_sales_invoice(si_name)
    company = si_doc.get("company")
    customer = si_doc.get("customer")
    po_no = si_doc.get("po_no")

    # Build the return SI
    items = []
    for r in (return_items or []):
        code = (r.get("item_code") or "").strip()
        if not code:
            continue
        qty = float(r.get("qty") or 0.0)
        rate = float(r.get("rate") or 0.0)
        if qty <= 0:
            continue
        row = {
            "item_code": code,
            "qty": -abs(qty),  # negative on return
            "rate": rate,
        }
        if r.get("warehouse"):
            row["warehouse"] = r["warehouse"]
        items.append(row)

    if not items:
        raise ValueError("No valid return items to create SI Return")

    doc: Dict[str, Any] = {
        "doctype": "Sales Invoice",
        "is_return": 1,
        "return_against": si_name,
        "company": company,
        "customer": customer,
        "items": items,
        "update_stock": 1 if update_stock else 0,
    }
    if posting_date:
        doc["posting_date"] = _iso_date(posting_date)
        doc["set_posting_time"] = 1
    # Keep link to Woo po_no if present (optional)
    if po_no:
        doc["po_no"] = po_no

    async with httpx.AsyncClient(timeout=60.0, headers=_auth_headers(), verify=False) as client:
        created = await _insert_doc(client, doc)
        submitted = await _submit_doc(client, "Sales Invoice", created["name"])
        return submitted["name"]


async def create_refund_payment_entry(
    *,
    si_return_name: str,
    mode_of_payment: str,
    reference_no: Optional[str] = None,
    reference_date: Optional[str] = None,
) -> str:
    """
    Create and submit a Payment Entry for a return (customer refund → Pay).
    """
    base = _erp_base()
    gen_url = f"{base}/api/method/erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry"
    async with httpx.AsyncClient(timeout=30.0, verify=False, headers=_auth_headers()) as client:
        gen = await client.post(gen_url, json={"dt": "Sales Invoice", "dn": si_return_name})
        gen.raise_for_status()
        payload = gen.json() or {}
        doc = payload.get("message") or payload.get("data") or payload

        if not isinstance(doc, dict) or doc.get("doctype") != "Payment Entry":
            raise RuntimeError(f"Unexpected response from get_payment_entry for return {si_return_name}: {payload!r}")

        # Force direction to PAY (refund to customer)
        doc["payment_type"] = "Pay"
        doc["mode_of_payment"] = mode_of_payment or "Bank"
        if reference_no:
            doc["reference_no"] = reference_no
        if reference_date:
            doc["reference_date"] = _iso_date(reference_date)

        # Ensure allocation to the return SI (negative outstanding)
        refs = doc.get("references") or []
        for r in refs:
            if r.get("reference_doctype") == "Sales Invoice" and r.get("reference_name") == si_return_name:
                outstanding = abs(float(r.get("outstanding_amount") or 0.0))
                if outstanding:
                    r["allocated_amount"] = outstanding

        # Create & submit
        create_url = f"{base}/api/resource/Payment Entry"
        created = await client.post(create_url, json=doc)
        created.raise_for_status()
        created_doc = (created.json() or {}).get("data") or {}
        pe_name = created_doc.get("name") or doc.get("name")
        if not pe_name:
            raise RuntimeError("Refund Payment Entry create failed (no name)")

        submit_url = f"{base}/api/resource/Payment Entry/{pe_name}"
        submitted = await client.put(submit_url, json={"docstatus": 1})
        submitted.raise_for_status()
        return pe_name


async def cancel_sales_invoice(name: str) -> str:
    """Cancel SI if submitted; idempotent if already cancelled."""
    async with httpx.AsyncClient(timeout=30.0, headers=_auth_headers(), verify=False) as client:
        try:
            # Preferred method
            out = await _post(client, "/api/method/frappe.client.cancel", {"doctype": "Sales Invoice", "name": name})
            _ = out
        except httpx.HTTPStatusError:
            # Fallback
            await _put(client, f"/api/resource/Sales Invoice/{name}", {"docstatus": 2})
    return name


async def cancel_sales_order(name: str) -> str:
    """Cancel SO if submitted; idempotent if already cancelled."""
    async with httpx.AsyncClient(timeout=30.0, headers=_auth_headers(), verify=False) as client:
        try:
            out = await _post(client, "/api/method/frappe.client.cancel", {"doctype": "Sales Order", "name": name})
            _ = out
        except httpx.HTTPStatusError:
            await _put(client, f"/api/resource/Sales Order/{name}", {"docstatus": 2})
    return name


# ===================== Helpers / ERPNext wrappers =============================

async def _find_one(client: httpx.AsyncClient, doctype: str, filters: List[Any]) -> Optional[Dict[str, Any]]:
    try:
        data = await _get(client, f"/api/resource/{doctype}", params=_filters_param(filters))
        results = (data or {}).get("data") or []
        return results[0] if results else None
    except httpx.HTTPStatusError as e:
        # 404 means not found (for some installations), treat as empty
        logger.debug("find_one %s %s failed: %s", doctype, filters, e)
        return None


async def _insert_doc(client: httpx.AsyncClient, doc: Dict[str, Any]) -> Dict[str, Any]:
    payload = {"doc": doc}
    out = await _post(client, "/api/method/frappe.client.insert", payload)
    return out["message"] if "message" in out else out


async def _submit_doc(client: httpx.AsyncClient, doctype: str, name: str) -> Dict[str, Any]:
    payload = {"doctype": doctype, "name": name}
    out = await _post(client, "/api/method/frappe.client.submit", payload)
    return out["message"] if "message" in out else out


async def _ensure_customer(
    client: httpx.AsyncClient,
    customer_name: str,
    email: Optional[str],
    phone: Optional[str],
) -> str:
    # Prefer lookup by email
    if email:
        found = await _find_one(client, "Customer", [["email_id", "=", email]])
        if found:
            return found["name"]
    # Else by exact customer_name
    found = await _find_one(client, "Customer", [["customer_name", "=", customer_name]])
    if found:
        return found["name"]

    # Create
    doc = {
        "doctype": "Customer",
        "customer_name": customer_name,
        "customer_group": DEFAULT_CUSTOMER_GROUP,
        "territory": DEFAULT_TERRITORY,
    }
    if email:
        doc["email_id"] = email
    if phone:
        doc["mobile_no"] = phone

    created = await _insert_doc(client, doc)
    return created["name"]


def _addr_fields_from(src: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "address_line1": (src.get("address_line1") or src.get("line1") or src.get("address1") or "").strip() or "—",
        "address_line2": (src.get("address_line2") or src.get("line2") or src.get("address2") or "") or "",
        "city": (src.get("city") or src.get("town") or "") or "",
        "state": (src.get("state") or src.get("province") or "") or "",
        "pincode": (src.get("pincode") or src.get("postal_code") or src.get("zip") or "") or "",
        "country": (src.get("country") or "") or "",
        "phone": (src.get("phone") or "") or "",
        "email_id": (src.get("email") or "") or "",
    }


async def _ensure_address(
    client: httpx.AsyncClient,
    customer_name: str,
    address_type: str,
    src: Dict[str, Any],
) -> Optional[str]:
    if not src:
        return None

    # Idempotency heuristic: same title + type + line1 + pincode
    title = (src.get("address_title") or src.get("name") or customer_name).strip()
    line1 = (src.get("address_line1") or src.get("line1") or src.get("address1") or "").strip()
    pincode = (src.get("pincode") or src.get("postal_code") or src.get("zip") or "").strip()

    filters = [
        ["address_title", "=", title],
        ["address_type", "=", address_type],
    ]
    if line1:
        filters.append(["address_line1", "=", line1])
    if pincode:
        filters.append(["pincode", "=", pincode])

    found = await _find_one(client, "Address", filters)
    if found:
        return found["name"]

    fields = _addr_fields_from(src)
    doc = {
        "doctype": "Address",
        "address_title": title,
        "address_type": address_type,
        **fields,
        "links": [{"link_doctype": "Customer", "link_name": customer_name}],
    }
    created = await _insert_doc(client, doc)
    return created["name"]


def _item_rows_from(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for li in items or []:
        sku = (li.get("sku") or li.get("item_code") or "").strip()
        if not sku:
            # we intentionally skip items without SKU
            continue
        qty = float(li.get("qty") or li.get("quantity") or 0) or 0.0
        rate = float(li.get("rate") or li.get("price") or li.get("rate_excl_tax") or 0) or 0.0
        row = {"item_code": sku, "qty": qty, "rate": rate}
        if DEFAULT_WAREHOUSE:
            row["warehouse"] = DEFAULT_WAREHOUSE
        # optional description
        desc = (li.get("name") or li.get("description") or "").strip()
        if desc:
            row["description"] = desc
        rows.append(row)
    return rows


async def _ensure_sales_order(
    client: httpx.AsyncClient,
    *,
    po_no: str,
    customer: str,
    items: List[Dict[str, Any]],
    billing_address: Optional[str],
    shipping_address: Optional[str],
    company: Optional[str],
) -> str:
    existing = await _find_one(client, "Sales Order", [["po_no", "=", po_no]])
    if existing:
        return existing["name"]

    today = date.today()
    doc = {
        "doctype": "Sales Order",
        "customer": customer,
        "po_no": po_no,
        "transaction_date": str(today),
        "delivery_date": str(today + timedelta(days=7)),
        "items": _item_rows_from(items),
    }
    if company:
        doc["company"] = company
    if billing_address:
        doc["customer_address"] = billing_address
    if shipping_address:
        doc["shipping_address_name"] = shipping_address

    created = await _insert_doc(client, doc)
    return created["name"]


async def _make_sales_invoice_from_so(client: httpx.AsyncClient, so_name: str) -> Dict[str, Any]:
    """
    Uses ERPNext whitelisted method to build a Sales Invoice doc (unsaved).
    """
    payload = {"source_name": so_name}
    out = await _post(client, "/api/method/erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice", payload)
    doc = out.get("message") if isinstance(out, dict) else out
    if not isinstance(doc, dict):
        raise ValueError(f"Unexpected SI make response: {out!r}")
    return doc


async def _make_delivery_note_from_si(client: httpx.AsyncClient, si_name: str) -> Dict[str, Any]:
    """
    Uses ERPNext whitelisted method to build a Delivery Note doc (unsaved) from a Sales Invoice.
    """
    payload = {"source_name": si_name}
    out = await _post(
        client,
        "/api/method/erpnext.accounts.doctype.sales_invoice.sales_invoice.make_delivery_note",
        payload,
    )
    doc = out.get("message") if isinstance(out, dict) else out
    if not isinstance(doc, dict):
        raise ValueError(f"Unexpected DN make response: {out!r}")
    return doc
