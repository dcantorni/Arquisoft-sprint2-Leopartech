"""
Redis cache-aside for token validation results.

Architecture: manejador_cloud, manejador_seguridad, and TenantAuthMiddleware
call GET /auth/validate on every inbound request. Without caching this creates
a DB round-trip on every API call across the platform.

Cache key:  auth:token:<sha256(token)>
TTL:        300 s (5 minutes) — matches LOCAL_JWT_ACCESS_EXPIRY default
Backend:    Redis DB 0 (REDIS_URL env var, shared with token blacklist in future)

Graceful degradation: any Redis error falls through to the source validator.
This means a Redis outage degrades performance but never breaks auth.
"""
import hashlib
import json
import logging
import os

import redis

logger = logging.getLogger(__name__)

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_TOKEN_TTL = 300  # 5 minutes — must be ≤ LOCAL_JWT_ACCESS_EXPIRY

# Lazy singleton — created on first use, not at import time
_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(_REDIS_URL, decode_responses=True, socket_timeout=1)
    return _client


def _cache_key(token: str) -> str:
    """SHA-256 of the full token so two tokens never share a key."""
    return "auth:token:" + hashlib.sha256(token.encode()).hexdigest()


def get_cached(token: str) -> dict | None:
    """
    Return the cached validation result for this token, or None on cache miss
    / Redis error.
    """
    try:
        raw = _get_client().get(_cache_key(token))
        if raw:
            logger.debug("token_cache: HIT")
            return json.loads(raw)
    except Exception as exc:
        logger.warning("token_cache.get error (fallback to source): %s", exc)
    return None


def set_cached(token: str, result: dict) -> None:
    """
    Store a validation result for TTL seconds. Silently swallows Redis errors
    so a Redis outage never breaks auth.
    """
    try:
        _get_client().setex(
            _cache_key(token),
            _TOKEN_TTL,
            json.dumps(result),
        )
        logger.debug("token_cache: SET ttl=%d", _TOKEN_TTL)
    except Exception as exc:
        logger.warning("token_cache.set error (non-fatal): %s", exc)


def invalidate(token: str) -> None:
    """
    Explicitly evict a token from the cache (e.g. on logout).
    Currently unused but available for future blacklist integration.
    """
    try:
        _get_client().delete(_cache_key(token))
    except Exception as exc:
        logger.warning("token_cache.invalidate error: %s", exc)


def ping() -> bool:
    """Return True if Redis is reachable, False otherwise."""
    try:
        return _get_client().ping()
    except Exception:
        return False
