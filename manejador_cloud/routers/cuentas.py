"""
/cloud/cloud-accounts endpoints.

GET  /cloud/cloud-accounts              → list active cuentas (optional ?proyecto_id=)
POST /cloud/cloud-accounts              → create cuenta, warm cache
GET  /cloud/cloud-accounts/{cuenta_id}  → get detail (cache-aside, 30 s TTL)
GET  /cloud/cloud-accounts/{cuenta_id}/validate → validate for Project Service

All reads go to the read replica via get_read_db().
Writes (POST) go to the primary via get_write_db().
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth import require_tenant
from cache import get as cache_get, set as cache_set
from database import get_read_db, get_write_db
from models.cloud_models import CuentaCloud, ProveedorCloud
from schemas.cloud_schemas import CuentaCloudCreate, CuentaCloudOut, CuentaCloudValidation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cloud/cloud-accounts", tags=["cloud-accounts"])

_DETAIL_CACHE_TYPE = "cuenta_detail"
_VALIDATION_CACHE_TYPE = "cuenta_validation"


@router.get("", response_model=list[CuentaCloudOut])
async def list_cuentas(
    proyecto_id: Optional[uuid.UUID] = Query(None),
    empresa_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_read_db),
):
    stmt = (
        select(CuentaCloud)
        .options(selectinload(CuentaCloud.proveedor))
        .where(CuentaCloud.activa == True)  # noqa: E712
        .order_by(CuentaCloud.creada_en.desc())
    )
    if proyecto_id:
        stmt = stmt.where(CuentaCloud.proyecto_id == proyecto_id)

    result = await db.execute(stmt)
    cuentas = result.scalars().all()
    return [CuentaCloudOut.from_orm_with_proveedor(c) for c in cuentas]


@router.post("", response_model=CuentaCloudOut, status_code=201)
async def create_cuenta(
    body: CuentaCloudCreate,
    empresa_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_write_db),
):
    # Verify proveedor exists
    proveedor = await db.get(ProveedorCloud, body.proveedor)
    if proveedor is None:
        raise HTTPException(status_code=400, detail=f"ProveedorCloud {body.proveedor} no existe.")

    cuenta = CuentaCloud(
        nombre=body.nombre,
        proveedor_id=body.proveedor,
        proyecto_id=body.proyecto_id,
        account_external_id=body.account_external_id,
        region=body.region,
        activa=True,
    )
    db.add(cuenta)
    await db.commit()
    await db.refresh(cuenta)

    # Eagerly load proveedor for the response
    await db.refresh(cuenta, ["proveedor"])

    # Warm up cache (validation used by manejador_usuarios)
    await cache_set(str(cuenta.id), _VALIDATION_CACHE_TYPE, True)
    logger.info("CuentaCloud created: %s", cuenta.id)

    return CuentaCloudOut.from_orm_with_proveedor(cuenta)


@router.get("/{cuenta_id}", response_model=CuentaCloudOut)
async def get_cuenta(
    cuenta_id: uuid.UUID,
    empresa_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_read_db),
):
    # Cache-aside
    cached = await cache_get(str(cuenta_id), _DETAIL_CACHE_TYPE)
    if cached is not None:
        return CuentaCloudOut.model_validate(cached)

    stmt = (
        select(CuentaCloud)
        .options(selectinload(CuentaCloud.proveedor))
        .where(CuentaCloud.id == cuenta_id)
    )
    result = await db.execute(stmt)
    cuenta = result.scalar_one_or_none()
    if cuenta is None:
        raise HTTPException(status_code=404, detail="CuentaCloud no encontrada.")

    out = CuentaCloudOut.from_orm_with_proveedor(cuenta)
    await cache_set(str(cuenta_id), _DETAIL_CACHE_TYPE, out.model_dump(mode="json"))
    return out


@router.get("/{cuenta_id}/validate", response_model=CuentaCloudValidation)
async def validate_cuenta(
    cuenta_id: uuid.UUID,
    db: AsyncSession = Depends(get_read_db),
):
    """
    Called by manejador_usuarios to validate a CuentaCloud before project creation.
    No auth required (inter-service call uses internal VPC routing).
    """
    # Cache-aside
    cached = await cache_get(str(cuenta_id), _VALIDATION_CACHE_TYPE)
    if cached is not None:
        return CuentaCloudValidation(
            cuenta_cloud_id=cuenta_id,
            activa=cached if isinstance(cached, bool) else cached.get("activa", False),
        )

    stmt = (
        select(CuentaCloud)
        .options(selectinload(CuentaCloud.proveedor))
        .where(CuentaCloud.id == cuenta_id)
    )
    result = await db.execute(stmt)
    cuenta = result.scalar_one_or_none()

    if cuenta is None:
        await cache_set(str(cuenta_id), _VALIDATION_CACHE_TYPE, False)
        raise HTTPException(status_code=404, detail="CuentaCloud no encontrada.")

    await cache_set(str(cuenta_id), _VALIDATION_CACHE_TYPE, cuenta.activa)
    return CuentaCloudValidation(
        cuenta_cloud_id=cuenta.id,
        activa=cuenta.activa,
        proveedor_tipo=cuenta.proveedor.tipo if cuenta.proveedor else None,
    )
