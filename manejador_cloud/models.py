import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Date, Numeric, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from database import Base

class ProveedorCloud(Base):
    __tablename__ = 'proveedores_cloud'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(100), nullable=False)
    tipo = Column(String(10), unique=True, index=True, nullable=False)
    activo = Column(Boolean, default=True, index=True)
    configuracion = Column(JSONB, default=dict)
    creado_en = Column(DateTime(timezone=True), default=datetime.utcnow)

    cuentas = relationship("CuentaCloud", back_populates="proveedor")

class CuentaCloud(Base):
    __tablename__ = 'cuentas_cloud'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(255), nullable=False)
    proveedor_id = Column(UUID(as_uuid=True), ForeignKey('proveedores_cloud.id'), index=True, nullable=False)
    proyecto_id = Column(UUID(as_uuid=True), index=True, nullable=False)
    account_external_id = Column(String(100), nullable=False)
    region = Column(String(50), default='us-east-1')
    activa = Column(Boolean, default=True, index=True)
    creada_en = Column(DateTime(timezone=True), default=datetime.utcnow)
    actualizada_en = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    proveedor = relationship("ProveedorCloud", back_populates="cuentas")
    recursos = relationship("RecursoCloud", back_populates="cuenta", cascade="all, delete")

    __table_args__ = (
        Index('cuentas_proveedor_activa_idx', 'proveedor_id', 'activa'),
        Index('idx_cuenta_activa_proveedor', 'activa', 'proveedor_id'),
    )

class RecursoCloud(Base):
    __tablename__ = 'recursos_cloud'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cuenta_id = Column(UUID(as_uuid=True), ForeignKey('cuentas_cloud.id'), index=True, nullable=False)
    nombre = Column(String(255), index=True, nullable=False)
    tipo = Column(String(20), index=True, nullable=False)
    region = Column(String(50), nullable=False)
    resource_external_id = Column(String(200), nullable=False)
    etiquetas = Column(JSONB, default=dict)
    activo = Column(Boolean, default=True, index=True)
    creado_en = Column(DateTime(timezone=True), default=datetime.utcnow)
    actualizado_en = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    cuenta = relationship("CuentaCloud", back_populates="recursos")
    metricas = relationship("MetricaConsumo", back_populates="recurso", cascade="all, delete")

    __table_args__ = (
        Index('recursos_cuenta_activo_idx', 'cuenta_id', 'activo'),
        Index('recursos_tipo_activo_idx', 'tipo', 'activo'),
        Index('recursos_region_idx', 'region'),
        Index('idx_recurso_cuenta_activo', 'cuenta_id', 'activo'),
    )

class MetricaConsumo(Base):
    __tablename__ = 'metricas_consumo'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recurso_id = Column(UUID(as_uuid=True), ForeignKey('recursos_cloud.id'), index=True, nullable=False)
    tipo_metrica = Column(String(20), index=True, nullable=False)
    periodo_inicio = Column(Date, index=True, nullable=False)
    periodo_fin = Column(Date, nullable=False)
    valor = Column(Numeric(20, 6), nullable=False)
    costo = Column(Numeric(15, 4), default=0)
    moneda = Column(String(3), default='USD')
    registrada_en = Column(DateTime(timezone=True), default=datetime.utcnow)

    recurso = relationship("RecursoCloud", back_populates="metricas")

    __table_args__ = (
        Index('met_rec_tipo_per_idx', 'recurso_id', 'tipo_metrica', 'periodo_inicio'),
        Index('metricas_periodo_idx', 'periodo_inicio', 'periodo_fin'),
        Index('idx_metrica_recurso', 'recurso_id'),
    )


class Proyecto(Base):
    """
    Cloud project — owns one or more CuentaCloud records.
    Moved here from manejador_usuarios so that POST /projects validation
    (cloud-account lookup) is a local DB query instead of an inter-service
    HTTP call, eliminating the network hop that penalised ASR16 latency.
    """
    __tablename__ = 'proyectos'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(255), nullable=False)
    descripcion = Column(String(1000), nullable=True)
    empresa_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    presupuesto = Column(JSONB, default=dict)
    activo = Column(Boolean, default=True, index=True)
    creado_en = Column(DateTime(timezone=True), default=datetime.utcnow)
    actualizado_en = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('proyectos_empresa_activo_idx', 'empresa_id', 'activo'),
    )
