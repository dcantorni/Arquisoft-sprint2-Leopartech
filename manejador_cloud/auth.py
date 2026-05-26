"""
JWT validation middleware / FastAPI dependency.

Flow (same as existing Django TenantAuthMiddleware):
  1. Extract Bearer token from Authorization header.
  2. Call GET AUTH_SERVICE_URL/auth/validate — if 200, extract empresa_id.
  3. If auth service unreachable → fallback to local HS256 validation.
  4. If invalid / missing → raise HTTP 403.
  5. Attach empresa_id to request.state.empresa_id.

/health is exempt from auth (checked in each router directly).
"""
import logging
import os
from typing import Optional

import httpx
import jwt
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

_AUTH_URL = os.environ.get("AUTH_SERVICE_URL", "http://manejador-autenticacion:8004")
_AUTH_TIMEOUT = float(os.environ.get("AUTH_SERVICE_TIMEOUT", "2"))
_LOCAL_JWT_SECRET = os.environ.get("LOCAL_JWT_SECRET", "local-dev-jwt-secret-change-in-production")

_bearer_scheme = HTTPBearer(auto_error=False)


async def require_tenant(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = None,
) -> str:
    """
    FastAPI dependency that validates the Bearer token and returns empresa_id.
    Raises HTTP 403 on missing or invalid tokens.
    """
    # Extract token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=403,
            detail="Token de autenticación requerido. Use: Authorization: Bearer <token>",
        )
    token = auth_header[7:]

    empresa_id = await _resolve_tenant(token)
    if empresa_id is None:
        raise HTTPException(status_code=403, detail="Token inválido o expirado.")

    # Attach to request state so routers can read it without re-validating
    request.state.empresa_id = empresa_id
    return empresa_id


async def _resolve_tenant(token: str) -> Optional[str]:
    """Try auth service first; fall back to local JWT on connection error."""
    if not _AUTH_URL:
        logger.warning("AUTH_SERVICE_URL not configured — falling back to local JWT")
        return _validate_locally(token)

    try:
        async with httpx.AsyncClient(timeout=_AUTH_TIMEOUT) as client:
            resp = await client.get(
                f"{_AUTH_URL}/auth/validate",
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code == 200:
            return resp.json().get("empresa_id")
        return None
    except httpx.RequestError as exc:
        logger.warning("Auth service unreachable: %s — falling back to local JWT", exc)
        return _validate_locally(token)


def _validate_locally(token: str) -> Optional[str]:
    """HS256 fallback when auth service is temporarily unreachable."""
    if not _LOCAL_JWT_SECRET:
        return None
    try:
        payload = jwt.decode(token, _LOCAL_JWT_SECRET, algorithms=["HS256"])
        if payload.get("type") == "access":
            return payload.get("empresa_id")
        return None
    except Exception:
        return None
