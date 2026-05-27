import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from database import get_read_db, get_write_db
from dependencies import get_current_tenant
from services import RecursoCloudService
import models, schemas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resources", tags=["Resources"])

@router.get("", response_model=List[schemas.RecursoCloudResponse])
def get_resources(
    cuenta_id: str = None, 
    db: Session = Depends(get_read_db),
    tenant_id: str = Depends(get_current_tenant)
):
    if cuenta_id:
        return RecursoCloudService.list_by_cuenta(db, cuenta_id)
    
    # No cuenta_id: return all resources (for dashboard overview) max 100
    stmt = select(models.RecursoCloud).where(models.RecursoCloud.activo == True).limit(100)
    recursos = db.execute(stmt).scalars().all()
    
    return [
        schemas.RecursoCloudResponse(
            **r.__dict__,
            cuenta_nombre=r.cuenta.nombre if r.cuenta else None,
            proveedor_tipo=r.cuenta.proveedor.tipo if r.cuenta and r.cuenta.proveedor else None
        ) for r in recursos
    ]

@router.post("", response_model=schemas.RecursoCloudResponse, status_code=status.HTTP_201_CREATED)
def create_resource(
    recurso: schemas.RecursoCloudCreate,
    db: Session = Depends(get_write_db),
    tenant_id: str = Depends(get_current_tenant)
):
    try:
        return RecursoCloudService.create(db, recurso)
    except Exception as e:
        logger.exception("Error creating RecursoCloud")
        raise HTTPException(status_code=500, detail="Error interno del servidor.")

@router.get("/{recurso_id}", response_model=schemas.RecursoCloudResponse)
def get_resource(
    recurso_id: str, 
    db: Session = Depends(get_read_db),
    tenant_id: str = Depends(get_current_tenant)
):
    result = RecursoCloudService.get_by_id(db, recurso_id)
    if result is None:
        raise HTTPException(status_code=404, detail="RecursoCloud no encontrado.")
    return result
