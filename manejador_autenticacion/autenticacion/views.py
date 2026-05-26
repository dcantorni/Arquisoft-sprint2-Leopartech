import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db import connection, OperationalError
from django.conf import settings

from .backends import do_login, do_refresh, do_validate

logger = logging.getLogger(__name__)


class LoginView(APIView):
    """POST /auth/login — validates credentials and returns JWT tokens."""

    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        password = request.data.get('password', '')

        if not email or not password:
            return Response(
                {'error': 'email y password son requeridos.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = do_login(email, password)
        except Exception:
            logger.exception("Login backend error")
            return Response(
                {'error': 'Error del servicio de autenticación.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if result is None:
            return Response(
                {'error': 'Credenciales inválidas.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return Response(result, status=status.HTTP_200_OK)


class RefreshView(APIView):
    """POST /auth/refresh — refreshes an expired access token."""

    def post(self, request):
        refresh_token = request.data.get('refresh_token', '').strip()
        if not refresh_token:
            return Response(
                {'error': 'refresh_token es requerido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = do_refresh(refresh_token)
        if result is None:
            return Response(
                {'error': 'Token de refresco inválido o expirado.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return Response(result, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """
    POST /auth/logout — invalidates the session.
    For local JWT mode, logout is client-side (delete the token).
    For Cognito, this would call GlobalSignOut.
    """

    def post(self, request):
        return Response(
            {'message': 'Sesión cerrada exitosamente.'},
            status=status.HTTP_200_OK,
        )


class ValidateView(APIView):
    """
    GET /auth/validate — validates the Bearer token and returns tenant info.
    Called by manejador_seguridad and TenantAuthMiddleware on each request.
    """

    def get(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            return Response(
                {'error': 'Token no proporcionado. Use: Authorization: Bearer <token>'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        token = auth_header[7:]
        result = do_validate(token)
        if result is None:
            return Response(
                {'error': 'Token inválido o expirado.', 'valid': False},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return Response(result, status=status.HTTP_200_OK)


class HealthCheckView(APIView):
    """GET /health"""

    def get(self, request):
        checks = {}

        try:
            connection.ensure_connection()
            checks['database'] = 'ok'
        except OperationalError:
            checks['database'] = 'error'

        # Redis token cache health (FIX 1 — added after verification report)
        from .token_cache import ping as redis_ping
        checks['redis'] = 'ok' if redis_ping() else 'error'

        checks['mode'] = 'cognito' if settings.USE_COGNITO else 'local_jwt'

        all_ok = checks.get('database') == 'ok' and checks.get('redis') == 'ok'
        return Response(
            {
                'service': 'manejador_autenticacion',
                'status': 'healthy' if all_ok else 'degraded',
                'checks': checks,
            },
            status=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        )
