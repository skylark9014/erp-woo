# app/config.py
# ----------------------------------------------------------------
# Import configuration variables to be used throughout the project
# ----------------------------------------------------------------
import os
from dotenv import load_dotenv

# Load .env (allow container env to override file values)
load_dotenv(override=True)


def _rstrip_slash(s: str) -> str:
    return (s or "").rstrip("/")


class Settings:
    # ── ERPNext ──────────────────────────────────────────────────────────────
    ERP_URL: str = _rstrip_slash(os.getenv("ERP_URL", ""))
    ERP_API_KEY: str = os.getenv("ERP_API_KEY", "")
    ERP_API_SECRET: str = os.getenv("ERP_API_SECRET", "")
    ERP_USER: str = os.getenv("ERP_USER", "")
    ERP_PASS: str = os.getenv("ERP_PASS", "")
    ERP_SELLING_PRICE_LIST: str = os.getenv("ERP_SELLING_PRICE_LIST", "Standard Selling")

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

    # ── Admin Panel ──────────────────────────────────────────────────────────
    ADMIN_USER: str = os.getenv("ADMIN_USER", "admin")
    ADMIN_PASS: str = os.getenv("ADMIN_PASS", "changeme")

    # ── Site-level Basic Auth (ngrok / Traefik) ─────────────────────────────
    WC_BASIC_USER: str = os.getenv("WC_BASIC_USER", "")
    WC_BASIC_PASS: str = os.getenv("WC_BASIC_PASS", "")

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Comma-separated list in .env, e.g. "https://example.com, https://foo.bar"
    CORS_ORIGINS: list[str] = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

    # ── Shipping params file path ───────────────────────────────────────────
    SHIPPING_PARAMS_PATH: str = os.getenv("SHIPPING_PARAMS_PATH", "app/shipping_prams.json")
    
settings = Settings()
