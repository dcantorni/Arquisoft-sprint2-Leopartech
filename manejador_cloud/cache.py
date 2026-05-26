"""
Redis async cache — dashboard data, 30 s TTL.
DB index 1 (same as manejador_cloud's Django predecessor used in main.tf CHANGE 4).

Key pattern:  cloud:dashboard:{empresa_id}:{resource_type}
Wildcard invalidation pattern: cloud:dashboard:*

On Lambda run → call invalidate_all() to bust stale cost data.
Cache miss → caller queries read replica → stores result here.
"""
import json
import logging
import os
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/1")
_TTL = int(os.environ.get("CLOUD_CACHE_TTL", "30"))  # 30 s — architecture §3 CHANGE 4

_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            _REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
    return _redis


def _key(empresa_id: str, resource_type: str) -> str:
    return f"cloud:dashboard:{empresa_id}:{resource_type}"


async def get(empresa_id: str, resource_type: str) -> Any | None:
    """Return cached JSON-decoded value or None on miss / error."""
    try:
        raw = await _get_redis().get(_key(empresa_id, resource_type))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning("cache.get error (%s/%s): %s", empresa_id, resource_type, exc)
        return None


async def set(empresa_id: str, resource_type: str, value: Any) -> None:
    """Store value as JSON with TTL. Silently swallows Redis errors."""
    try:
        await _get_redis().set(
            _key(empresa_id, resource_type),
            json.dumps(value, default=str),
            ex=_TTL,
        )
    except Exception as exc:
        logger.warning("cache.set error (%s/%s): %s", empresa_id, resource_type, exc)


async def invalidate(empresa_id: str, resource_type: str) -> None:
    """Delete a single cache key."""
    try:
        await _get_redis().delete(_key(empresa_id, resource_type))
    except Exception as exc:
        logger.warning("cache.invalidate error (%s/%s): %s", empresa_id, resource_type, exc)


async def invalidate_all() -> int:
    """
    Delete all cloud:dashboard:* keys.
    Called after cloud_collector Lambda runs to flush stale cost data.
    Returns number of keys deleted.
    """
    try:
        r = _get_redis()
        keys = await r.keys("cloud:dashboard:*")
        if keys:
            deleted = await r.delete(*keys)
            logger.info("cache.invalidate_all: deleted %d keys", deleted)
            return deleted
        return 0
    except Exception as exc:
        logger.warning("cache.invalidate_all error: %s", exc)
        return 0


async def ping() -> bool:
    """Health check — True if Redis responds to PING."""
    try:
        return await _get_redis().ping()
    except Exception:
        return False
