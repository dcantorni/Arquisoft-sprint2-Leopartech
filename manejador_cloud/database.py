"""
Async SQLAlchemy engine configuration.

Read path  → DATABASE_READ_HOST (RDS read replica, CHANGE 3 in main.tf)
Write path → DATABASE_HOST      (RDS primary — only seed.py + health check)

Connection pool sizes are tuned for t3.small (2 vCPU, 2 GB RAM).
"""
import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

_DB_USER = os.environ.get("DATABASE_USER", "admin")
_DB_PASS = os.environ.get("DATABASE_PASSWORD", "admin123")
_DB_NAME = os.environ.get("DATABASE_NAME", "cloud_db")
_WRITE_HOST = os.environ.get("DATABASE_HOST", "localhost")
_READ_HOST = os.environ.get("DATABASE_READ_HOST", _WRITE_HOST)  # fallback to primary if no replica
_DB_PORT = os.environ.get("DATABASE_PORT", "5432")


def _url(host: str) -> str:
    return f"postgresql+asyncpg://{_DB_USER}:{_DB_PASS}@{host}:{_DB_PORT}/{_DB_NAME}"


# All reads go to the replica (CQRS read path — architecture.md §3 CHANGE 3)
read_engine = create_async_engine(
    _url(_READ_HOST),
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=300,
    echo=os.environ.get("DB_ECHO", "false").lower() == "true",
)

# Writes and health check only — keep pool small
write_engine = create_async_engine(
    _url(_WRITE_HOST),
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=300,
    echo=os.environ.get("DB_ECHO", "false").lower() == "true",
)

ReadSession = async_sessionmaker(read_engine, expire_on_commit=False, class_=AsyncSession)
WriteSession = async_sessionmaker(write_engine, expire_on_commit=False, class_=AsyncSession)


async def get_read_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a read-replica session."""
    async with ReadSession() as session:
        yield session


async def get_write_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a primary (write) session."""
    async with WriteSession() as session:
        yield session
