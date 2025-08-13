#=================================================================
# app/main_app.py
# FastAPI application entry-point (no static serving).
#=================================================================

import logging
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Depends, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.routes import router as api_router           # Public API under /api/*
from app.admin_routes import router as admin_router   # Admin API under /admin/api/*
from app.shipping_api import router as shipping_router
from app.config import settings

ADMIN_USER = settings.ADMIN_USER
ADMIN_PASS = settings.ADMIN_PASS

# --- FastAPI instance ---
app = FastAPI(
    title="ERPNext WooCommerce Integration Middleware",
    description="Middleware for syncing ERPNext with WooCommerce.",
    debug=True,
)

# --- Logging setup ---
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

# --- Admin auth for /admin/api/* ---
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

# --- Startup diagnostics (shipping path) ---
def _resolve_shipping_params_path() -> Path:
    raw = getattr(settings, "SHIPPING_PARAMS_PATH", None) or os.getenv(
        "SHIPPING_PARAMS_PATH", "app/mapping/shipping_params.json"
    )
    p = Path(raw)
    return p if p.is_absolute() else Path.cwd() / raw

rp = _resolve_shipping_params_path()
try:
    st = rp.stat()
    logger.info(
        "Shipping params path: env=%r | resolved=%s | exists=%s | size=%s",
        getattr(settings, "SHIPPING_PARAMS_PATH", None) or os.getenv("SHIPPING_PARAMS_PATH", None) or "<default>",
        str(rp),
        rp.exists(),
        st.st_size,
    )
except FileNotFoundError:
    logger.info(
        "Shipping params path: env=%r | resolved=%s | exists=%s",
        getattr(settings, "SHIPPING_PARAMS_PATH", None) or os.getenv("SHIPPING_PARAMS_PATH", None) or "<default>",
        str(rp),
        False,
    )

# --- Routers ---
app.include_router(api_router)  # /api/*
app.include_router(shipping_router, prefix="/api/integration")  # /api/integration/shipping/*
app.include_router(admin_router, prefix="/admin", dependencies=[Depends(verify_admin)])  # /admin/api/*

@app.get("/")
async def home():
    return {"status": "running", "service": "ERPNext WooCommerce Middleware"}

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Sync failed: {str(exc)}"},
    )
