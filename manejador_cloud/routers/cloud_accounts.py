import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from database import get_read_db, get_write_db
from dependencies import get_current_tenant
from services import CuentaCloudService
import models, schemas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cloud-accounts", tags=["Cloud Accounts"])

@router.get("", response_model=List[schemas.CuentaCloudResponse])
def get_cloud_accounts(
    proyecto_id: str = None, 
    db: Session = Depends(get_read_db), 
    tenant_id: str = Depends(get_current_tenant)
):
    stmt = select(models.CuentaCloud).where(models.CuentaCloud.activa == True)
    if proyecto_id:
        stmt = stmt.where(models.CuentaCloud.proyecto_id == proyecto_id)
    cuentas = db.execute(stmt).scalars().all()
    
    return [
        schemas.CuentaCloudResponse(
            **c.__dict__, 
            proveedor_tipo=c.proveedor.tipo if c.proveedor else None,
            proveedor_nombre=c.proveedor.nombre if c.proveedor else None
        ) for c in cuentas
    ]

@router.post("", response_model=schemas.CuentaCloudResponse, status_code=status.HTTP_201_CREATED)
def create_cloud_account(
    cuenta: schemas.CuentaCloudCreate,
    db: Session = Depends(get_write_db),
    tenant_id: str = Depends(get_current_tenant)
):
    try:
        return CuentaCloudService.create(db, cuenta)
    except Exception as e:
        logger.exception("Error creating CuentaCloud")
        raise HTTPException(status_code=500, detail="Error interno del servidor.")

@router.get("/{cuenta_id}", response_model=schemas.CuentaCloudResponse)
def get_cloud_account(
    cuenta_id: str, 
    db: Session = Depends(get_read_db),
    tenant_id: str = Depends(get_current_tenant)
):
    result = CuentaCloudService.get_by_id(db, cuenta_id)
    if result is None:
        raise HTTPException(status_code=404, detail="CuentaCloud no encontrada.")
    return result

@router.delete("/{cuenta_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cloud_account(
    cuenta_id: str, 
    db: Session = Depends(get_write_db),
    tenant_id: str = Depends(get_current_tenant)
):
    deactivated = CuentaCloudService.deactivate(db, cuenta_id)
    if not deactivated:
        raise HTTPException(status_code=404, detail="CuentaCloud no encontrada.")

@router.get("/{cuenta_id}/validate", response_model=schemas.CuentaCloudValidationResponse)
def validate_cloud_account(
    cuenta_id: str,
    db: Session = Depends(get_read_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """Validate whether a cloud account is active.
    Always returns 200 with {"activa": bool, "cuenta_cloud_id": str}.
    Callers (manejador_usuarios) must check the 'activa' flag themselves.
    """
    result = CuentaCloudService.validate(db, cuenta_id)
    return result
