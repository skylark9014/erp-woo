#=================================================================
# app/main_app.py
# FastAPI application entry-point (production ready).
#=================================================================

import os
from fastapi import FastAPI, Depends, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import logging
from app.routes import router as sync_router
from app.admin_routes import router as admin_router
from fastapi.responses import FileResponse

# --- Load .env and config ---
load_dotenv()

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "changeme")  # Set a secure password in .env!

# --- FastAPI instance ---
app = FastAPI(
    title="ERPNext WooCommerce Integration Middleware",
    description="Middleware for syncing ERPNext with WooCommerce."
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

# --- Static Files (for Admin UI) ---
# Serves /static/* and /admin_panel.html
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# --- CORS (Cross-Origin Resource Sharing) ---
origins = os.getenv("CORS_ORIGINS", "*").split(",")  # List from .env or "*"
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Simple HTTP Basic Auth for /admin/api/* ---
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets

security = HTTPBasic()

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USER)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASS)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

# --- Protect the admin routes by wrapping their router ---
from fastapi.routing import APIRoute

def secure_router(router):
    for route in router.routes:
        if isinstance(route, APIRoute):
            # Only secure the /admin/api/* routes
            route.dependant.dependencies.append(Depends(verify_admin))
    return router

# --- Include routers ---
app.include_router(secure_router(admin_router))  # /admin/api/* protected
app.include_router(sync_router, prefix="/api", tags=["Sync"])

# --- Root endpoint ---
@app.get("/")
async def home():
    return {"status": "running", "service": "ERPNext WooCommerce Middleware"}

# --- Error handler example ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )

