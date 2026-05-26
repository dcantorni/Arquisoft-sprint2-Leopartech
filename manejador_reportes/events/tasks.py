"""
ASR15 — Non-blocking RabbitMQ publisher for POST /events/batch.

Preserves the `.apply_async(args, task_id, **kwargs)` interface used by views.py.
Publishing runs in a background daemon thread (fire-and-forget) so HTTP 202
returns immediately without waiting for RabbitMQ I/O.
"""

import uuid
import logging

from .publisher import publish_event_async

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

    def apply_async(self, args=None, task_id=None, **kwargs):
        """
        Enqueue event_data to RabbitMQ in a background thread and return immediately.

        args[0] is the event_data dict (matches former Celery task signature).
        task_id becomes the AMQP message_id for idempotency.
        """
        event_data = (args or [{}])[0]
        message_id = task_id or str(uuid.uuid4())

        publish_event_async(self._routing_key, event_data, message_id=message_id)
        logger.info(
            "[ASR15] apply_async dispatched (non-blocking): message_id=%s tipo=%s",
            message_id,
            event_data.get('tipo', ''),
        )

        return _AsyncResult(message_id)


# Views.py calls: procesar_evento_batch.apply_async(args=[event], task_id=evento_id)
# The Golang worker consumes bite.eventos queue (binding evento.#).
procesar_evento_batch = _PikaPublisher(routing_key='evento.batch')
