# app/config.py
# ----------------------------------------------------------------
# import configuration variables to be used throughout the project
# ----------------------------------------------------------------
import os
from dotenv import load_dotenv

# Load .env once
load_dotenv()

class Settings:
    # ── ERPNext ──────────────────────────────────────────────────────────────
    ERP_URL: str = os.getenv("ERP_URL", "").rstrip("/")
    ERP_API_KEY: str = os.getenv("ERP_API_KEY", "")
    ERP_API_SECRET: str = os.getenv("ERP_API_SECRET", "")
    ERP_USER: str = os.getenv("ERP_USER", "")
    ERP_PASS: str = os.getenv("ERP_PASS", "")
    ERP_SELLING_PRICE_LIST: str = os.getenv("ERP_SELLING_PRICE_LIST", "Standard Selling")

    # ── WooCommerce ──────────────────────────────────────────────────────────
    WC_BASE_URL: str = os.getenv("WC_BASE_URL", "")
    WC_API_KEY: str = os.getenv("WC_API_KEY", "")
    WC_API_SECRET: str = os.getenv("WC_API_SECRET", "")
    WP_USERNAME: str = os.getenv("WP_USERNAME", "")
    WP_PASSWORD: str = os.getenv("WP_APP_PASSWORD", "")  # or WP_PASSWORD, depending on your .env

    # ── Admin Panel ─────────────────────────────────────────────────────────
    ADMIN_USER: str = os.getenv("ADMIN_USER", "admin")
    ADMIN_PASS: str = os.getenv("ADMIN_PASS", "changeme")

    # ── Site-level Basic Auth (ngrok / Traefik) ────────────────────────
    WC_BASIC_USER: str = os.getenv("WC_BASIC_USER", "")
    WC_BASIC_PASS: str = os.getenv("WC_BASIC_PASS", "")

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Comma-separated list in .env, e.g. "https://example.com,https://foo.bar"
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "*").split(",")

settings = Settings()
