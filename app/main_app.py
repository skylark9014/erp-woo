#=================================================================
# app/main_app.py
# FastAPI application entry-point (production ready).
#=================================================================

import logging
import secrets

from fastapi import FastAPI, Depends, Request, HTTPException, status
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.routes import router as api_router          # NEW + legacy endpoints under /api/*
from app.admin_routes import router as admin_router  # Admin UI endpoints under /admin/api/*
from app.config import settings

ADMIN_USER = settings.ADMIN_USER
ADMIN_PASS = settings.ADMIN_PASS

# --- FastAPI instance ---
app = FastAPI(
    title="ERPNext WooCommerce Integration Middleware",
    description="Middleware for syncing ERPNext with WooCommerce.",
    debug=True,
)

@app.get("/admin", include_in_schema=False)
async def serve_admin_panel():
    # Adjust path if needed
    return FileResponse("app/static/admin_panel.html", media_type="text/html")

# --- Logging setup (console, INFO level) ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s"
)
logger = logging.getLogger("uvicorn.error")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# --- Static Files (for Admin UI) ---
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# --- CORS ---
origins = settings.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

# Only secure /admin/api/* routes (not the public /api/*)
from fastapi.routing import APIRoute
def secure_router(router):
    for route in router.routes:
        if isinstance(route, APIRoute):
            route.dependant.dependencies.append(Depends(verify_admin))
    return router

# --- Include routers ---
# IMPORTANT: Do NOT add another prefix here, api_router already has prefix="/api" inside routes.py
app.include_router(api_router)                     # -> /api/*
app.include_router(secure_router(admin_router))    # -> /admin/api/* (protected)

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
