from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, Dict, Any, List
from datetime import datetime, date
from uuid import UUID

class ProveedorCloudSchema(BaseModel):
    id: UUID
    nombre: str
    tipo: str
    activo: bool
    creado_en: datetime
    
    model_config = ConfigDict(from_attributes=True)

class CuentaCloudCreate(BaseModel):
    nombre: str
    proveedor_id: UUID
    proyecto_id: UUID
    account_external_id: str
    region: Optional[str] = "us-east-1"

class CuentaCloudResponse(BaseModel):
    id: UUID
    nombre: str
    proveedor_id: UUID
    proveedor_tipo: Optional[str] = None
    proveedor_nombre: Optional[str] = None
    proyecto_id: UUID
    account_external_id: str
    region: str
    activa: bool
    creada_en: datetime
    actualizada_en: datetime
    
    model_config = ConfigDict(from_attributes=True)

class CuentaCloudValidationResponse(BaseModel):
    cuenta_cloud_id: UUID
    activa: bool
    proveedor_tipo: Optional[str] = None

class RecursoCloudCreate(BaseModel):
    nombre: str
    tipo: str
    region: str
    resource_external_id: str
    etiquetas: Optional[Dict[str, Any]] = Field(default_factory=dict)
    cuenta_id: UUID

class RecursoCloudResponse(BaseModel):
    id: UUID
    nombre: str
    tipo: str
    region: str
    resource_external_id: str
    etiquetas: Dict[str, Any]
    activo: bool
    cuenta_id: UUID
    cuenta_nombre: Optional[str] = None
    proveedor_tipo: Optional[str] = None
    creado_en: datetime
    actualizado_en: datetime
    
    model_config = ConfigDict(from_attributes=True)

class MetricaConsumoCreate(BaseModel):
    recurso_id: UUID
    tipo_metrica: str
    periodo_inicio: date
    periodo_fin: date
    valor: float
    costo: Optional[float] = 0.0
    moneda: Optional[str] = "USD"

class MetricaConsumoResponse(BaseModel):
    id: UUID
    recurso_id: UUID
    recurso_nombre: Optional[str] = None
    tipo_metrica: str
    periodo_inicio: date
    periodo_fin: date
    valor: float
    costo: float
    moneda: str
    registrada_en: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Proyecto schemas — POST /projects payload and response
# ---------------------------------------------------------------------------

class PresupuestoSchema(BaseModel):
    monto_mensual: Optional[str] = None
    moneda: Optional[str] = "USD"
    alerta_porcentaje: Optional[int] = 80

class ProyectoCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=255)
    descripcion: Optional[str] = Field(None, max_length=1000)
    empresa_id: UUID
    cuentas_cloud: List[UUID] = Field(..., min_length=1)
    presupuesto: Optional[PresupuestoSchema] = None

class ProyectoResponse(BaseModel):
    id: UUID
    nombre: str
    descripcion: Optional[str] = None
    empresa_id: UUID
    presupuesto: Optional[Dict[str, Any]] = None
    activo: bool
    creado_en: datetime
    cuentas_cloud: List[UUID] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
