"""
Pydantic v2 schemas for manejador_cloud API.
Fields and field names match the Django serializers exactly so existing
clients (manejador_usuarios resource_client, JMeter, frontend) need no changes.
"""
import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, Any

from pydantic import BaseModel, Field, ConfigDict


# ── ProveedorCloud ─────────────────────────────────────────────────────────

class ProveedorCloudOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nombre: str
    tipo: str
    activo: bool
    creado_en: datetime


# ── CuentaCloud ────────────────────────────────────────────────────────────

class CuentaCloudCreate(BaseModel):
    nombre: str
    proveedor: uuid.UUID = Field(..., description="UUID of ProveedorCloud")
    proyecto_id: uuid.UUID
    account_external_id: str
    region: str = "us-east-1"


class CuentaCloudOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nombre: str
    proveedor: uuid.UUID = Field(..., alias="proveedor_id")
    proveedor_tipo: Optional[str] = None
    proveedor_nombre: Optional[str] = None
    proyecto_id: uuid.UUID
    account_external_id: str
    region: str
    activa: bool
    creada_en: datetime
    actualizada_en: datetime

    @classmethod
    def from_orm_with_proveedor(cls, obj: Any) -> "CuentaCloudOut":
        """Build schema including nested proveedor fields."""
        data = {
            "id": obj.id,
            "nombre": obj.nombre,
            "proveedor_id": obj.proveedor_id,
            "proveedor_tipo": obj.proveedor.tipo if obj.proveedor else None,
            "proveedor_nombre": obj.proveedor.nombre if obj.proveedor else None,
            "proyecto_id": obj.proyecto_id,
            "account_external_id": obj.account_external_id,
            "region": obj.region,
            "activa": obj.activa,
            "creada_en": obj.creada_en,
            "actualizada_en": obj.actualizada_en,
        }
        return cls.model_validate(data)


class CuentaCloudValidation(BaseModel):
    cuenta_cloud_id: uuid.UUID
    activa: bool
    proveedor_tipo: Optional[str] = None


# ── RecursoCloud ───────────────────────────────────────────────────────────

class RecursoCloudCreate(BaseModel):
    nombre: str
    tipo: str
    region: str
    resource_external_id: str
    etiquetas: dict = Field(default_factory=dict)
    cuenta: uuid.UUID = Field(..., description="UUID of CuentaCloud")


class RecursoCloudOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nombre: str
    tipo: str
    region: str
    resource_external_id: str
    etiquetas: dict
    activo: bool
    cuenta: uuid.UUID = Field(..., alias="cuenta_id")
    cuenta_nombre: Optional[str] = None
    proveedor_tipo: Optional[str] = None
    creado_en: datetime
    actualizado_en: datetime

    @classmethod
    def from_orm_with_cuenta(cls, obj: Any) -> "RecursoCloudOut":
        data = {
            "id": obj.id,
            "nombre": obj.nombre,
            "tipo": obj.tipo,
            "region": obj.region,
            "resource_external_id": obj.resource_external_id,
            "etiquetas": obj.etiquetas or {},
            "activo": obj.activo,
            "cuenta_id": obj.cuenta_id,
            "cuenta_nombre": obj.cuenta.nombre if obj.cuenta else None,
            "proveedor_tipo": (
                obj.cuenta.proveedor.tipo
                if obj.cuenta and obj.cuenta.proveedor
                else None
            ),
            "creado_en": obj.creado_en,
            "actualizado_en": obj.actualizado_en,
        }
        return cls.model_validate(data)


# ── MetricaConsumo ─────────────────────────────────────────────────────────

class MetricaConsumoCreate(BaseModel):
    recurso: uuid.UUID = Field(..., description="UUID of RecursoCloud")
    tipo_metrica: str
    periodo_inicio: date
    periodo_fin: date
    valor: Decimal
    costo: Decimal = Decimal("0")
    moneda: str = "USD"


class MetricaConsumoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recurso: uuid.UUID = Field(..., alias="recurso_id")
    recurso_nombre: Optional[str] = None
    tipo_metrica: str
    periodo_inicio: date
    periodo_fin: date
    valor: Decimal
    costo: Decimal
    moneda: str
    registrada_en: datetime

    @classmethod
    def from_orm_with_recurso(cls, obj: Any) -> "MetricaConsumoOut":
        data = {
            "id": obj.id,
            "recurso_id": obj.recurso_id,
            "recurso_nombre": obj.recurso.nombre if obj.recurso else None,
            "tipo_metrica": obj.tipo_metrica,
            "periodo_inicio": obj.periodo_inicio,
            "periodo_fin": obj.periodo_fin,
            "valor": obj.valor,
            "costo": obj.costo,
            "moneda": obj.moneda,
            "registrada_en": obj.registrada_en,
        }
        return cls.model_validate(data)
