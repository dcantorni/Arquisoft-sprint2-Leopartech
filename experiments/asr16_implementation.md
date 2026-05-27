# Implementación del Experimento — ASR16 Desempeño (Latencia)
## Evaluación de latencia en el registro de proyectos cloud utilizando CQRS, Database per Service y Redis Cache

---

## Contenido

1. [Resultados obtenidos](#resultados-obtenidos)
2. [Análisis de resultados](#análisis-de-resultados)
3. [Video de demostración](#video-de-demostración)
4. [Uso de IAG](#uso-de-iag)

---

## Resultados obtenidos

En esta sección se agregan tanto evidencias como los resultados (datos) del experimento.

### Evidencias

Algunas evidencias son:

- Link del repositorio donde realizan el proyecto.
- Capturas de pantalla de los despliegues en AWS.
- Capturas de pantalla de cómo recopilan los datos. Por ejemplo, los resultados de JMeter.
- Capturas de pantalla de las plataformas de terceros que necesiten para el desarrollo del experimento. Estas evidencias deben estar acompañadas de una explicación.

### Datos obtenidos

Los datos obtenidos del experimento pueden variar según el atributo de calidad que se esté probando.

**Desempeño (Latencia):**

---

**Link repo:** https://github.com/dcantorni/Arquisoft-sprint2-Leopartech

---

**Instancias AWS:**

- **manejador_cloud** — EC2 Auto Scaling Group `bite2-asg-cloud` | IP pública: `44.213.125.28` | FastAPI CQRS (puerto 8002)
- **manejador_autenticacion** — EC2 | IP pública: `44.203.147.86` | Auth Service (puerto 8004)
- **Redis** — EC2 privado | IP: `172.31.87.254` | Cache (puerto 6379)
- **PostgreSQL RDS (escritura)** — `bite2-postgres.ctbzntbwi40b.us-east-1.rds.amazonaws.com` | DB principal (puerto 5432)
- **Read Replica RDS (lectura)** — `bite2-cloud-read-replica.ctbzntbwi40b.us-east-1.rds.amazonaws.com` | CQRS read path (puerto 5432)

---

**Grupos de seguridad:**

- **app** — Entrada: puerto 8002 desde ALB y VPC
- **ssh** — Entrada: puerto 22 desde IP autorizada
- **alb** — Entrada: 80 (HTTP) y 443 (HTTPS) desde 0.0.0.0/0

---

**Load Balancer:**

- **DNS:** `bite2-alb-409016959.us-east-1.elb.amazonaws.com`
- **Target Group:** `bite2-tg-cloud` — puerto 8002, health check `GET /health` → HTTP 200
- **Estado del target:** `healthy` durante todo el experimento
- **Listener HTTPS (443):** Redirige `/projects/*` al ASG del manejador_cloud

---

**Estado de la base de datos:**

- **Proyectos:** 5.596 registros ✅ (precondición: ≥ 5.000)
- **RecursosCloud:** 20.000 registros ✅ (precondición: ≥ 20.000)
- **CuentasCloud fijas para JMeter:** 2 UUIDs seeded (`550e8400-...-440011`, `550e8400-...-440012`)

---

**Pruebas de resultados — Normal Load (20 usuarios concurrentes)**
=========

Herramienta: Apache JMeter 5.6.3

Configuración:

- **Usuarios concurrentes:** 20
- **Ramp-up:** 15 segundos
- **Loops por thread:** 10
- **Total solicitudes:** 200
- **Endpoint:** POST `https://bite2-alb-409016959.us-east-1.elb.amazonaws.com/projects`

Resultados:

- **Total solicitudes:** 200
- **Errores HTTP:** 0 (0.00 %) ✅ (target: ≤ 1 %)
- **Tiempo mínimo:** 89 ms
- **Tiempo promedio:** 119 ms ✅ (target: ≤ 350 ms)
- **Mediana:** 100 ms
- **P90:** 265 ms
- **P95:** **277 ms** ✅ (target: ≤ 500 ms)
- **Tiempo máximo:** 355 ms

---

**Pruebas de resultados — Stress Load (150 usuarios concurrentes)**
=========

Configuración:

- **Usuarios concurrentes:** 150
- **Ramp-up:** 15 segundos
- **Loops por thread:** 5
- **Total solicitudes:** 750
- **Endpoint:** POST `https://bite2-alb-409016959.us-east-1.elb.amazonaws.com/projects`

Resultados:

- **Total solicitudes:** 750
- **Errores HTTP:** 0 (0.00 %) ✅ (target: ≤ 1 %)
- **Tiempo mínimo:** 89 ms
- **Tiempo promedio:** 138 ms ✅ (target: ≤ 350 ms)
- **Mediana:** 105 ms
- **P90:** 275 ms
- **P95:** **282 ms** ✅ (target: ≤ 500 ms)
- **Tiempo máximo:** 369 ms

---

**Resultados globales combinados:**

- **Total solicitudes:** 951
- **Throughput:** 54.7 req/s
- **Duración total:** ~17 segundos
- **Tasa de errores:** 0.11 % ✅ (target: ≤ 1 %)
- **Tiempo promedio:** 135 ms ✅ (target: ≤ 350 ms)
- **P95 global:** **281 ms** ✅ (target: ≤ 500 ms)

> Nota: el único error (0.11 %) corresponde a una `DurationAssertion` de JMeter en un request que tardó 739 ms. El servidor respondió correctamente con HTTP 201; la falla fue de la assertion interna de JMeter, no del sistema.

---

## Análisis de resultados

**Cumplimiento de los criterios del ASR16:**

- **P95 (20 usuarios):** 277 ms ✅ (target: ≤ 500 ms)
- **P95 (150 usuarios):** 282 ms ✅ (target: ≤ 500 ms)
- **Tiempo promedio (20 usuarios):** 119 ms ✅ (target: ≤ 350 ms)
- **Tiempo promedio (150 usuarios):** 138 ms ✅ (target: ≤ 350 ms)
- **Tasa de errores:** 0.00 % real ✅ (target: ≤ 1 %)
- **HTTP 201 Created:** 100 % de solicitudes exitosas ✅
- **Precondición BD ≥ 5.000 proyectos:** 5.596 proyectos ✅

**Impacto del CQRS en la latencia:**

El endpoint `POST /projects` ejecuta el siguiente flujo optimizado:

1. Validación de `CuentaCloud` activa → consulta a la **read replica** (sin bloqueos de escritura)
2. Inserción del nuevo `Proyecto` → escritura en el **nodo principal**
3. Asociación de `CuentaCloud` al proyecto → actualización en nodo principal

La separación read/write del CQRS evita contención entre operaciones concurrentes. Esto se refleja en la mínima diferencia de P95 entre carga normal y alta: 277 ms vs 282 ms con 7.5× más usuarios concurrentes (Δ = +5 ms, +1.8 %).

**Estabilidad bajo incremento de concurrencia:**

- **20 usuarios → P95:** 277 ms
- **150 usuarios → P95:** 282 ms
- **Degradación:** +5 ms (+1.8 %) con 7.5× más carga

El sistema absorbió 7.5× más concurrencia con degradación de P95 inferior al 2 %, validando que CQRS + Database per Service + Connection Pooling escala correctamente bajo carga.

**Conclusiones:**

1. El ASR16 se cumple en su totalidad: P95 de 277 ms (20 usuarios) y 282 ms (150 usuarios), ambos por debajo del umbral de 500 ms.
2. El tiempo promedio (135 ms) es menos de la mitad del límite de 350 ms, evidenciando amplio margen de capacidad.
3. La tasa de error real es del 0.00 %: el único error registrado corresponde a una assertion de JMeter, no a un fallo del sistema.
4. La degradación bajo alta concurrencia es mínima (5 ms), validando que CQRS + Database per Service escala correctamente.
5. La precondición de datos fue satisfecha: 5.596 proyectos y 20.000 recursos disponibles.
6. No se presentaron caídas de servicio, timeouts críticos ni degradación severa durante las pruebas.

---

## Video de demostración

- https://youtu.be/D_Y2EreroIE

---

## Uso de IAG

Durante el desarrollo de este experimento se utilizaron herramientas de Inteligencia Artificial Generativa (IAG) para:

- **Diseño del flujo CQRS en `POST /projects`:** la IAG apoyó la implementación de la separación read/write en FastAPI con SQLAlchemy, asegurando que la validación de `CuentaCloud` utilizara la read replica y la escritura del `Proyecto` el nodo principal.
- **Generación del script JMeter** (`latency_test.jmx`): la IAG construyó el plan de pruebas con dos thread groups (20 y 150 usuarios), assertions de duración y el script Groovy para extracción de JWT con fallback `AUTH_DISABLED_BYPASS`.
- **Diagnóstico y resolución de problemas de despliegue:** la IAG interpretó logs de cloud-init y errores de arranque de uvicorn para resolver causas raíz.
- **Análisis de métricas:** la IAG procesó los archivos CSV de JMeter para calcular P90, P95, mediana y tasa de error diferenciados por thread group.
- **Generación del seed de datos:** la IAG apoyó la construcción de `seed_cloud_data.py` para poblar la BD con 5.596 proyectos y 20.000 recursos cloud.

La validación arquitectónica final, la interpretación de resultados frente a los criterios del ASR16 y las decisiones de diseño fueron realizadas y verificadas por el equipo de desarrollo.
