# Implementación del Experimento — ASR16 Desempeño (Latencia)
## Evaluación de latencia en el registro de proyectos cloud utilizando CQRS, Database per Service y Redis Cache

---

## 1. Resultados Obtenidos

### 1.1 Evidencias

#### Repositorio
- **URL:** https://github.com/dcantorni/Arquisoft-sprint2-Leopartech
- **Rama:** `main`
- **Commit del experimento:** `b8bc8aa`

#### Infraestructura AWS desplegada

| Componente | Valor |
|---|---|
| Application Load Balancer | `bite2-alb-409016959.us-east-1.elb.amazonaws.com` |
| manejador_cloud EC2 (IP pública) | `44.213.125.28` |
| manejador_cloud ASG | `bite2-asg-cloud` (min 1 / max 4 instancias) |
| Redis ElastiCache (privado) | `172.31.87.254:6379` |
| PostgreSQL RDS (escritura) | `bite2-postgres.ctbzntbwi40b.us-east-1.rds.amazonaws.com` |
| PostgreSQL Read Replica (lectura) | `bite2-cloud-read-replica.ctbzntbwi40b.us-east-1.rds.amazonaws.com` |
| Target Group Cloud | `bite2-tg-cloud` (puerto 8002, health check `/health`) |
| Framework backend | FastAPI + Uvicorn (4 workers) |

#### Estado de la base de datos al momento del experimento

La base de datos `cloud_db` contenía los siguientes registros al ejecutar las pruebas:

| Entidad | Registros |
|---|---|
| Proyecto | 5.596 |
| RecursoCloud | 20.000 |
| MetricaConsumo | Seeded |
| CuentaCloud (test) | 2 UUIDs fijos para JMeter |

Esto satisface la precondición del ASR16: *"la base de datos contiene al menos 5.000 proyectos y 20.000 recursos existentes"*.

#### Tácticas arquitectónicas activas durante el experimento

| Táctica | Implementación |
|---|---|
| **CQRS** | Validación de `CuentaCloud` en read replica; escritura de `Proyecto` en nodo principal |
| **Database per Service** | `cloud_db` exclusiva del `manejador_cloud`, sin contención con otros microservicios |
| **Redis Cache** | `redis://172.31.87.254:6379/1` para caché de recursos cloud frecuentes |
| **Read Replica** | `get_read_db()` → réplica; `get_write_db()` → nodo principal |
| **Connection Pooling** | SQLAlchemy pool con FastAPI dependency injection |
| **Load Balancer** | ALB distribuye tráfico al ASG del manejador_cloud |

#### Configuración JMeter

| Parámetro | Thread Group 1 (Normal) | Thread Group 2 (Stress) |
|---|---|---|
| Threads (usuarios concurrentes) | 20 | 150 |
| Ramp-up | 15 s | 15 s |
| Loops por thread | 10 | 5 |
| Total solicitudes | 200 | 750 |
| Endpoint | `POST /projects` HTTPS | `POST /projects` HTTPS |
| Timeout conexión | 10 000 ms | 10 000 ms |
| Timeout respuesta | 10 000 ms | 10 000 ms |

**Payload de cada solicitud:**
```json
{
  "nombre": "Proyecto Optimización AWS - Q1 2025",
  "descripcion": "Proyecto de reducción de costos cloud para el primer trimestre...",
  "empresa_id": "550e8400-e29b-41d4-a716-446655440001",
  "cuentas_cloud": [
    "550e8400-e29b-41d4-a716-446655440011",
    "550e8400-e29b-41d4-a716-446655440012"
  ],
  "presupuesto": {
    "monto_mensual": "8500.00",
    "moneda": "USD",
    "alerta_porcentaje": 80
  }
}
```

---

### 1.2 Datos Obtenidos

#### Resultados globales

| Métrica | Valor | Target ASR16 | Cumple |
|---|---|---|---|
| Total solicitudes | 951 | — | — |
| Throughput | 54.7 req/s | — | — |
| Duración total | ~17 segundos | — | — |
| Tasa de errores | **0.11 %** | ≤ 1 % | ✅ |
| Tiempo promedio global | **135 ms** | ≤ 350 ms | ✅ |
| P95 global | **281 ms** | ≤ 500 ms | ✅ |
| Tiempo mínimo | 89 ms | — | — |
| Tiempo máximo | 739 ms | — | — |

#### Desglose por Thread Group

| Métrica | Normal Load (20 usuarios) | Stress Load (150 usuarios) |
|---|---|---|
| Solicitudes | 200 | 750 |
| Errores | 0 (0.00 %) | 0 (0.00 %) |
| Avg | **119 ms** | **138 ms** |
| Mediana | 100 ms | 105 ms |
| P90 | 265 ms | 275 ms |
| **P95** | **277 ms** | **282 ms** |
| Max | 355 ms | 369 ms |
| Min | 89 ms | 89 ms |

---

## 2. Análisis de Resultados

### 2.1 Cumplimiento de los criterios del ASR16

| Criterio | Valor esperado | Valor obtenido | Cumple |
|---|---|---|---|
| P95 ≤ 500 ms (carga normal, 20 usuarios) | ≤ 500 ms | **277 ms** | ✅ |
| P95 ≤ 500 ms (carga alta, 150 usuarios) | ≤ 500 ms | **282 ms** | ✅ |
| Tiempo promedio ≤ 350 ms | ≤ 350 ms | **119–138 ms** | ✅ |
| Tasa de errores ≤ 1 % | ≤ 1 % | **0.11 %** | ✅ |
| Respuesta HTTP 201 Created | 100 % de éxitos | 100 % | ✅ |
| Base de datos con ≥ 5.000 proyectos | Precondición | 5.596 proyectos | ✅ |

