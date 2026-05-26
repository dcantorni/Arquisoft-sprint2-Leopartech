"""
AWS Cost Explorer collector — stub implementation.
Real AWS CE integration will be added in the next prompt when AWS credentials
and API access are confirmed.

Called by handler.lambda_handler for every active CuentaCloud with tipo='AWS'.
"""
import logging
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def collect(cuenta: dict[str, Any], db_conn) -> dict[str, Any]:
    """
    Stub implementation — returns synthetic cost data.

    Args:
        cuenta: dict with keys: id, nombre, account_external_id, region
        db_conn: psycopg2 connection to cloud_db PRIMARY (for upsert)

    Returns:
        dict with:
            cuenta_id: str
            metricas: list[dict] — each has tipo_metrica, valor, unidad, periodo_inicio, periodo_fin

    Real implementation will call:
        boto3.client('ce').get_cost_and_usage(
            TimePeriod={'Start': ..., 'End': ...},
            Granularity='MONTHLY',
            Metrics=['BlendedCost'],
        )
    """
    today = date.today()
    period_start = date(today.year, today.month, 1)
    period_end = today

    logger.info(
        "aws_collector.collect (stub): cuenta_id=%s account=%s",
        cuenta.get("id"),
        cuenta.get("account_external_id"),
    )

    return {
        "cuenta_id": cuenta["id"],
        "metricas": [
            {
                "tipo_metrica": "COSTO",
                "valor": 0.0,
                "unidad": "USD",
                "periodo_inicio": period_start.isoformat(),
                "periodo_fin": period_end.isoformat(),
            },
            {
                "tipo_metrica": "CPU",
                "valor": 0.0,
                "unidad": "CPU_HOURS",
                "periodo_inicio": period_start.isoformat(),
                "periodo_fin": period_end.isoformat(),
            },
        ],
    }
