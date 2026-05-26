"""
cloud_collector Lambda handler.
Triggered by EventBridge every 6 hours (main.tf CHANGE 5).

Flow:
  1. Connect to cloud_db PRIMARY via DATABASE_HOST env var.
  2. Fetch all active CuentaCloud records.
  3. For each cuenta → call aws_collector or gcp_collector based on proveedor tipo.
  4. Upsert MetricaConsumo records (INSERT ... ON CONFLICT DO UPDATE).
  5. Invalidate Redis cache keys matching cloud:dashboard:* (DB 1).
  6. Log execution summary → CloudWatch.
  7. Return {statusCode: 200, processed: N, errors: M}.
"""
import json
import logging
import os
import uuid
from datetime import date, datetime
from typing import Any

import psycopg2
import psycopg2.extras
import redis

from collectors.aws_collector import collect as aws_collect
from collectors.gcp_collector import collect as gcp_collect

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Environment ────────────────────────────────────────────────────────────
_DB_HOST = os.environ.get("DATABASE_HOST", "localhost")
_DB_PORT = int(os.environ.get("DATABASE_PORT", "5432"))
_DB_NAME = os.environ.get("DATABASE_NAME", "cloud_db")
_DB_USER = os.environ.get("DATABASE_USER", "admin")
_DB_PASS = os.environ.get("DATABASE_PASSWORD", "admin123")
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/1")

_COLLECTOR_DISPATCH = {
    "AWS": aws_collect,
    "GCP": gcp_collect,
    "AZURE": gcp_collect,  # Stub until Azure integration is added
}


def _db_connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=_DB_HOST,
        port=_DB_PORT,
        dbname=_DB_NAME,
        user=_DB_USER,
        password=_DB_PASS,
        connect_timeout=10,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def _fetch_active_cuentas(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cc.id, cc.nombre, cc.account_external_id, cc.region,
                   pc.tipo AS proveedor_tipo
            FROM   cuentas_cloud cc
            JOIN   proveedores_cloud pc ON pc.id = cc.proveedor_id
            WHERE  cc.activa = TRUE AND pc.activo = TRUE
            ORDER  BY cc.id
            """
        )
        return [dict(row) for row in cur.fetchall()]


def _upsert_metricas(conn, recurso_id: str, metricas: list[dict]) -> int:
    """
    For each metrica, find the first active RecursoCloud linked to cuenta_id
    and upsert a MetricaConsumo record.
    Uses ON CONFLICT on (recurso_id, tipo_metrica, periodo_inicio) to be idempotent.
    """
    if not metricas:
        return 0

    inserted = 0
    with conn.cursor() as cur:
        # Find any active recurso for this recurso_id (already resolved by caller)
        for m in metricas:
            cur.execute(
                """
                INSERT INTO metricas_consumo
                    (id, recurso_id, tipo_metrica, periodo_inicio, periodo_fin,
                     valor, costo, moneda)
                VALUES
                    (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (recurso_id, tipo_metrica, periodo_inicio)
                DO UPDATE SET
                    valor       = EXCLUDED.valor,
                    periodo_fin = EXCLUDED.periodo_fin,
                    costo       = EXCLUDED.costo
                """,
                (
                    recurso_id,
                    m["tipo_metrica"],
                    m["periodo_inicio"],
                    m["periodo_fin"],
                    m["valor"],
                    m.get("costo", 0.0),
                    m.get("moneda", "USD"),
                ),
            )
            inserted += 1
    conn.commit()
    return inserted


def _get_recurso_for_cuenta(conn, cuenta_id: str) -> str | None:
    """Return the first active RecursoCloud id for the given cuenta."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM recursos_cloud WHERE cuenta_id = %s AND activo = TRUE LIMIT 1",
            (cuenta_id,),
        )
        row = cur.fetchone()
        return str(row["id"]) if row else None


def _invalidate_redis_cache() -> int:
    """Delete all cloud:dashboard:* keys from Redis DB 1."""
    try:
        r = redis.from_url(_REDIS_URL, decode_responses=True, socket_timeout=3)
        keys = r.keys("cloud:dashboard:*")
        if keys:
            deleted = r.delete(*keys)
            logger.info("Redis: invalidated %d cloud:dashboard:* keys", deleted)
            return deleted
        return 0
    except Exception as exc:
        logger.warning("Redis invalidation failed: %s", exc)
        return 0


def lambda_handler(event: dict, context: Any) -> dict:
    """
    EventBridge entry point.
    Returns: {statusCode, processed, errors, cached_keys_invalidated}
    """
    logger.info("cloud_collector started — event: %s", json.dumps(event))
    start = datetime.utcnow()

    processed = 0
    errors = 0

    try:
        conn = _db_connect()
    except Exception as exc:
        logger.error("DB connection failed: %s", exc)
        return {"statusCode": 500, "error": str(exc)}

    try:
        cuentas = _fetch_active_cuentas(conn)
        logger.info("Found %d active CuentaCloud records", len(cuentas))

        for cuenta in cuentas:
            tipo = cuenta.get("proveedor_tipo", "AWS")
            collector_fn = _COLLECTOR_DISPATCH.get(tipo, aws_collect)

            try:
                result = collector_fn(cuenta, conn)
                metricas = result.get("metricas", [])

                # Find a recurso to attach metrics to
                recurso_id = _get_recurso_for_cuenta(conn, str(cuenta["id"]))
                if recurso_id and metricas:
                    inserted = _upsert_metricas(conn, recurso_id, metricas)
                    processed += inserted
                    logger.info(
                        "cuenta=%s tipo=%s metricas_inserted=%d",
                        cuenta["id"], tipo, inserted,
                    )
                else:
                    logger.info(
                        "cuenta=%s tipo=%s — no recurso or no metricas, skipping",
                        cuenta["id"], tipo,
                    )
            except Exception as exc:
                errors += 1
                logger.error("Error processing cuenta %s: %s", cuenta["id"], exc)
    finally:
        conn.close()

    # Invalidate Redis dashboard cache so manejador_cloud serves fresh data
    invalidated = _invalidate_redis_cache()

    elapsed = (datetime.utcnow() - start).total_seconds()
    summary = {
        "statusCode": 200,
        "processed": processed,
        "errors": errors,
        "cached_keys_invalidated": invalidated,
        "elapsed_seconds": round(elapsed, 2),
    }
    logger.info("cloud_collector finished: %s", json.dumps(summary))
    return summary
