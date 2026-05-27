"""
TenantAuthMiddleware — validates Bearer JWT on every non-health request.

Calls manejador_autenticacion GET /auth/validate to verify the token.
Attaches request.tenant_id (empresa_id UUID string) on success.
Returns HTTP 403 JSON on missing/invalid tokens.

Health check endpoints (/health) are exempt from authentication.
"""
import logging
import requests
from django.http import JsonResponse
from django.conf import settings

logger = logging.getLogger(__name__)

EXEMPT_PREFIXES = ('/health',)


class TenantAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if any(request.path.startswith(p) for p in EXEMPT_PREFIXES):
            return self.get_response(request)

        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            self._report_unauthorized(request, 'missing_bearer_token')
            return JsonResponse(
                {'error': 'Token de autenticación requerido. Use: Authorization: Bearer <token>'},
                status=403,
            )

        token = auth_header[7:]
        tenant_id = self._resolve_tenant(token)

        if tenant_id is None:
            self._report_unauthorized(request, 'invalid_or_expired_token')
            return JsonResponse(
                {'error': 'Token inválido o expirado.'},
                status=403,
            )

        request.tenant_id = tenant_id
        return self.get_response(request)

    def _report_unauthorized(self, request, tipo, empresa_id_token=None):
        def _send():
            try:
                import requests as req
                seguridad_url = os.environ.get('SEGURIDAD_URL', '')
                if seguridad_url:
                    req.post(
                        f'{seguridad_url}/security/events',
                        json={
                            'tipo': tipo,
                            'endpoint': request.path,
                            'metodo': request.method,
                            'ip_origen': request.META.get('REMOTE_ADDR', ''),
                            'empresa_id_token': str(empresa_id_token) if empresa_id_token else None,
                            'evidencia': {'path': request.path},
                        },
                        timeout=0.5
                    )
            except Exception:
                pass
        import threading
        threading.Thread(target=_send, daemon=True).start()


    def _resolve_tenant(self, token):
        auth_url = getattr(settings, 'AUTH_SERVICE_URL', '')
        if not auth_url:
            logger.warning("AUTH_SERVICE_URL not configured — auth middleware is disabled")
            return 'unauthenticated'

        try:
            resp = requests.get(
                f'{auth_url}/auth/validate',
                headers={'Authorization': f'Bearer {token}'},
                timeout=getattr(settings, 'AUTH_SERVICE_TIMEOUT', 2),
            )
            if resp.status_code == 200:
                return resp.json().get('empresa_id')
            return None
        except requests.RequestException as e:
            logger.warning("Auth service unreachable: %s — falling back to local validation", e)
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
