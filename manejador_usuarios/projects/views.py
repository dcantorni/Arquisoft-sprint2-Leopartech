import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db import connection, OperationalError

from .models import Proyecto
from .serializers import ProyectoCreateSerializer, ProyectoResponseSerializer
from .services import ProyectoService
from .resource_client import CloudServiceUnavailable

logger = logging.getLogger(__name__)


class ProyectoCreateView(APIView):
    """
    GET  /projects — list projects for the authenticated tenant.
    POST /projects — create a new project (requires valid JWT).
    Target latency: ≤ 100 ms (architecture.md §6 Performance SLA).
    """

    def get(self, request):
        empresa_id = getattr(request, 'tenant_id', None)
        if not empresa_id:
            return Response({'error': 'Tenant no identificado.'}, status=status.HTTP_403_FORBIDDEN)

        qs = (
            Proyecto.objects
            .filter(empresa__id=empresa_id)
            .select_related('empresa', 'presupuesto')
            .prefetch_related('cuentas_cloud')
            .order_by('-creado_en')[:50]
        )
        serializer = ProyectoResponseSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = ProyectoCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            proyecto = ProyectoService.crear_proyecto(serializer.validated_data)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except CloudServiceUnavailable as exc:
            logger.error("Resource Service unavailable: %s", exc)
            return Response(
                {'error': 'Servicio de recursos cloud no disponible. Intente más tarde.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            logger.exception("Unexpected error creating proyecto")
            return Response(
                {'error': 'Error interno del servidor.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response_data = ProyectoResponseSerializer(proyecto).data
        return Response(response_data, status=status.HTTP_201_CREATED)


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

        # CHANGE 4: Redis removed from manejador_usuarios — health check DB-only
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
