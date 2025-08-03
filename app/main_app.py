#=================================================================
# app/main_app.py
# FastAPI application entry-point (production ready).
#=================================================================

import logging

from fastapi import FastAPI, Depends, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.routes import router as sync_router
from app.admin_routes import router as admin_router
from fastapi.responses import FileResponse
from app.config import settings

ADMIN_USER = settings.ADMIN_USER
ADMIN_PASS = settings.ADMIN_PASS

# --- FastAPI instance ---
app = FastAPI(
    title="ERPNext WooCommerce Integration Middleware",
    description="Middleware for syncing ERPNext with WooCommerce.",
    debug=True,        # <<< enable debug
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
# silence HTTPX chatter
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)   # if you also want to silence the core layer

# --- Static Files (for Admin UI) ---
# Serves /static/* and /admin_panel.html
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# --- CORS (Cross-Origin Resource Sharing) ---
origins = settings.CORS_ORIGINS
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
#@app.exception_handler(Exception)
#async def global_exception_handler(request: Request, exc: Exception):
#    logger.exception(f"Unhandled error: {exc}")
#    return JSONResponse(
#        status_code=500,
#        content={"detail": "Internal server error"},
#    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # This will print the full stack trace to your container logs
    logger.error("Full sync failed", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Sync failed: {str(exc)}"},
    )

