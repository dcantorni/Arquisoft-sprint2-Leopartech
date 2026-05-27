"""
POST /projects  — create a cloud project with validated cloud accounts.
GET  /projects  — list projects for the current tenant (empresa_id).

Architecture note (ASR16):
  Validation of cuentas_cloud is a local DB query on the CQRS read replica,
  eliminating the inter-service HTTP hop that previously added ~50–150 ms to
  every POST /projects in manejador_usuarios.
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_read_db, get_write_db
from dependencies import get_current_tenant
import models, schemas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post(
    "",
    response_model=schemas.ProyectoResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project(
    payload: schemas.ProyectoCreate,
    db: Session = Depends(get_write_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """
    Create a project and associate the requested cloud accounts.

    Validates that every cuenta_cloud_id exists and is active before
    persisting — pure local DB read, no HTTP hop to manejador_usuarios.
    """
    cuenta_ids = [str(cid) for cid in payload.cuentas_cloud]

    # Validate all requested cloud accounts (read replica — fast path)
    read_db: Session = next(get_read_db())
    try:
        cuentas = (
            read_db.query(models.CuentaCloud)
            .filter(
                models.CuentaCloud.id.in_(cuenta_ids),
                models.CuentaCloud.activa == True,
            )
            .all()
        )
    finally:
        read_db.close()

    found_ids = {str(c.id) for c in cuentas}
    missing = set(cuenta_ids) - found_ids
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cloud accounts not found or inactive: {sorted(missing)}",
        )

    presupuesto_dict = (
        payload.presupuesto.model_dump(exclude_none=True)
        if payload.presupuesto
        else {}
    )

    nuevo = models.Proyecto(
        nombre=payload.nombre,
        descripcion=payload.descripcion,
        empresa_id=payload.empresa_id,
        presupuesto=presupuesto_dict,
        activo=True,
    )
    db.add(nuevo)
    db.flush()  # populate nuevo.id before updating cuentas

    # Associate cloud accounts with this project
    for cuenta in db.query(models.CuentaCloud).filter(
        models.CuentaCloud.id.in_(cuenta_ids)
    ).all():
        cuenta.proyecto_id = nuevo.id

    db.commit()
    db.refresh(nuevo)

    logger.info("Created Proyecto %s for empresa %s", nuevo.id, nuevo.empresa_id)

    return schemas.ProyectoResponse(
        id=nuevo.id,
        nombre=nuevo.nombre,
        descripcion=nuevo.descripcion,
        empresa_id=nuevo.empresa_id,
        presupuesto=nuevo.presupuesto,
        activo=nuevo.activo,
        creado_en=nuevo.creado_en,
        cuentas_cloud=[c.id for c in cuentas],
    )


@router.get(
    "",
    response_model=List[schemas.ProyectoResponse],
)
def list_projects(
    db: Session = Depends(get_read_db),
    tenant_id: str = Depends(get_current_tenant),
):
    """
    Return the 50 most-recent active projects for the authenticated tenant.
    Reads from the CQRS read replica for low latency.
    """
    proyectos = (
        db.query(models.Proyecto)
        .filter(
            models.Proyecto.empresa_id == tenant_id,
            models.Proyecto.activo == True,
        )
        .order_by(models.Proyecto.creado_en.desc())
        .limit(50)
        .all()
    )

    result = []
    for p in proyectos:
        cuentas = (
            db.query(models.CuentaCloud.id)
            .filter(models.CuentaCloud.proyecto_id == p.id)
            .all()
        )
        result.append(
            schemas.ProyectoResponse(
                id=p.id,
                nombre=p.nombre,
                descripcion=p.descripcion,
                empresa_id=p.empresa_id,
                presupuesto=p.presupuesto,
                activo=p.activo,
                creado_en=p.creado_en,
                cuentas_cloud=[row[0] for row in cuentas],
            )
        )
    return result
