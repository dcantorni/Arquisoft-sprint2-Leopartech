"""
SQLAlchemy async models for cloud_db.
Table names match the existing Django migration exactly so the FastAPI
rewrite can share the same Postgres database without a schema change.
"""
import uuid
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, Numeric,
    ForeignKey, Index, text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class ProveedorCloud(Base):
    """Cloud provider — AWS mandatory, GCP optional.
    Table: proveedores_cloud (created by Django migration 0001_initial)
    """
    __tablename__ = "proveedores_cloud"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(100), nullable=False)
    tipo = Column(String(10), nullable=False, unique=True, index=True)  # AWS | GCP | AZURE
    activo = Column(Boolean, default=True, nullable=False, index=True)
    configuracion = Column(JSONB, default=dict, nullable=False)
    creado_en = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    cuentas = relationship("CuentaCloud", back_populates="proveedor", lazy="selectin")


class CuentaCloud(Base):
    """Cloud account linked to a project and a provider.
    Table: cuentas_cloud (created by Django migration 0001_initial)
    """
    __tablename__ = "cuentas_cloud"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre = Column(String(255), nullable=False)
    proveedor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("proveedores_cloud.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    proyecto_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    account_external_id = Column(String(100), nullable=False)
    region = Column(String(50), nullable=False, default="us-east-1")
    activa = Column(Boolean, default=True, nullable=False, index=True)
    creada_en = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    actualizada_en = Column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=datetime.utcnow,
        nullable=False,
    )

    proveedor = relationship("ProveedorCloud", back_populates="cuentas", lazy="selectin")
    recursos = relationship("RecursoCloud", back_populates="cuenta", lazy="select")

    # Composite indexes required by spec (CHANGE 2 — CQRS read optimisation)
    __table_args__ = (
        Index("idx_cuenta_activa", "activa", "proveedor_id"),
        Index("cuentas_proyecto_idx", "proyecto_id"),
        Index("cuentas_proveedor_activa_idx", "proveedor_id", "activa"),
    )


class RecursoCloud(Base):
    """A specific cloud resource within a cloud account.
    Table: recursos_cloud (created by Django migration 0001_initial)
    """
    __tablename__ = "recursos_cloud"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cuenta_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cuentas_cloud.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    nombre = Column(String(255), nullable=False, index=True)
    tipo = Column(String(20), nullable=False, index=True)  # EC2 | S3 | RDS | LAMBDA | EKS | VPC | OTRO
    region = Column(String(50), nullable=False)
    resource_external_id = Column(String(200), nullable=False)
    etiquetas = Column(JSONB, default=dict, nullable=False)
    activo = Column(Boolean, default=True, nullable=False, index=True)
    creado_en = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    actualizado_en = Column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=datetime.utcnow,
        nullable=False,
    )

    cuenta = relationship("CuentaCloud", back_populates="recursos", lazy="selectin")
    metricas = relationship("MetricaConsumo", back_populates="recurso", lazy="select")

    __table_args__ = (
        Index("idx_recurso_cuenta", "cuenta_id", "activo"),
        Index("recursos_cuenta_activo_idx", "cuenta_id", "activo"),
        Index("recursos_tipo_activo_idx", "tipo", "activo"),
        Index("recursos_region_idx", "region"),
    )


class MetricaConsumo(Base):
    """Consumption metric for a cloud resource.
    Table: metricas_consumo (created by Django migration 0001_initial)
    """
    __tablename__ = "metricas_consumo"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recurso_id = Column(
        UUID(as_uuid=True),
        ForeignKey("recursos_cloud.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tipo_metrica = Column(String(20), nullable=False, index=True)  # COSTO | CPU | MEMORIA | ...
    periodo_inicio = Column(Date, nullable=False, index=True)
    periodo_fin = Column(Date, nullable=False)
    valor = Column(Numeric(20, 6), nullable=False)
    costo = Column(Numeric(15, 4), nullable=False, default=Decimal("0"))
    moneda = Column(String(3), nullable=False, default="USD")
    registrada_en = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    recurso = relationship("RecursoCloud", back_populates="metricas", lazy="selectin")

    __table_args__ = (
        Index("idx_metrica_empresa_periodo", "periodo_inicio", "recurso_id"),
        Index("met_rec_tipo_per_idx", "recurso_id", "tipo_metrica", "periodo_inicio"),
        Index("metricas_periodo_idx", "periodo_inicio", "periodo_fin"),
    )
