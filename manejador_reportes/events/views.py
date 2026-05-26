import uuid
import logging
import time
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db import connection, OperationalError

from .serializers import BatchEventSerializer, AnalisisSerializer, ReporteSerializer
from .tasks import procesar_evento_batch
from .models import Analisis, Reporte

logger = logging.getLogger(__name__)


class EventoBatchView(APIView):
    """
    POST /events/batch

    Receives multiple events, publishes them to RabbitMQ in background threads,
    and returns HTTP 202 Accepted immediately (ASR15 async processing).

    Used for Experiment A – Scalability (architecture.md §4.1):
    - Target throughput: ≥ 500 events/min
    - Target latency per batch: ≤ 1.5 s
    - Zero failures
    """

    def post(self, request):
        request_start = time.perf_counter()
        logger.info(
            "[ASR15] POST /events/batch recibido: events_count=%s",
            len(request.data.get('events', [])) if isinstance(request.data, dict) else '?',
        )

        serializer = BatchEventSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        events = serializer.validated_data['events']
        accepted = []
        failed = []

        for event in events:
            evento_id = event.get('evento_id') or str(uuid.uuid4())
            event['evento_id'] = evento_id

            try:
                task = procesar_evento_batch.apply_async(
                    args=[event],
                    task_id=evento_id,
                )
                accepted.append({'evento_id': evento_id, 'task_id': task.id})
                logger.info(
                    "[ASR15] evento encolado (async): evento_id=%s tipo=%s",
                    evento_id,
                    event.get('tipo', ''),
                )
            except Exception:
                logger.exception("[ASR15] Failed to enqueue event: %s", evento_id)
                failed.append({'evento_id': evento_id, 'error': 'enqueue_failed'})

        elapsed_ms = (time.perf_counter() - request_start) * 1000
        response_status = (
            status.HTTP_202_ACCEPTED if not failed else status.HTTP_207_MULTI_STATUS
        )
        logger.info(
            "[ASR15] respuesta HTTP enviada: status=%s accepted=%d failed=%d elapsed_ms=%.1f",
            response_status,
            len(accepted),
            len(failed),
            elapsed_ms,
        )

        return Response(
            {
                'accepted': len(accepted),
                'failed': len(failed),
                'events': accepted,
                'errors': failed,
            },
            status=response_status,
        )


class AnalisisListView(APIView):
    """GET /analytics?proyecto_id=<uuid>"""

    def get(self, request):
        proyecto_id = request.query_params.get('proyecto_id')
        qs = Analisis.objects.all().order_by('-creado_en')
        if proyecto_id:
            qs = qs.filter(proyecto_id=proyecto_id)
        serializer = AnalisisSerializer(qs[:50], many=True)
        return Response(serializer.data)


class ReporteListView(APIView):
    """
    GET /reports?proyecto_id=<uuid>
    CHANGE 4: Redis removed — direct indexed DB query (periodo_inicio index).
    """
    def get(self, request):
        proyecto_id = request.query_params.get('proyecto_id')
        qs = Reporte.objects.all().order_by('-periodo_inicio')
        if proyecto_id:
            qs = qs.filter(proyecto_id=proyecto_id)
        serializer = ReporteSerializer(qs[:50], many=True)
        return Response(serializer.data)


class HealthCheckView(APIView):
    """GET /health"""
    throttle_classes = []

    def get(self, request):
        checks = {}

        try:
            connection.ensure_connection()
            checks['database'] = 'ok'
        except OperationalError:
            checks['database'] = 'error'

        try:
            import pika
            from django.conf import settings
            params = pika.URLParameters(settings.RABBITMQ_URL)
            params.connection_attempts = 1
            conn = pika.BlockingConnection(params)
            conn.close()
            checks['rabbitmq'] = 'ok'
        except Exception:
            checks['rabbitmq'] = 'error'

        all_ok = all(v == 'ok' for v in checks.values())
        return Response(
            {
                'service': 'manejador_reportes',
                'status': 'healthy' if all_ok else 'degraded',
                'checks': checks,
            },
            status=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        )
