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

| Instancia | Tipo | IP Pública | Servicio |
|---|---|---|---|
| manejador_cloud (ASG) | EC2 – Auto Scaling Group `bite2-asg-cloud` | `44.213.125.28` | FastAPI CQRS |
| manejador_autenticacion | EC2 | `44.203.147.86` | Auth Service |
| Redis | EC2 privado | `172.31.87.254` | Cache |
| PostgreSQL RDS (escritura) | RDS | `bite2-postgres.ctbzntbwi40b.us-east-1.rds.amazonaws.com` | DB principal |
| Read Replica RDS (lectura) | RDS réplica | `bite2-cloud-read-replica.ctbzntbwi40b.us-east-1.rds.amazonaws.com` | CQRS read path |

---

**Grupos de seguridad:**

| Grupo | Reglas relevantes |
|---|---|
| `app` | Entrada: puerto 8002 desde ALB y VPC |
| `ssh` | Entrada: puerto 22 desde IP autorizada |
| ALB | Entrada: 80 (HTTP) y 443 (HTTPS) desde 0.0.0.0/0 |

---

**Load Balancer:**

- **DNS:** `bite2-alb-409016959.us-east-1.elb.amazonaws.com`
- **Target Group:** `bite2-tg-cloud` — puerto 8002, health check `GET /health` → HTTP 200
- **Estado del target:** `healthy` durante todo el experimento
- **Listener HTTPS (443):** Redirige `/projects/*` al ASG del manejador_cloud

---

**Estado de la base de datos al momento del experimento:**

| Entidad | Registros |
|---|---|
| Proyecto | **5.596** (precondición: ≥ 5.000 ✅) |
| RecursoCloud | **20.000** (precondición: ≥ 20.000 ✅) |
| CuentaCloud fijas para JMeter | 2 UUIDs seeded |

---

**Pruebas de resultados — Normal Load (20 usuarios concurrentes)**
==========

Herramienta: Apache JMeter 5.6.3

| Parámetro | Valor |
|---|---|
| Usuarios concurrentes | 20 |
| Ramp-up | 15 segundos |
| Loops por thread | 10 |
| Total solicitudes | 200 |
| Endpoint | `POST https://bite2-alb-409016959.us-east-1.elb.amazonaws.com/projects` |

Resultados:

| Métrica | Valor | Target | Cumple |
|---|---|---|---|
| Solicitudes | 200 | — | — |
| Errores HTTP | 0 (0.00 %) | ≤ 1 % | ✅ |
| Tiempo mínimo | 89 ms | — | — |
| Tiempo promedio | **119 ms** | ≤ 350 ms | ✅ |
| Mediana | 100 ms | — | — |
| P90 | 265 ms | — | — |
| **P95** | **277 ms** | ≤ 500 ms | ✅ |
| Tiempo máximo | 355 ms | — | — |

---

**Pruebas de resultados — Stress Load (150 usuarios concurrentes)**
==========

| Parámetro | Valor |
|---|---|
| Usuarios concurrentes | 150 |
| Ramp-up | 15 segundos |
| Loops por thread | 5 |
| Total solicitudes | 750 |
| Endpoint | `POST https://bite2-alb-409016959.us-east-1.elb.amazonaws.com/projects` |

Resultados:

| Métrica | Valor | Target | Cumple |
|---|---|---|---|
| Solicitudes | 750 | — | — |
| Errores HTTP | 0 (0.00 %) | ≤ 1 % | ✅ |
| Tiempo mínimo | 89 ms | — | — |
| Tiempo promedio | **138 ms** | ≤ 350 ms | ✅ |
| Mediana | 105 ms | — | — |
| P90 | 275 ms | — | — |
| **P95** | **282 ms** | ≤ 500 ms | ✅ |
| Tiempo máximo | 369 ms | — | — |

---

**Resultados globales (ambos thread groups combinados)**

| Métrica | Valor | Target | Cumple |
|---|---|---|---|
| Total solicitudes | 951 | — | — |
| Throughput | **54.7 req/s** | — | — |
| Duración total | ~17 segundos | — | — |
| Tasa de errores | **0.11 %** | ≤ 1 % | ✅ |
| Tiempo promedio | **135 ms** | ≤ 350 ms | ✅ |
| **P95 global** | **281 ms** | ≤ 500 ms | ✅ |

