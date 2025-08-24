#=================================================================
# app/main_app.py
# FastAPI application entry-point (no static serving).
#=================================================================

import logging, secrets, asyncio

from fastapi import FastAPI, Depends, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

# Public webhooks (no auth)
from app.webhooks.woo import router as woo_webhooks_router

# Public API under /api/*
from app.routes import router as api_router
from app.routes import compat_router as api_compat_router  # /sync/* aliases

# Admin API originally used by Next.js proxies under /admin/*
from app.admin_routes import router as admin_router

# Integration helpers (public /api/integration/*)
from app.shipping.shipping_api import router as shipping_router
from app.mapping.mapping_api import router as mapping_router
from app.mapping.customer_map_api import router as customer_map_router
from app.webhooks.inbox_api import router as webhook_admin_router

# New: admin backfill & ops router (mounted under /admin/integration/*)
from app.backfill.backfill_api import router as backfill_router

from app.workers.jobs_worker import worker_loop
from app.db import init_db
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

# --- Simple HTTP Basic Auth for /admin/* protected endpoints ---
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

# ---------------- Include routers ----------------

# Webhooks (public)
app.include_router(woo_webhooks_router)  # /webhooks/woo

# Public API
app.include_router(api_router)           # /api/*
app.include_router(api_compat_router)    # /sync/*

# Integration helpers (public /api/integration/*)
app.include_router(shipping_router)           # /api/integration/shipping/*
app.include_router(mapping_router)            # /api/integration/mapping/*
app.include_router(customer_map_router)       # /api/integration/customers/map/*
app.include_router(webhook_admin_router)      # /api/integration/webhooks/*

# Admin API (legacy, used by Next.js proxies hitting /admin/*)
# Keep this to avoid breaking existing admin-ui expectations.
app.include_router(
    admin_router,
    prefix="/admin",
    dependencies=[Depends(verify_admin)],
)

# Admin backfill/ops (direct to FastAPI at /admin/integration/* via Traefik rule)
app.include_router(
    backfill_router,
    dependencies=[Depends(verify_admin)],
)

# Print all registered routes on startup (works with Docker/uvicorn)
print("Registered FastAPI routes:")
for route in app.routes:
    print(f"{route.path} [{','.join(route.methods)}]")

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

# ---- Background worker lifecycle ----
_worker_task: asyncio.Task | None = None
_worker_stop: asyncio.Event | None = None

@app.on_event("startup")
async def _startup():
    # Init DB tables (for mappings, inbox index, etc.)
    await init_db()
    # Start worker
    global _worker_task, _worker_stop
    _worker_stop = asyncio.Event()
    _worker_task = asyncio.create_task(worker_loop(_worker_stop))

@app.on_event("shutdown")
async def _shutdown():
    global _worker_task, _worker_stop
    if _worker_stop:
        _worker_stop.set()
    if _worker_task:
        try:
            await asyncio.wait_for(_worker_task, timeout=5.0)
        except Exception:
            _worker_task.cancel()

#if __name__ == "__main__":
#    import uvicorn
#
#    uvicorn.run(app, host="0.0.0.0", port=8000)