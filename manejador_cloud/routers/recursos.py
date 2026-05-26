"""
/cloud/resources endpoints.

GET  /cloud/resources             → list active recursos (optional ?cuenta_id=)
POST /cloud/resources             → create recurso
GET  /cloud/resources/{recurso_id} → get detail (cache-aside)
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth import require_tenant
from cache import get as cache_get, set as cache_set, invalidate as cache_invalidate
from database import get_read_db, get_write_db
from models.cloud_models import CuentaCloud, RecursoCloud
from schemas.cloud_schemas import RecursoCloudCreate, RecursoCloudOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cloud/resources", tags=["resources"])

_DETAIL_CACHE_TYPE = "recurso_detail"
_LIST_CACHE_TYPE = "recursos_list"


@router.get("", response_model=list[RecursoCloudOut])
async def list_recursos(
    cuenta_id: Optional[uuid.UUID] = Query(None),
    empresa_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_read_db),
):
    if cuenta_id:
        # Cache-aside per account
        cached = await cache_get(str(cuenta_id), _LIST_CACHE_TYPE)
        if cached is not None:
            return [RecursoCloudOut.model_validate(r) for r in cached]

    stmt = (
        select(RecursoCloud)
        .options(
            selectinload(RecursoCloud.cuenta).selectinload(CuentaCloud.proveedor)
        )
        .where(RecursoCloud.activo == True)  # noqa: E712
        .order_by(RecursoCloud.tipo, RecursoCloud.nombre)
        .limit(100)
    )
    if cuenta_id:
        stmt = stmt.where(RecursoCloud.cuenta_id == cuenta_id)

    result = await db.execute(stmt)
    recursos = result.scalars().all()
    out = [RecursoCloudOut.from_orm_with_cuenta(r) for r in recursos]

    if cuenta_id:
        await cache_set(str(cuenta_id), _LIST_CACHE_TYPE, [o.model_dump(mode="json") for o in out])

    return out


@router.post("", response_model=RecursoCloudOut, status_code=201)
async def create_recurso(
    body: RecursoCloudCreate,
    empresa_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_write_db),
):
    # Verify cuenta exists
    cuenta = await db.get(CuentaCloud, body.cuenta)
    if cuenta is None:
        raise HTTPException(status_code=400, detail=f"CuentaCloud {body.cuenta} no existe.")

    recurso = RecursoCloud(
        cuenta_id=body.cuenta,
        nombre=body.nombre,
        tipo=body.tipo,
        region=body.region,
        resource_external_id=body.resource_external_id,
        etiquetas=body.etiquetas,
        activo=True,
    )
    db.add(recurso)
    await db.commit()
    await db.refresh(recurso)
    await db.refresh(recurso, ["cuenta"])

    # Invalidate list cache for this account
    await cache_invalidate(str(body.cuenta), _LIST_CACHE_TYPE)
    logger.info("RecursoCloud created: %s for cuenta %s", recurso.id, recurso.cuenta_id)

    return RecursoCloudOut.from_orm_with_cuenta(recurso)


@router.get("/{recurso_id}", response_model=RecursoCloudOut)
async def get_recurso(
    recurso_id: uuid.UUID,
    empresa_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_read_db),
):
    cached = await cache_get(str(recurso_id), _DETAIL_CACHE_TYPE)
    if cached is not None:
        return RecursoCloudOut.model_validate(cached)

    stmt = (
        select(RecursoCloud)
        .options(
            selectinload(RecursoCloud.cuenta).selectinload(CuentaCloud.proveedor)
        )
        .where(RecursoCloud.id == recurso_id)
    )
    result = await db.execute(stmt)
    recurso = result.scalar_one_or_none()
    if recurso is None:
        raise HTTPException(status_code=404, detail="RecursoCloud no encontrado.")

    out = RecursoCloudOut.from_orm_with_cuenta(recurso)
    await cache_set(str(recurso_id), _DETAIL_CACHE_TYPE, out.model_dump(mode="json"))
    return out
