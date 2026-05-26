"""
PROMPT 3 — Celery removed. procesar_evento_batch is now a pika-based publisher
that preserves the `.apply_async(args, task_id, **kwargs)` interface used by
views.py (views.py is not modified).

The Golang worker (worker_golang/) consumes from the bite_events topic exchange
via queue bite.eventos (binding key evento.#).
"""

import json
import uuid
import logging

import pika
from django.conf import settings

logger = logging.getLogger(__name__)


class _AsyncResult:
    """Minimal duck-type of Celery AsyncResult — only .id is needed by views.py."""

    def __init__(self, task_id: str):
        self.id = task_id


class _PikaPublisher:
    """
    Drop-in replacement for a Celery shared_task.

    Usage (unchanged from Celery era):
        task = procesar_evento_batch.apply_async(args=[event], task_id=evento_id)
        task.id  # returns evento_id
    """

    def __init__(self, routing_key: str):
        self._routing_key = routing_key

    # ── public interface ────────────────────────────────────────────────────

    def apply_async(self, args=None, task_id=None, **kwargs):
        """
        Publish event_data to RabbitMQ and return a mock AsyncResult.

        args[0] is the event_data dict (matches former Celery task signature).
        task_id becomes the AMQP message_id for idempotency.
        """
        event_data = (args or [{}])[0]
        message_id = task_id or str(uuid.uuid4())

        try:
            self._publish(event_data, message_id)
        except Exception:
            logger.exception(
                "Failed to publish event to RabbitMQ: routing_key=%s message_id=%s",
                self._routing_key,
                message_id,
            )
            raise  # let views.py catch and add to failed[]

        return _AsyncResult(message_id)

    # ── internal ─────────────────────────────────────────────────────────

    def _publish(self, event_data: dict, message_id: str) -> None:
        exchange = getattr(settings, 'RABBITMQ_EXCHANGE', 'bite_events')
        params = pika.URLParameters(settings.RABBITMQ_URL)
        params.connection_attempts = 2
        params.retry_delay = 1

        connection = pika.BlockingConnection(params)
        try:
            channel = connection.channel()
            channel.exchange_declare(
                exchange=exchange,
                exchange_type='topic',
                durable=True,
                passive=False,
            )
            channel.basic_publish(
                exchange=exchange,
                routing_key=self._routing_key,
                body=json.dumps(event_data, default=str).encode(),
                properties=pika.BasicProperties(
                    content_type='application/json',
                    delivery_mode=2,        # persistent
                    message_id=message_id,
                ),
            )
            logger.debug(
                "Published event: routing_key=%s message_id=%s",
                self._routing_key,
                message_id,
            )
        finally:
            connection.close()


# ── Public symbols (unchanged names, new implementation) ─────────────────────

# Views.py calls: procesar_evento_batch.apply_async(args=[event], task_id=evento_id)
# The Golang worker consumes bite.eventos queue (binding evento.#).
procesar_evento_batch = _PikaPublisher(routing_key='evento.batch')
