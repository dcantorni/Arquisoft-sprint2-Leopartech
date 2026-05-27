"""
RateLimitMiddleware — Redis sliding-window rate limiter (ASR19).

Controls abusive traffic on POST /projects before any DB work is done.

Configuration (env vars):
  RATE_LIMIT_ENABLED   true|false  — toggle at runtime by restarting uvicorn (default: true)
  RATE_LIMIT_REQUESTS  int         — max requests allowed per window per IP (default: 20)
  RATE_LIMIT_WINDOW    int         — window size in seconds (default: 10)

Run Phase 1 (baseline, no rate limit):
  RATE_LIMIT_ENABLED=false uvicorn main:app --host 0.0.0.0 --port 8002 --workers 4

Run Phase 2 (with protection):
  RATE_LIMIT_ENABLED=true  uvicorn main:app --host 0.0.0.0 --port 8002 --workers 4
"""
import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Routes protected by the rate limiter — (method, path) tuples.
_PROTECTED: set[tuple[str, str]] = {("POST", "/projects")}


def _client_ip(request: Request) -> str:
    """Return the real client IP, preferring the ALB-injected X-Forwarded-For header."""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window counter stored in Redis.

    Each (client_ip, method, path) combination gets its own counter that
    expires after RATE_LIMIT_WINDOW seconds.  When the counter exceeds
    RATE_LIMIT_REQUESTS the middleware returns HTTP 429 immediately,
    before any router code runs — keeping the DB and app workers free.

    Fails OPEN on Redis errors (logs a warning but lets the request through).
    """

    def __init__(self, app, redis_client):
        super().__init__(app)
        self.redis = redis_client

    async def dispatch(self, request: Request, call_next):
        # Re-read env each request so restarting uvicorn with a different
        # RATE_LIMIT_ENABLED value is the only knob needed between phases.
        enabled = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
        if not enabled:
            return await call_next(request)

        method = request.method
        # Normalise path: strip trailing slash so /projects/ == /projects
        path = request.url.path.rstrip("/") or "/"

        if (method, path) not in _PROTECTED:
            return await call_next(request)

        limit  = int(os.getenv("RATE_LIMIT_REQUESTS", "20"))
        window = int(os.getenv("RATE_LIMIT_WINDOW",   "10"))
        ip     = _client_ip(request)
        key    = f"rl:{ip}:{method}:{path}"

        try:
            count = self.redis.incr(key)
            # Set TTL only on the first request so the window is fixed and
            # expires naturally — calling EXPIRE on every request would reset
            # the timer and keep the key alive indefinitely under load.
            if count == 1:
                self.redis.expire(key, window)
        except Exception as exc:
            logger.warning("RateLimitMiddleware: Redis error — failing open: %s", exc)
            return await call_next(request)

        if count > limit:
            logger.warning(
                "Rate limit exceeded | ip=%s count=%d limit=%d window=%ds",
                ip, count, limit, window,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded. "
                        f"Max {limit} POST /projects requests per {window} s. "
                        f"Retry after {window} s."
                    )
                },
                headers={"Retry-After": str(window)},
            )

        return await call_next(request)