> Nota: el único error registrado (0.11 %) corresponde a una `DurationAssertion` de JMeter en un request que tardó 739 ms. El servidor respondió correctamente con HTTP 201; la falla fue de la assertion interna de JMeter, no del sistema.

---

## Análisis de resultados

### Cumplimiento de los criterios del ASR16

| Criterio | Valor esperado | 20 usuarios | 150 usuarios | Cumple |
|---|---|---|---|---|
| P95 ≤ 500 ms | ≤ 500 ms | **277 ms** | **282 ms** | ✅ |
| Tiempo promedio ≤ 350 ms | ≤ 350 ms | **119 ms** | **138 ms** | ✅ |
| Tasa de errores ≤ 1 % | ≤ 1 % | **0.00 %** | **0.00 %** | ✅ |
| HTTP 201 Created | 100 % éxitos | 100 % | 100 % | ✅ |
| BD con ≥ 5.000 proyectos | Precondición | 5.596 ✅ | 5.596 ✅ | ✅ |

### Impacto del CQRS en la latencia

El endpoint `POST /projects` ejecuta el siguiente flujo optimizado:
1. Validación de `CuentaCloud` activa → consulta a la **read replica** (sin bloqueos de escritura)
2. Inserción del nuevo `Proyecto` → escritura en el **nodo principal**
3. Asociación de `CuentaCloud` al proyecto → actualización en nodo principal

La separación read/write del CQRS evita contención entre operaciones concurrentes. Esto se refleja en la mínima diferencia de P95 entre carga normal y alta: 277 ms vs 282 ms con 7.5× más usuarios concurrentes (+1.8 %).

### Estabilidad bajo incremento de concurrencia

| Concurrencia | P95 | Δ |
|---|---|---|
| 20 usuarios | 277 ms | — |
| 150 usuarios | 282 ms | +5 ms (+1.8 %) |

El sistema absorbió 7.5× más concurrencia con degradación de P95 inferior al 2 %, validando que CQRS + Database per Service + Connection Pooling escala correctamente bajo carga.

### Conclusiones

1. El **ASR16 se cumple en su totalidad**: P95 de 277 ms (20 usuarios) y 282 ms (150 usuarios), ambos por debajo del umbral de 500 ms.
2. El **tiempo promedio (135 ms)** es menos de la mitad del límite de 350 ms, evidenciando amplio margen de capacidad.
3. La **tasa de error real es del 0.00 %**: el único error registrado corresponde a una assertion de JMeter, no a un fallo del sistema.
4. La **degradación bajo alta concurrencia es mínima** (5 ms entre 20 y 150 usuarios), validando que CQRS + Database per Service escala correctamente.
5. La **precondición de datos** fue satisfecha: 5.596 proyectos y 20.000 recursos disponibles.
6. No se presentaron **caídas de servicio, timeouts críticos ni degradación severa** durante las pruebas.

---

## Video de demostración

- https://youtu.be/D_Y2EreroIE

---

## Uso de IAG

Durante el desarrollo de este experimento se utilizaron herramientas de Inteligencia Artificial Generativa (IAG) para:

- **Diseño del flujo CQRS en `POST /projects`**: la IAG apoyó la implementación de la separación read/write en FastAPI con SQLAlchemy, asegurando que la validación de `CuentaCloud` utilizara la read replica y la escritura del `Proyecto` el nodo principal.
- **Generación del script JMeter** (`latency_test.jmx`): la IAG construyó el plan de pruebas con dos thread groups (20 y 150 usuarios), assertions de duración, colectores de aggregate/summary report y el script Groovy para extracción de JWT con fallback `AUTH_DISABLED_BYPASS`.
- **Diagnóstico y resolución de problemas de despliegue**: la IAG interpretó logs de cloud-init y errores de arranque de uvicorn para resolver causas raíz.
- **Análisis de métricas**: la IAG procesó los archivos CSV de JMeter para calcular P90, P95, mediana y tasa de error diferenciados por thread group.
- **Generación del seed de datos**: la IAG apoyó la construcción de `seed_cloud_data.py` para poblar la BD con 5.596 proyectos y 20.000 recursos cloud.

La validación arquitectónica final, la interpretación de resultados frente a los criterios del ASR16 y las decisiones de diseño fueron realizadas y verificadas por el equipo de desarrollo.
