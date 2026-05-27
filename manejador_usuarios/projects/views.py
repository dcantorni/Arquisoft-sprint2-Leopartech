import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db import connection, OperationalError

logger = logging.getLogger(__name__)

_MOVED_DETAIL = (
    "El recurso /projects ha sido movido permanentemente a manejador_cloud. "
    "Use el ALB endpoint: POST /projects (puerto 443)."
)


class ProyectoCreateView(APIView):
    """
    /projects has moved to manejador_cloud (FastAPI, port 8002).
    This stub returns HTTP 410 Gone so any misconfigured client or
    direct-to-EC2 call fails loudly rather than silently timing out.
    All ALB traffic for /projects/* now routes to the cloud target group.
    """

    def get(self, request):
        return Response(
            {'error': _MOVED_DETAIL, 'code': 'ENDPOINT_MOVED'},
            status=status.HTTP_410_GONE,
        )

    def post(self, request):
        return Response(
            {'error': _MOVED_DETAIL, 'code': 'ENDPOINT_MOVED'},
            status=status.HTTP_410_GONE,
        )


class HealthCheckView(APIView):
    """
    GET /health
    Returns service health. Used by AWS Load Balancer and Docker health checks.
    """
    throttle_classes = []

    def get(self, request):
        checks = {}

        # Database
        try:
            connection.ensure_connection()
            checks['database'] = 'ok'
        except OperationalError:
            checks['database'] = 'error'

        # Redis
        try:
            from django.core.cache import cache
            cache.set('_health', '1', 5)
            checks['redis'] = 'ok' if cache.get('_health') == '1' else 'error'
        except Exception:
            checks['redis'] = 'error'

        all_ok = all(v == 'ok' for v in checks.values())
        http_status = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(
            {
                'service': 'manejador_usuarios',
                'status': 'healthy' if all_ok else 'degraded',
                'checks': checks,
            },
            status=http_status,
        )
