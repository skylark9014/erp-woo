# app/db.py
from __future__ import annotations

import os
import pathlib
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncEngine,
    async_sessionmaker,
    AsyncSession,
)

from app.config import settings

logger = logging.getLogger("uvicorn.error")

_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def _resolve_dsn() -> str:
    """
    Prefer settings.DATABASE_URL, then env var DATABASE_URL,
    else default to a local SQLite database under ./data/.
    """
    dsn = (
        getattr(settings, "DATABASE_URL", None)
        or os.getenv("DATABASE_URL")
        or "sqlite+aiosqlite:///./data/middleware.db"
    )

    # If using SQLite, make sure the folder exists so SQLAlchemy can create the file.
    if dsn.startswith("sqlite"):
        try:
            # Handle sqlite+aiosqlite:///./data/middleware.db
            # or sqlite+aiosqlite:////code/data/middleware.db
            sep = "///" if "///" in dsn else "//"
            path_part = dsn.split(sep, 1)[1] if sep in dsn else ""
            if path_part:
                path = pathlib.Path(path_part).resolve()
                path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("[DB] Could not ensure SQLite directory exists: %s", e)

    return dsn


def get_engine() -> AsyncEngine:
    """
    Lazily create a global AsyncEngine and sessionmaker.
    """
    global _engine, _sessionmaker
    if _engine is None:
        dsn = _resolve_dsn()
        _engine = create_async_engine(
            dsn,
            echo=False,
            pool_pre_ping=True,
            future=True,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
        logger.info("[DB] engine initialized for %s", dsn)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """
    Get the global async session factory.
    """
    global _sessionmaker
    if _sessionmaker is None:
        get_engine()
    # _sessionmaker will be set by get_engine()
    return _sessionmaker  # type: ignore[return-value]


async def init_db() -> None:
    """
    Ensure the engine is created and a first connection can be acquired.
    (We don't create tables here because we haven't defined ORM models yet.)
    """
    eng = get_engine()
    try:
        async with eng.begin() as conn:
            # Simple no-op to validate connectivity
            await conn.run_sync(lambda _: None)

    except Exception as e:
        logger.error("[DB] initial connect failed: %s", e)
        raise
