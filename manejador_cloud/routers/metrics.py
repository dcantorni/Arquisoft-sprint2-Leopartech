import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from database import get_read_db, get_write_db
from dependencies import get_current_tenant
import models, schemas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metrics", tags=["Metrics"])

@router.get("", response_model=List[schemas.MetricaConsumoResponse])
def get_metrics(
    db: Session = Depends(get_read_db),
    tenant_id: str = Depends(get_current_tenant)
):
    stmt = select(models.MetricaConsumo).order_by(models.MetricaConsumo.registrada_en.desc()).limit(100)
    metricas = db.execute(stmt).scalars().all()
    
    return [
        schemas.MetricaConsumoResponse(
            **m.__dict__,
            recurso_nombre=m.recurso.nombre if m.recurso else None
        ) for m in metricas
    ]

@router.post("", response_model=schemas.MetricaConsumoResponse, status_code=status.HTTP_201_CREATED)
def create_metric(
    metrica: schemas.MetricaConsumoCreate,
    db: Session = Depends(get_write_db),
    tenant_id: str = Depends(get_current_tenant)
):
    try:
        m = models.MetricaConsumo(**metrica.model_dump())
        db.add(m)
        db.commit()
        db.refresh(m)
        
        return schemas.MetricaConsumoResponse(
            **m.__dict__,
            recurso_nombre=m.recurso.nombre if m.recurso else None
        )
    except Exception as e:
        logger.exception("Error creating MetricaConsumo")
        raise HTTPException(status_code=400, detail=str(e))