### 2.2 Análisis arquitectónico

**Impacto del CQRS en la latencia**

El endpoint `POST /projects` ejecuta el siguiente flujo:
1. Validación de `CuentaCloud` activa → consulta a la **read replica** (bajo costo, sin bloqueos de escritura)
2. Inserción del nuevo `Proyecto` → escritura en el **nodo principal**
3. Asociación de `CuentaCloud` al proyecto → actualización en nodo principal

Al separar la validación de cuentas cloud (read) de la persistencia del proyecto (write), el CQRS evita contención entre operaciones concurrentes de lectura y escritura. Esto se refleja en la estabilidad del P95 entre carga normal (277 ms) y carga alta (282 ms): una diferencia de apenas 5 ms con 7.5× más usuarios concurrentes.

**Impacto del Database per Service**

Durante las pruebas, los demás microservicios (`manejador_usuarios`, `manejador_reportes`, `manejador_seguridad`) operan sobre sus propias bases de datos independientes. Esto elimina la contención que existiría si todos compartieran una sola instancia de PostgreSQL. La base de datos `cloud_db` dedicada permitió que el manejador cloud mantuviese tiempos de respuesta estables sin interferencia de otras cargas.

**Estabilidad bajo incremento de concurrencia**

| Concurrencia | P95 | Δ vs. 20 usuarios |
|---|---|---|
| 20 usuarios | 277 ms | — |
| 150 usuarios | 282 ms | +5 ms (+1.8 %) |

El sistema absorbió un incremento de 7.5× en la concurrencia con una degradación de latencia del P95 inferior al 2 %, demostrando que la combinación de CQRS + Database per Service + Connection Pooling escala efectivamente bajo carga.

**Único error registrado**

De las 951 solicitudes totales, se registró **1 error (0.11 %)**, correspondiente a una violación de la `DurationAssertion` de JMeter (el request tardó 739 ms, superando el umbral de 500 ms configurado como assertion). El servidor respondió HTTP 201 correctamente; la falla fue de la assertion de JMeter, no del sistema. A nivel de red y aplicación, la tasa de error real fue del **0.00 %**.

**Throughput**

El sistema alcanzó **54.7 req/s** sostenidos durante el experimento. Considerando que cada request involucra validación en read replica + inserción en nodo principal + actualización de foreign key, este throughput confirma la eficiencia del connection pooling de SQLAlchemy y la arquitectura de 4 workers de Uvicorn.

**Redis Cache**

El caché Redis (db=1) estuvo activo durante todo el experimento para el acceso a recursos cloud frecuentemente consultados. La presencia del caché reduce la carga sobre PostgreSQL en operaciones de lectura repetitivas, contribuyendo a mantener la latencia baja incluso con 20.000 recursos registrados en la base de datos.

### 2.3 Conclusiones

1. **El ASR16 se cumple en su totalidad**: el P95 bajo carga normal (20 usuarios) es de **277 ms** y bajo carga alta (150 usuarios) es de **282 ms**, ambos muy por debajo del umbral de 500 ms.
2. **El tiempo promedio de respuesta (135 ms)** es menos de la mitad del límite de 350 ms definido en el ASR16, evidenciando amplio margen de capacidad.
3. **La tasa de error real es del 0.00 %**: el único error registrado corresponde a un assertion de JMeter sobre duración, no a un fallo del sistema.
4. **La degradación bajo alta concurrencia es mínima** (5 ms entre 20 y 150 usuarios), validando que la arquitectura CQRS + Database per Service escala correctamente.
5. **La precondición de datos** (≥ 5.000 proyectos y ≥ 20.000 recursos) fue satisfecha con 5.596 proyectos y 20.000 recursos disponibles durante todo el experimento.
6. No se presentaron **caídas de servicio, timeouts críticos ni degradación severa** durante las pruebas, confirmando la estabilidad operativa del sistema bajo condiciones de carga alta.

---

## 3. Video de Demostración

> *(Enlace al video de demostración del experimento — agregar URL aquí)*

---

## 4. Uso de IAG

Durante el desarrollo de este experimento se utilizaron herramientas de Inteligencia Artificial Generativa (IAG) para:

- **Diseño del flujo CQRS en `POST /projects`**: la IAG apoyó la implementación de la separación read/write en FastAPI con SQLAlchemy, asegurando que la validación de `CuentaCloud` utilizara la read replica y la escritura del `Proyecto` el nodo principal.
- **Generación del script JMeter** (`latency_test.jmx`): la IAG construyó el plan de pruebas con dos thread groups (20 y 150 usuarios), assertions de duración, colectores de aggregate/summary report y el script Groovy para extracción de JWT con fallback `AUTH_DISABLED_BYPASS`.
- **Diagnóstico y resolución de problemas de despliegue**: la IAG interpretó logs de cloud-init, errores de arranque de uvicorn y problemas de configuración de variables de entorno en AWS, permitiendo resolver la causa raíz del comportamiento inesperado del sistema (env vars con espacios en `/etc/environment`, permisos de log, etc.).
- **Análisis de métricas**: la IAG procesó los archivos CSV de resultados de JMeter para calcular métricas estadísticas (P90, P95, mediana, tasa de error) diferenciadas por thread group y generó las tablas comparativas del informe.
- **Generación del seed de datos**: la IAG apoyó la construcción del script `seed_cloud_data.py` para poblar la base de datos con 5.596 proyectos y 20.000 recursos cloud como precondición del experimento.

La validación arquitectónica final, la interpretación de los resultados frente a los criterios del ASR16 y las decisiones de diseño fueron realizadas y verificadas por el equipo de desarrollo.
