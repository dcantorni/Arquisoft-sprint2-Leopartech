"""
GCP Cloud Billing collector — stub.
Returns empty results and logs a warning.
Full GCP BigQuery billing export integration is out of scope for the current sprint.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def collect(cuenta: dict[str, Any], db_conn) -> dict[str, Any]:
    """
    Stub implementation — GCP not yet integrated.

    Args:
        cuenta: dict with keys: id, nombre, account_external_id (GCP project ID)
        db_conn: psycopg2 connection to cloud_db PRIMARY

    Returns:
        dict with cuenta_id and empty metricas list.
    """
    logger.warning(
        "gcp_collector.collect: GCP integration not yet implemented "
        "(cuenta_id=%s project=%s) — skipping.",
        cuenta.get("id"),
        cuenta.get("account_external_id"),
    )
    return {"cuenta_id": cuenta["id"], "metricas": []}
