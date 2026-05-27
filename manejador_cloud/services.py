import logging
from sqlalchemy.orm import Session
from sqlalchemy.exc import NoResultFound
from sqlalchemy import select
import models, schemas
from cache import CuentaCloudCache, RecursoCloudCache

logger = logging.getLogger(__name__)

class CuentaCloudService:
    @staticmethod
    def get_by_id(db: Session, cuenta_id: str) -> dict | None:
        cached = CuentaCloudCache.get_detail(cuenta_id)
        if cached:
            return cached

        stmt = select(models.CuentaCloud).where(models.CuentaCloud.id == cuenta_id)
        cuenta = db.execute(stmt).scalar_one_or_none()
        if not cuenta:
            return None

        # To dict based on response schema
        data = schemas.CuentaCloudResponse(
            **cuenta.__dict__, 
            proveedor_tipo=cuenta.proveedor.tipo if cuenta.proveedor else None,
            proveedor_nombre=cuenta.proveedor.nombre if cuenta.proveedor else None
        ).model_dump(mode='json')
        CuentaCloudCache.set_detail(cuenta_id, data)
        return data

    @staticmethod
    def validate(db: Session, cuenta_id: str) -> dict:
        cached = CuentaCloudCache.get_validation(cuenta_id)
        if cached is not None:
            return {'cuenta_cloud_id': cuenta_id, 'activa': cached}

        stmt = select(models.CuentaCloud).where(models.CuentaCloud.id == cuenta_id)
        cuenta = db.execute(stmt).scalar_one_or_none()
        if not cuenta:
            result = {'cuenta_cloud_id': cuenta_id, 'activa': False}
            CuentaCloudCache.set_validation(cuenta_id, False)
            return result

        result = {
            'cuenta_cloud_id': str(cuenta.id),
            'activa': cuenta.activa,
            'proveedor_tipo': cuenta.proveedor.tipo if cuenta.proveedor else None,
        }
        CuentaCloudCache.set_validation(cuenta_id, cuenta.activa)
        return result

    @staticmethod
    def create(db: Session, data: schemas.CuentaCloudCreate) -> schemas.CuentaCloudResponse:
        cuenta = models.CuentaCloud(**data.model_dump())
        db.add(cuenta)
        db.commit()
        db.refresh(cuenta)

        # Warm cache
        CuentaCloudCache.set_validation(str(cuenta.id), cuenta.activa)
        
        # We need the relations to construct the response
        resp = schemas.CuentaCloudResponse(
            **cuenta.__dict__, 
            proveedor_tipo=cuenta.proveedor.tipo if cuenta.proveedor else None,
            proveedor_nombre=cuenta.proveedor.nombre if cuenta.proveedor else None
        )
        CuentaCloudCache.set_detail(str(cuenta.id), resp.model_dump(mode='json'))
        logger.info("CuentaCloud created and cached: %s", cuenta.id)
        return resp

    @staticmethod
    def deactivate(db: Session, cuenta_id: str) -> bool:
        stmt = select(models.CuentaCloud).where(models.CuentaCloud.id == cuenta_id)
        cuenta = db.execute(stmt).scalar_one_or_none()
        if cuenta:
            cuenta.activa = False
            db.commit()
            CuentaCloudCache.invalidate(cuenta_id)
            logger.info("CuentaCloud deactivated and cache invalidated: %s", cuenta_id)
            return True
        return False


class RecursoCloudService:
    @staticmethod
    def list_by_cuenta(db: Session, cuenta_id: str) -> list:
        cached = RecursoCloudCache.get_list(cuenta_id)
        if cached is not None:
            return cached

        stmt = select(models.RecursoCloud).where(models.RecursoCloud.cuenta_id == cuenta_id, models.RecursoCloud.activo == True).order_by(models.RecursoCloud.tipo, models.RecursoCloud.nombre)
        recursos = db.execute(stmt).scalars().all()
        
        data = []
        for r in recursos:
            data.append(schemas.RecursoCloudResponse(
                **r.__dict__,
                cuenta_nombre=r.cuenta.nombre if r.cuenta else None,
                proveedor_tipo=r.cuenta.proveedor.tipo if r.cuenta and r.cuenta.proveedor else None
            ).model_dump(mode='json'))
            
        RecursoCloudCache.set_list(cuenta_id, data)
        return data

    @staticmethod
    def get_by_id(db: Session, recurso_id: str) -> dict | None:
        cached = RecursoCloudCache.get_detail(recurso_id)
        if cached:
            return cached

        stmt = select(models.RecursoCloud).where(models.RecursoCloud.id == recurso_id)
        recurso = db.execute(stmt).scalar_one_or_none()
        if not recurso:
            return None

        data = schemas.RecursoCloudResponse(
            **recurso.__dict__,
            cuenta_nombre=recurso.cuenta.nombre if recurso.cuenta else None,
            proveedor_tipo=recurso.cuenta.proveedor.tipo if recurso.cuenta and recurso.cuenta.proveedor else None
        ).model_dump(mode='json')
        
        RecursoCloudCache.set_detail(recurso_id, data)
        return data

    @staticmethod
    def create(db: Session, data: schemas.RecursoCloudCreate) -> schemas.RecursoCloudResponse:
        recurso = models.RecursoCloud(**data.model_dump())
        db.add(recurso)
        db.commit()
        db.refresh(recurso)

        # Invalidate list cache
        RecursoCloudCache.invalidate_list(str(recurso.cuenta_id))
        logger.info("RecursoCloud created: %s for cuenta %s", recurso.id, recurso.cuenta_id)
        
        return schemas.RecursoCloudResponse(
            **recurso.__dict__,
            cuenta_nombre=recurso.cuenta.nombre if recurso.cuenta else None,
            proveedor_tipo=recurso.cuenta.proveedor.tipo if recurso.cuenta and recurso.cuenta.proveedor else None
        )
