from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PersonBlock:
    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None


@dataclass
class AddressBlock:
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    phone: str | None = None
    email: str | None = None


@dataclass
class LineItem:
    sku: str
    name: str
    qty: float
    rate: float           # unit price (excl. tax)
    line_total: float     # total for the line AFTER discounts (excl. tax)
    product_id: Optional[int] = None
    variation_id: Optional[int] = None


@dataclass
class NormalizedOrder:
    order_id: int
    number: str | None
    date_created: str | None
    status: str | None
    currency: str | None

    subtotal: float               # sum(line_items[].line_total), excl. tax
    discount_total: float         # root discount_total
    shipping_total: float         # excl. tax
    tax_total: float              # root total_tax
    total: float                  # grand total (incl. tax)

    set_paid: bool                # strict flag from woo payload

    payment_method: str | None
    payment_title: str | None
    transaction_id: str | None
    order_key: str | None
    paid_at: str | None

    customer_id: Optional[int]
    customer: PersonBlock
    billing: AddressBlock
    shipping: AddressBlock

    items: List[LineItem] = field(default_factory=list)


def _get(d: Dict[str, Any] | None, key: str, default=None):
    if not isinstance(d, dict):
        return default
    return d.get(key, default)


def _coerce_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _mk_person(blob: Dict[str, Any] | None) -> PersonBlock:
    first = _get(blob, "first_name")
    last = _get(blob, "last_name")
    email = _get(blob, "email")
    phone = _get(blob, "phone")
    company = _get(blob, "company") or None
    full = " ".join([p for p in [first, last] if p]).strip() or None
    return PersonBlock(full_name=full, first_name=first, last_name=last, email=email, phone=phone, company=company)


def _mk_addr(blob: Dict[str, Any] | None) -> AddressBlock:
    return AddressBlock(
        line1=_get(blob, "address_1"),
        line2=_get(blob, "address_2"),
        city=_get(blob, "city"),
        state=_get(blob, "state"),
        postal_code=_get(blob, "postcode"),
        country=_get(blob, "country"),
        phone=_get(blob, "phone"),
        email=_get(blob, "email"),
    )


def normalize_order(order_json: Dict[str, Any]) -> NormalizedOrder:
    line_items: List[LineItem] = []
    for li in (order_json.get("line_items") or []):
        sku = (li.get("sku") or "").strip()
        if not sku:
            # if there is no SKU, skip; we’ll log upstream in worker
            continue
        qty = _coerce_float(li.get("quantity"), 0.0)
        # Woo sends unit "price" (excl tax) and "total" per line (excl tax, post-discount)
        rate = _coerce_float(li.get("price"), 0.0)
        line_total = _coerce_float(li.get("total"), rate * qty)
        line_items.append(LineItem(
            sku=sku,
            name=str(li.get("name") or sku),
            qty=qty,
            rate=rate,
            line_total=line_total,
            product_id=li.get("product_id"),
            variation_id=li.get("variation_id"),
        ))

    billing_blob = order_json.get("billing") or {}
    shipping_blob = order_json.get("shipping") or {}
    bill_person = _mk_person(billing_blob)
    bill_addr = _mk_addr(billing_blob)
    ship_addr = _mk_addr(shipping_blob)

    subtotal = sum(li.line_total for li in line_items)  # excl tax
    discount_total = _coerce_float(order_json.get("discount_total"), 0.0)
    shipping_total = _coerce_float(order_json.get("shipping_total"), 0.0)  # excl tax
    tax_total = _coerce_float(order_json.get("total_tax"), 0.0)
    total = _coerce_float(order_json.get("total"), 0.0)

    return NormalizedOrder(
        order_id=int(order_json.get("id")),
        number=str(order_json.get("number") or "") or None,
        date_created=order_json.get("date_created") or order_json.get("date_created_gmt"),
        status=str(order_json.get("status") or None),
        currency=str(order_json.get("currency") or "ZAR"),

        subtotal=subtotal,
        discount_total=discount_total,
        shipping_total=shipping_total,
        tax_total=tax_total,
        total=total,

        set_paid=bool(order_json.get("set_paid") is True),

        payment_method=_get(order_json, "payment_method"),
        payment_title=_get(order_json, "payment_method_title"),
        transaction_id=_get(order_json, "transaction_id"),
        order_key=_get(order_json, "order_key"),
        paid_at=_get(order_json, "date_paid") or _get(order_json, "date_paid_gmt"),

        customer_id=order_json.get("customer_id"),
        customer=bill_person,
        billing=bill_addr,
        shipping=ship_addr,
        items=line_items,
    )
