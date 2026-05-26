import json
import logging
import threading

import pika
from django.conf import settings

logger = logging.getLogger(__name__)


def _publish_to_rabbitmq(
    exchange: str,
    routing_key: str,
    body: dict,
    message_id: str | None = None,
) -> None:
    """
    Internal: publishes a single event to RabbitMQ.
    Runs in a background daemon thread (non-blocking for the HTTP request).
    """
    try:
        params = pika.URLParameters(settings.RABBITMQ_URL)
        params.connection_attempts = 3
        params.retry_delay = 1
        connection = pika.BlockingConnection(params)
        channel = connection.channel()

        channel.exchange_declare(
            exchange=exchange,
            exchange_type='topic',
            durable=True,
        )

        props = pika.BasicProperties(
            delivery_mode=2,
            content_type='application/json',
            app_id='manejador_reportes',
        )
        if message_id:
            props.message_id = message_id

        channel.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=json.dumps(body, default=str).encode(),
            properties=props,
        )
        connection.close()
        logger.info(
            "[ASR15] RabbitMQ enqueue OK: routing_key=%s message_id=%s tipo=%s",
            routing_key,
            message_id,
            body.get('tipo', ''),
        )
    except Exception:
        logger.exception(
            "[ASR15] RabbitMQ enqueue FAILED: routing_key=%s message_id=%s",
            routing_key,
            message_id,
        )


def publish_event_async(
    routing_key: str,
    body: dict,
    message_id: str | None = None,
) -> None:
    """
    Fire-and-forget publish — returns immediately without blocking the HTTP thread.
    Same pattern as manejador_usuarios/projects/publisher.py.
    """
    thread = threading.Thread(
        target=_publish_to_rabbitmq,
        args=(settings.RABBITMQ_EXCHANGE, routing_key, body, message_id),
        daemon=True,
        name=f"publisher-{message_id or 'batch'}",
    )
    thread.start()
    logger.info(
        "[ASR15] Background RabbitMQ publish started: routing_key=%s message_id=%s",
        routing_key,
        message_id,
    )


def publish_event(routing_key: str, body: dict, message_id: str | None = None):
    """
    Synchronous RabbitMQ publish for management commands that need blocking I/O.
    Raises on failure.
    """
    try:
        _publish_to_rabbitmq(settings.RABBITMQ_EXCHANGE, routing_key, body, message_id)
    except Exception:
        logger.exception("Failed to publish event: routing_key=%s", routing_key)
        raise


def routing_key_for_event(tipo: str) -> str:
    """Maps event type to RabbitMQ routing key."""
    mapping = {
        'proyecto_creado': 'proyecto.creado',
        'proyecto_actualizado': 'proyecto.actualizado',
        'analisis_completado': 'analisis.completado',
        'reporte_generado': 'reporte.generado',
        'reporte_solicitado': 'reporte.solicitado',
        'recurso_infrautilizado': 'recurso.infrautilizado',
        'alerta_presupuesto': 'alerta.presupuesto',
    }
    return mapping.get(tipo, f"evento.{tipo}")
