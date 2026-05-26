"""
TenantAuthMiddleware — validates Bearer JWT on every non-health request.

Calls manejador_autenticacion GET /auth/validate to verify the token.
Attaches request.tenant_id (empresa_id UUID string) on success.
Returns HTTP 403 JSON on missing/invalid tokens.

Health check endpoints (/health) are exempt from authentication.
"""
import json
import logging
import os
import time

import requests
from django.conf import settings
from django.http import JsonResponse

from .cognito_auth import validate_cognito_id_token

logger = logging.getLogger(__name__)

EXEMPT_PREFIXES = ('/health',)


# #region agent log
def _debug_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    entry = {
        'sessionId': 'c85b96',
        'hypothesisId': hypothesis_id,
        'location': location,
        'message': message,
        'data': data,
        'timestamp': int(time.time() * 1000),
    }
    line = json.dumps(entry, default=str) + '\n'
    for path in (
        os.environ.get('DEBUG_LOG_PATH', ''),
        '/tmp/debug-c85b96.log',
        os.path.join(os.path.dirname(__file__), '..', '..', 'debug-c85b96.log'),
    ):
        if not path:
            continue
        try:
            with open(path, 'a', encoding='utf-8') as fh:
                fh.write(line)
            break
        except OSError:
            continue
# #endregion


class TenantAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if any(request.path.startswith(p) for p in EXEMPT_PREFIXES):
            return self.get_response(request)

        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            return JsonResponse(
                {'error': 'Token de autenticación requerido. Use: Authorization: Bearer <token>'},
                status=403,
            )

        token = auth_header[7:]
        tenant_id = self._resolve_tenant(token)

        # #region agent log
        _debug_log('H3', 'middleware.py:__call__', 'auth resolution result', {
            'path': request.path,
            'tenant_resolved': tenant_id is not None,
            'auth_service_url': getattr(settings, 'AUTH_SERVICE_URL', ''),
        })
        # #endregion

        if tenant_id is None:
            return JsonResponse(
                {'error': 'Token inválido o expirado.'},
                status=403,
            )

        request.tenant_id = tenant_id
        return self.get_response(request)

    def _resolve_tenant(self, token):
        resolve_start = time.perf_counter()

        # Fast path: local Cognito JWT (no HTTP round-trip via ALB → auth service).
        # Under JMeter load, calling auth/validate per request caused ~10s queue → ALB 504.
        if os.environ.get('COGNITO_USER_POOL_ID'):
            empresa_id = validate_cognito_id_token(token)
            elapsed_ms = (time.perf_counter() - resolve_start) * 1000
            # #region agent log
            _debug_log('H1', 'middleware.py:_resolve_tenant', 'cognito fast path', {
                'resolved': empresa_id is not None,
                'elapsed_ms': round(elapsed_ms, 1),
            })
            # #endregion
            if empresa_id:
                return empresa_id

        auth_url = getattr(settings, 'AUTH_SERVICE_URL', '')
        if not auth_url:
            logger.warning("AUTH_SERVICE_URL not configured — auth middleware is disabled")
            return 'unauthenticated'

        validate_url = f'{auth_url}/auth/validate'
        try:
            auth_start = time.perf_counter()
            resp = requests.get(
                validate_url,
                headers={'Authorization': f'Bearer {token}'},
                timeout=getattr(settings, 'AUTH_SERVICE_TIMEOUT', 2),
            )
            auth_ms = (time.perf_counter() - auth_start) * 1000
            # #region agent log
            _debug_log('H2', 'middleware.py:_resolve_tenant', 'auth service response', {
                'validate_url': validate_url,
                'status_code': resp.status_code,
                'has_empresa_id': bool(resp.status_code == 200 and resp.json().get('empresa_id')),
                'auth_elapsed_ms': round(auth_ms, 1),
            })
            # #endregion
            if resp.status_code == 200:
                empresa_id = resp.json().get('empresa_id')
                if empresa_id:
                    return empresa_id
            logger.warning(
                "Auth service returned %s — trying local JWT fallback",
                resp.status_code,
            )
        except requests.RequestException as e:
            # #region agent log
            _debug_log('H2', 'middleware.py:_resolve_tenant', 'auth service unreachable', {
                'validate_url': validate_url,
                'error': str(e),
            })
            # #endregion
            logger.warning("Auth service unreachable: %s — trying local JWT fallback", e)

        return self._validate_locally(token)

    def _validate_locally(self, token):
        """HS256 fallback when auth service is temporarily unreachable."""
        local_secret = getattr(settings, 'LOCAL_JWT_SECRET', '')
        if not local_secret:
            return None
        try:
            import jwt
            payload = jwt.decode(token, local_secret, algorithms=['HS256'])
            if payload.get('type') == 'access':
                return payload.get('empresa_id')
            return None
        except Exception:
            return None
