#=================================================================
# app/main_app.py
# FastAPI application entry-point.
# Loads configuration and includes all routes for the middleware.
#=================================================================

from fastapi import FastAPI
from dotenv import load_dotenv
from app.routes import router as sync_router

# Load environment variables from .env
load_dotenv()

# Create FastAPI app instance
app = FastAPI(
    title="ERPNext WooCommerce Integration Middleware",
    description="Middleware for syncing ERPNext with WooCommerce."
)

# Include the sync routes under the /api prefix
app.include_router(sync_router, prefix="/api", tags=["Sync"])

@app.get("/")
async def home():
    """
    Root endpoint: Confirms the middleware is operational.
    """
    return {"status": "running", "service": "ERPNext WooCommerce Middleware"}
