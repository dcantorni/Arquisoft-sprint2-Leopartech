"""
/cloud/metrics endpoints.

GET  /cloud/metrics  → list metrics (optional ?recurso_id= / ?tipo_metrica=)
POST /cloud/metrics  → record a new metric
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth import require_tenant
from database import get_read_db, get_write_db
from models.cloud_models import MetricaConsumo, RecursoCloud
from schemas.cloud_schemas import MetricaConsumoCreate, MetricaConsumoOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cloud/metrics", tags=["metrics"])


@router.get("", response_model=list[MetricaConsumoOut])
async def list_metricas(
    recurso_id: Optional[uuid.UUID] = Query(None),
    tipo_metrica: Optional[str] = Query(None),
    empresa_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_read_db),
):
    stmt = (
        select(MetricaConsumo)
        .options(selectinload(MetricaConsumo.recurso))
        .order_by(MetricaConsumo.registrada_en.desc())
        .limit(100)
    )
    if recurso_id:
        stmt = stmt.where(MetricaConsumo.recurso_id == recurso_id)
    if tipo_metrica:
        stmt = stmt.where(MetricaConsumo.tipo_metrica == tipo_metrica.upper())

    result = await db.execute(stmt)
    metricas = result.scalars().all()
    return [MetricaConsumoOut.from_orm_with_recurso(m) for m in metricas]


@router.post("", response_model=MetricaConsumoOut, status_code=201)
async def create_metrica(
    body: MetricaConsumoCreate,
    empresa_id: str = Depends(require_tenant),
    db: AsyncSession = Depends(get_write_db),
):
    metrica = MetricaConsumo(
        recurso_id=body.recurso,
        tipo_metrica=body.tipo_metrica.upper(),
        periodo_inicio=body.periodo_inicio,
        periodo_fin=body.periodo_fin,
        valor=body.valor,
        costo=body.costo,
        moneda=body.moneda.upper(),
    )
    db.add(metrica)
    await db.commit()
    await db.refresh(metrica)
    await db.refresh(metrica, ["recurso"])
    logger.info("MetricaConsumo created: %s for recurso %s", metrica.id, metrica.recurso_id)
    return MetricaConsumoOut.from_orm_with_recurso(metrica)
