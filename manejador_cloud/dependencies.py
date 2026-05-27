import logging
import requests
import jwt
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config import settings

logger = logging.getLogger(__name__)

security = HTTPBearer()

def get_current_tenant(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """
    FastAPI dependency equivalent to TenantAuthMiddleware.
    Validates token via auth service or locally on fallback.
    Returns the tenant UUID string.
    """
    token = credentials.credentials
    auth_url = settings.AUTH_SERVICE_URL
    if not auth_url:
        logger.warning("AUTH_SERVICE_URL not configured — returning 'unauthenticated'")
        return 'unauthenticated'

    try:
        resp = requests.get(
            f'{auth_url}/auth/validate',
            headers={'Authorization': f'Bearer {token}'},
            timeout=settings.AUTH_SERVICE_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json().get('empresa_id')
    except requests.RequestException as e:
        logger.warning("Auth service unreachable: %s — falling back to local validation", e)
        # fallback locally
        local_secret = settings.LOCAL_JWT_SECRET
        if local_secret:
            try:
                payload = jwt.decode(token, local_secret, algorithms=['HS256'])
                if payload.get('type') == 'access':
                    return payload.get('empresa_id')
            except Exception:
                pass

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Token inválido o expirado."
    )
