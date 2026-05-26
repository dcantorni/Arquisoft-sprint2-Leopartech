"""
Idempotent seed script for cloud_db.
Same UUIDs as the old Django seed_cloud_data management command.

Run: python seed.py
Called automatically by entrypoint.sh before uvicorn starts.
Writes to the PRIMARY via write_engine (DATABASE_HOST).
"""
import asyncio
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database import WriteSession, write_engine
from models.cloud_models import Base, ProveedorCloud, CuentaCloud, RecursoCloud, MetricaConsumo

# ── Well-known UUIDs — must match JMeter projects_payload.json ──────────────
CUENTA_CLOUD_IDS = [
    uuid.UUID("550e8400-e29b-41d4-a716-446655440011"),
    uuid.UUID("550e8400-e29b-41d4-a716-446655440012"),
    uuid.UUID("550e8400-e29b-41d4-a716-446655440013"),
    uuid.UUID("550e8400-e29b-41d4-a716-446655440014"),
    uuid.UUID("550e8400-e29b-41d4-a716-446655440015"),
]
SEED_PROYECTO_ID = uuid.UUID("550e8400-e29b-41d4-a716-446655440099")


async def seed(db: AsyncSession) -> None:
    print("Seeding cloud data...")

    # 1. ProveedorCloud
    for tipo, nombre, config in [
        ("AWS", "Amazon Web Services", {"regions": ["us-east-1", "us-west-2", "eu-west-1"]}),
        ("GCP", "Google Cloud Platform", {"regions": ["us-central1", "us-east1", "europe-west1"]}),
    ]:
        result = await db.execute(select(ProveedorCloud).where(ProveedorCloud.tipo == tipo))
        if result.scalar_one_or_none() is None:
            db.add(ProveedorCloud(nombre=nombre, tipo=tipo, activo=True, configuracion=config))
    await db.commit()

    result = await db.execute(select(ProveedorCloud).where(ProveedorCloud.tipo == "AWS"))
    aws = result.scalar_one()
    result = await db.execute(select(ProveedorCloud).where(ProveedorCloud.tipo == "GCP"))
    gcp = result.scalar_one()
    proveedores = [aws, aws, gcp, aws, gcp]
    print(f"  ProveedorCloud: AWS={aws.id}  GCP={gcp.id}")

    # 2. CuentaCloud
    cuentas = []
    for i, cuenta_id in enumerate(CUENTA_CLOUD_IDS):
        proveedor = proveedores[i]
        result = await db.execute(select(CuentaCloud).where(CuentaCloud.id == cuenta_id))
        cuenta = result.scalar_one_or_none()
        if cuenta is None:
            cuenta = CuentaCloud(
                id=cuenta_id,
                nombre=f"Cuenta {proveedor.tipo} {i + 1}",
                proveedor_id=proveedor.id,
                proyecto_id=SEED_PROYECTO_ID,
                account_external_id=f"{proveedor.tipo.lower()}-seed-{i + 1:04d}",
                region="us-east-1" if proveedor.tipo == "AWS" else "us-central1",
                activa=True,
            )
            db.add(cuenta)
            print(f"  CuentaCloud {cuenta_id} [created]")
        else:
            print(f"  CuentaCloud {cuenta_id} [exists]")
        cuentas.append(cuenta)
    await db.commit()

    # 3. RecursoCloud
    tipos = ["EC2", "S3", "RDS", "LAMBDA", "EC2", "S3", "RDS", "EC2", "LAMBDA", "S3"]
    recursos = []
    for i in range(10):
        recurso_id = uuid.UUID(f"aaaaaaaa-aaaa-0000-0000-{(i + 1):012x}")
        result = await db.execute(select(RecursoCloud).where(RecursoCloud.id == recurso_id))
        recurso = result.scalar_one_or_none()
        cuenta = cuentas[i % len(cuentas)]
        if recurso is None:
            tipo = tipos[i]
            recurso = RecursoCloud(
                id=recurso_id,
                cuenta_id=cuenta.id,
                nombre=f"Recurso-{tipo}-{i + 1}",
                tipo=tipo,
                region=cuenta.region,
                resource_external_id=f"arn:aws:{tipo.lower()}:us-east-1:seed:{i + 1}",
                etiquetas={"env": "seed", "index": str(i + 1)},
                activo=True,
            )
            db.add(recurso)
            print(f"  RecursoCloud {recurso_id} [created]")
        else:
            print(f"  RecursoCloud {recurso_id} [exists]")
        recursos.append(recurso)
    await db.commit()

    # 4. MetricaConsumo
    hoy = date.today()
    inicio = date(hoy.year, hoy.month, 1)
    tipos_metrica = ["COSTO", "CPU", "MEMORIA", "ALMACENAMIENTO", "TRANSFERENCIA"]
    for i in range(5):
        metrica_id = uuid.UUID(f"bbbbbbbb-bbbb-0000-0000-{(i + 1):012x}")
        result = await db.execute(
            select(MetricaConsumo).where(MetricaConsumo.id == metrica_id)
        )
        if result.scalar_one_or_none() is None:
            db.add(MetricaConsumo(
                id=metrica_id,
                recurso_id=recursos[i].id,
                tipo_metrica=tipos_metrica[i],
                periodo_inicio=inicio,
                periodo_fin=hoy,
                valor=Decimal(str(round(100.0 * (i + 1), 6))),
                costo=Decimal(str(round(10.0 * (i + 1), 4))),
                moneda="USD",
            ))
            print(f"  MetricaConsumo {metrica_id} [created]")
        else:
            print(f"  MetricaConsumo {metrica_id} [exists]")
    await db.commit()
    print("Cloud seed data ready.")


async def main() -> None:
    # Tables are created by the same Django migration that already ran on the DB;
    # seed.py only inserts data. If running against a fresh DB (e.g. local dev
    # without the Django migration history), uncomment the next line:
    # async with write_engine.begin() as conn:
    #     await conn.run_sync(Base.metadata.create_all)
    async with WriteSession() as session:
        await seed(session)


if __name__ == "__main__":
    asyncio.run(main())
