# app/main_app.py

from fastapi import FastAPI
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Initialize FastAPI application instance
app = FastAPI(title="ERPNext WooCommerce Integration Middleware",
              description="Middleware service facilitating integration between ERPNext and WooCommerce.")

@app.get("/")
async def home():
    """
    Root Endpoint:
    
    Basic health-check endpoint to verify that the middleware service is operational.

    Returns:
        dict: JSON containing the service status message.
    """
    return {
        "status": "running",
        "service": "ERPNext WooCommerce Middleware"
    }

@app.get("/config")
async def config_check():
    """
    Configuration Check Endpoint:

    Validates the current ERPNext and WooCommerce URLs configured in the .env file.
    Useful for ensuring middleware connectivity settings are correctly set.

    Returns:
        dict: JSON containing the configured ERPNext and WooCommerce URLs.
    """
    return {
        "erpnext_url": os.getenv("ERP_URL"),
        "woocommerce_url": os.getenv("WC_BASE_URL")
    }
