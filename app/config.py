# ----------------------------------------------------------------
# Import configuration variables to be used throughout the project
# ----------------------------------------------------------------
import os
import json as _json
from dotenv import load_dotenv

# Load .env (allow container env to override file values)
load_dotenv(override=True)


def _rstrip_slash(s: str) -> str:
    return (s or "").rstrip("/")


def _get_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _get_json_map(name: str, default: dict | None = None) -> dict:
    raw = os.getenv(name, "")
    if not raw:
        return default or {}
    try:
        return _json.loads(raw)
    except Exception:
        return default or {}


class Settings:
    # ── ERPNext ──────────────────────────────────────────────────────────────
    ERP_URL: str = _rstrip_slash(os.getenv("ERP_URL", ""))
    ERP_API_KEY: str = os.getenv("ERP_API_KEY", "")
    ERP_API_SECRET: str = os.getenv("ERP_API_SECRET", "")
    ERP_USER: str = os.getenv("ERP_USER", "")
    ERP_PASS: str = os.getenv("ERP_PASS", "")
    ERP_SELLING_PRICE_LIST: str = os.getenv("ERP_SELLING_PRICE_LIST", "Standard Selling")

    # Common company/warehouse hints (optional)
    ERP_COMPANY: str = os.getenv("ERP_COMPANY", "")  # leave empty to let ERPNext choose default
    ERP_DEFAULT_WAREHOUSE: str = os.getenv("ERP_DEFAULT_WAREHOUSE", "")

    # Sales Invoice stock & delivery toggles (mutually exclusive by convention)
    ERP_SI_UPDATE_STOCK: bool = _get_bool("ERP_SI_UPDATE_STOCK", False)
    ERP_CREATE_DN: bool = _get_bool("ERP_CREATE_DN", False)

    # Shipping as a non-stock item (optional)
    ERP_SHIPPING_ITEM_CODE: str = os.getenv("ERP_SHIPPING_ITEM_CODE", "")

    # Taxes: either use a fixed account (exact total via "Actual"), or a template
    ERP_TAX_ACCOUNT: str = os.getenv("ERP_TAX_ACCOUNT", "")
    ERP_SALES_TAX_TEMPLATE: str = os.getenv("ERP_SALES_TAX_TEMPLATE", "")

    # ── WooCommerce / WordPress ──────────────────────────────────────────────
    WC_BASE_URL: str = _rstrip_slash(os.getenv("WC_BASE_URL", ""))
    WC_API_KEY: str = os.getenv("WC_API_KEY", "")
    WC_API_SECRET: str = os.getenv("WC_API_SECRET", "")

    # WP auth (Application Password)
    WP_USERNAME: str = os.getenv("WP_USERNAME", "")
    WP_PASSWORD: str = os.getenv("WP_APP_PASSWORD", "")  # keep the name WP_PASSWORD in code

    # Optional explicit WP API root; if missing, fall back to WC_BASE_URL/wp-json
    WP_API_URL: str = _rstrip_slash(
        os.getenv("WP_API_URL", "") or (
            _rstrip_slash(os.getenv("WC_BASE_URL", "")) + "/wp-json"
            if os.getenv("WC_BASE_URL") else ""
        )
    )

    # Webhook HMAC secret (support both names)
    WOO_WEBHOOK_SECRET: str = os.getenv("WOO_WEBHOOK_SECRET", "") or os.getenv("WC_WEBHOOK_SECRET", "")
    WOO_WEBHOOK_DEBUG: bool = _get_bool("WOO_WEBHOOK_DEBUG", False)

    # Mode-of-Payment mapping (gateway → ERP Mode of Payment)
    WOO_MODE_OF_PAYMENT_MAP: dict = _get_json_map("WOO_MODE_OF_PAYMENT_MAP", {})

    # ── Admin Panel ──────────────────────────────────────────────────────────
    ADMIN_USER: str = os.getenv("ADMIN_USER", "admin")
    ADMIN_PASS: str = os.getenv("ADMIN_PASS", "changeme")

    # ── Site-level Basic Auth (ngrok / Traefik) ─────────────────────────────
    WC_BASIC_USER: str = os.getenv("WC_BASIC_USER", "")
    WC_BASIC_PASS: str = os.getenv("WC_BASIC_PASS", "")

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Comma-separated list in .env, e.g. "https://example.com, https://foo.bar"
    CORS_ORIGINS: list[str] = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

    # ── Paths ────────────────────────────────────────────────────────────────
    SHIPPING_PARAMS_PATH: str = os.getenv("SHIPPING_PARAMS_PATH", "app/mapping/shipping_params.json")
    MAPPING_STORE_PATH: str = os.getenv("MAPPING_STORE_PATH", "app/mapping/mapping_store.json")
    # data directory is mounted: ./data ↔ /code/data (see docker-compose)
    DATA_DIR: str = os.getenv("DATA_DIR", "/code/data")


settings = Settings()
