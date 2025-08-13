#=================================================================
# app/main_app.py
# FastAPI application entry-point (no static serving).
#=================================================================

import logging
import secrets

from fastapi import FastAPI, Depends, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.routes import router as api_router          # Public API under /api/*
from app.admin_routes import router as admin_router  # Admin API under /admin/api/*
from app.config import settings

ADMIN_USER = settings.ADMIN_USER
ADMIN_PASS = settings.ADMIN_PASS

# --- FastAPI instance ---
app = FastAPI(
    title="ERPNext WooCommerce Integration Middleware",
    description="Middleware for syncing ERPNext with WooCommerce.",
    debug=True,
)

# --- Logging setup (console, INFO level) ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s"
)
logger = logging.getLogger("uvicorn.error")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# --- CORS ---
origins = settings.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# NOTE: If CORS_ORIGINS is ["*"] AND allow_credentials=True, browsers will block.
# Use a concrete origin (e.g., https://records.techniclad.co.za) in production.

# --- Simple HTTP Basic Auth for /admin/api/* ---
security = HTTPBasic()

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username, ADMIN_USER)
    ok_pass = secrets.compare_digest(credentials.password, ADMIN_PASS)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

# --- Include routers ---
# Public API (stays at /api/*)
app.include_router(api_router)

# Admin API (mounted under /admin/api/* and protected)
# admin_router already has prefix="/api" inside it, so final path is /admin/api/*
app.include_router(
    admin_router,
    prefix="/admin",
    dependencies=[Depends(verify_admin)],
)

# --- Root endpoint ---
@app.get("/")
async def home():
    return {"status": "running", "service": "ERPNext WooCommerce Middleware"}

# --- Global error handler (keeps full stack trace in logs) ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Sync failed: {str(exc)}"},
    )
