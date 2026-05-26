"""
CHANGE 4 — Redis removed from manejador_usuarios.

CuentaCloudCache and EmpresaCache previously stored data in Redis DB 0.
They now pass through directly to the Resource Service or local DB.
The class interface is preserved so services.py calls require no structural change.

CuentaCloud validation: always calls Resource Service HTTP (ResourceServiceClient).
Empresa lookup: always queries local usuarios_db directly (indexed by id).
"""
import logging

logger = logging.getLogger(__name__)


class CuentaCloudCache:
    """
    Thin pass-through — no in-process cache.
    services.py calls get_validation() → always returns None (cache miss),
    which triggers a ResourceServiceClient.validate_cuenta_cloud() call.
    """

    @classmethod
    def get_validation(cls, cuenta_id) -> bool | None:
        return None  # Always miss — callers fall through to Resource Service HTTP

    @classmethod
    def set_validation(cls, cuenta_id, is_active: bool) -> None:
        pass  # No-op

    @classmethod
    def invalidate(cls, cuenta_id) -> None:
        pass  # No-op


class EmpresaCache:
    """
    Thin pass-through — no in-process cache.
    services.py calls get() → always returns None (cache miss),
    which triggers a direct Empresa.objects.get() DB query.
    The Empresa.id column is indexed (PK UUID) so the fallback is fast.
    """

    @classmethod
    def get(cls, empresa_id) -> dict | None:
        return None  # Always miss — callers fall through to DB

    @classmethod
    def set(cls, empresa_id, data: dict) -> None:
        pass  # No-op

    @classmethod
    def invalidate(cls, empresa_id) -> None:
        pass  # No-op
