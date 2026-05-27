import uuid
import logging
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import SessionLocalWrite
from models import ProveedorCloud, CuentaCloud, RecursoCloud, MetricaConsumo, Proyecto

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def seed_data():
    db = SessionLocalWrite()
    try:
        # 1. ProveedorCloud
        proveedor_aws = db.query(ProveedorCloud).filter(ProveedorCloud.tipo == 'AWS').first()
        if not proveedor_aws:
            proveedor_aws = ProveedorCloud(
                nombre='Amazon Web Services',
                tipo='AWS',
                configuracion={'supported_regions': ['us-east-1', 'us-west-2']}
            )
            db.add(proveedor_aws)
            db.commit()
            logger.info("Created ProveedorCloud AWS")
        
        # 2. Proyecto (Seed 5,000 projects for ASR16 load tests)
        existing_proyectos = db.query(func.count(Proyecto.id)).scalar()
        if existing_proyectos < 5000:
            logger.info(f"Seeding {5000 - existing_proyectos} Proyecto records...")
            # Fixed empresa_id so JMeter payloads reference a known tenant
            empresa_demo = uuid.UUID('550e8400-e29b-41d4-a716-446655440001')
            proyectos_to_create = []
            for i in range(existing_proyectos, 5000):
                proyectos_to_create.append(Proyecto(
                    id=uuid.uuid4(),
                    nombre=f"Proyecto-Demo-{i+1}",
                    descripcion=f"Proyecto de demostración #{i+1} para pruebas de carga",
                    empresa_id=empresa_demo,
                    presupuesto={"monto_mensual": str(1000 + i * 10), "moneda": "USD", "alerta_porcentaje": 80},
                    activo=True,
                ))
            batch_size = 500
            for i in range(0, len(proyectos_to_create), batch_size):
                db.bulk_save_objects(proyectos_to_create[i:i+batch_size])
                db.commit()
            logger.info(f"Seeded {len(proyectos_to_create)} Proyecto records.")
        else:
            logger.info(f"Proyecto already seeded: {existing_proyectos} records")

        # 4. CuentaCloud — fixed test UUIDs required by JMeter payload
        test_cuentas = [
            uuid.UUID('550e8400-e29b-41d4-a716-446655440011'),
            uuid.UUID('550e8400-e29b-41d4-a716-446655440012'),
        ]
        for tc_id in test_cuentas:
            exists = db.query(CuentaCloud).filter(CuentaCloud.id == tc_id).first()
            if not exists:
                db.add(CuentaCloud(
                    id=tc_id,
                    nombre=f"Cuenta-JMeter-{str(tc_id)[-4:]}",
                    proveedor_id=proveedor_aws.id,
                    proyecto_id=uuid.UUID('550e8400-e29b-41d4-a716-446655440099'),
                    account_external_id=f"aws-jmeter-{str(tc_id)[-4:]}",
                    region="us-east-1",
                    activa=True,
                ))
                logger.info("Created fixed test CuentaCloud %s", tc_id)
        db.commit()

        # 4b. CuentaCloud (Seed 100 accounts)
        existing_cuentas = db.query(func.count(CuentaCloud.id)).scalar()
        if existing_cuentas < 100:
            logger.info(f"Seeding {100 - existing_cuentas} CuentaCloud records...")
            cuentas_to_create = []
            for i in range(existing_cuentas, 100):
                cuentas_to_create.append(CuentaCloud(
                    id=uuid.uuid4(),
                    nombre=f"Cuenta-Demo-{i+1}",
                    proveedor_id=proveedor_aws.id,
                    proyecto_id=uuid.uuid4(),  # Mock project ID
                    account_external_id=f"aws-acc-{100000000000+i}",
                    region="us-east-1",
                    activa=True
                ))
            db.bulk_save_objects(cuentas_to_create)
            db.commit()
        
        # Fetch accounts for resources
        cuentas = [c.id for c in db.query(CuentaCloud).limit(100).all()]
        
        # 5. RecursoCloud (Seed 20,000 resources)
        existing_recursos = db.query(func.count(RecursoCloud.id)).scalar()
        if existing_recursos < 20000:
            tipos = ['EC2', 'S3', 'RDS', 'LAMBDA', 'EKS', 'VPC', 'OTRO']
            recursos_to_create = []
            for i in range(existing_recursos, 20000):
                recursos_to_create.append(RecursoCloud(
                    nombre=f'Recurso-{i:05d}',
                    tipo=tipos[i % len(tipos)],
                    region='us-east-1',
                    cuenta_id=cuentas[i % len(cuentas)],
                    resource_external_id=f"arn:aws:{tipos[i % len(tipos)].lower()}:us-east-1:seed:{i}",
                    activo=True,
                ))
            
            logger.info(f"Seeding {len(recursos_to_create)} RecursoCloud records in batches...")
            batch_size = 1000
            for i in range(0, len(recursos_to_create), batch_size):
                db.bulk_save_objects(recursos_to_create[i:i+batch_size])
                db.commit()
            logger.info(f"Seeded {len(recursos_to_create)} RecursoCloud records.")
        else:
            logger.info(f"RecursoCloud already seeded: {existing_recursos} records")
        
        # 6. MetricaConsumo
        recursos = db.query(RecursoCloud).limit(5).all()
        hoy = date.today()
        inicio = date(hoy.year, hoy.month, 1)
        tipos_metrica = ['COSTO', 'CPU', 'MEMORIA', 'ALMACENAMIENTO', 'TRANSFERENCIA']
        
        for i, recurso in enumerate(recursos):
            tipo_m = tipos_metrica[i % len(tipos_metrica)]
            metrica = db.query(MetricaConsumo).filter(
                MetricaConsumo.recurso_id == recurso.id,
                MetricaConsumo.tipo_metrica == tipo_m,
                MetricaConsumo.periodo_inicio == inicio
            ).first()
            if not metrica:
                db.add(MetricaConsumo(
                    recurso_id=recurso.id,
                    tipo_metrica=tipo_m,
                    periodo_inicio=inicio,
                    periodo_fin=hoy,
                    valor=100.5 * (i + 1),
                    costo=10.5 * (i + 1),
                    moneda='USD'
                ))
        db.commit()
        logger.info("MetricaConsumo seeded.")

    except Exception as e:
        logger.error(f"Error seeding data: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_data()
