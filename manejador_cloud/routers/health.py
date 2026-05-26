"""GET /health — no auth required."""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from database import read_engine, write_engine
from cache import ping as redis_ping

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", include_in_schema=True, tags=["ops"])
async def health():
    checks: dict[str, str] = {}

    # Read replica reachability
    try:
        async with read_engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["database_read"] = "ok"
    except Exception as exc:
        logger.warning("Health: read replica error: %s", exc)
        checks["database_read"] = "error"

    # Primary reachability
    try:
        async with write_engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["database_write"] = "ok"
    except Exception as exc:
        logger.warning("Health: primary error: %s", exc)
        checks["database_write"] = "error"

    # Redis
    try:
        checks["redis"] = "ok" if await redis_ping() else "error"
    except Exception as exc:
        logger.warning("Health: redis error: %s", exc)
        checks["redis"] = "error"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        content={
            "service": "manejador_cloud",
            "runtime": "fastapi",
            "status": "healthy" if all_ok else "degraded",
            "checks": checks,
        },
        status_code=200 if all_ok else 503,
    )
