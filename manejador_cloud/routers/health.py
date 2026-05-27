import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from cache import redis_client
from database import get_read_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])

@router.get("")
def health_check(db: Session = Depends(get_read_db)):
    """Health check endpoint — always returns HTTP 200, never 503.
    ALB health check requires 200 for the instance to stay in service."""
    checks = {}

    try:
        db.execute(text("SELECT 1"))
        checks['database'] = 'ok'
    except Exception:
        checks['database'] = 'error'

    try:
        redis_client.setex('_health', 5, '1')
        val = redis_client.get('_health')
        checks['redis'] = 'ok' if val == '1' else 'error'
    except Exception:
        checks['redis'] = 'error'

    all_ok = all(v == 'ok' for v in checks.values())

    return {
        'service': 'manejador_cloud',
        'status': 'healthy' if all_ok else 'degraded',
        'checks': checks,
    }
