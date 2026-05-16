from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.core.config import settings


def _normalize_database_url(url: str) -> str:
    """Ensure SQLite path exists and use a local file when /data isn't writable."""
    if url.startswith("sqlite+aiosqlite:///"):
        path_part = url.replace("sqlite+aiosqlite:///", "", 1)
        if path_part.startswith("/"):
            target = Path(path_part)
        else:
            target = Path(path_part).resolve()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            probe = target.parent / ".write_probe"
            probe.write_text("ok")
            probe.unlink(missing_ok=True)
        except OSError:
            fallback = Path.cwd() / "app.db"
            return f"sqlite+aiosqlite:///{fallback}"
    return url


DATABASE_URL = _normalize_database_url(settings.database_url)

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables registered with SQLModel metadata."""
    from app.data import models  # noqa: F401 - register models

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
