# Implementación del Experimento — ASR19 Seguridad
## Protección de latencia frente a tráfico abusivo

---

## 1. Resultados Obtenidos

### 1.1 Evidencias

#### Repositorio
- **URL:** https://github.com/dcantorni/Arquisoft-sprint2-Leopartech
- **Rama:** `main`
- **Commit del experimento:** `44572ce` — *Fix rate limiter: set EXPIRE only on first request per window*

#### Infraestructura AWS desplegada

| Componente | Valor |
|---|---|
| Application Load Balancer | `bite2-alb-409016959.us-east-1.elb.amazonaws.com` |
| manejador_cloud EC2 (IP pública) | `44.213.125.28` |
| manejador_cloud ASG | `bite2-asg-cloud` (min 1 / max 4 instancias) |
| Redis (privado) | `172.31.87.254:6379` |
| PostgreSQL RDS | `bite2-postgres.ctbzntbwi40b.us-east-1.rds.amazonaws.com` |
| Read Replica RDS | `bite2-cloud-read-replica.ctbzntbwi40b.us-east-1.rds.amazonaws.com` |
| Target Group Cloud | `bite2-tg-cloud` (puerto 8002, health check `/health`) |

#### Configuración del Rate Limiter

El mecanismo de protección implementado es un **middleware Redis sliding-window** en `manejador_cloud/middleware/rate_limit.py`:

```
RATE_LIMIT_ENABLED  = true
RATE_LIMIT_REQUESTS = 20        # máximo 20 solicitudes por ventana
RATE_LIMIT_WINDOW   = 10        # ventana de 10 segundos por IP
Endpoint protegido  = POST /projects
Clave Redis         = rl:{client_ip}:POST:/projects
Respuesta bloqueada = HTTP 429 + header Retry-After: 10
```

El middleware verifica el header `X-Forwarded-For` (inyectado por el ALB) para obtener la IP real del cliente.

#### Verificación funcional previa al experimento

Prueba de humo ejecutada directamente sobre el EC2 (`http://localhost:8002/projects`):

```
Request  1: 201   ← ventana nueva, solicitud permitida
Request  2: 201
...
Request 20: 201   ← último request permitido en la ventana (límite = 20)
Request 21: 429   ← tráfico bloqueado por rate limiter
Request 22: 429
...
Request 25: 429
```

#### Configuración JMeter — Phase 2 (ataque con rate limiting habilitado)

| Parámetro | Valor |
|---|---|
| Herramienta | Apache JMeter 5.6.3 |
| Thread Group | Phase 2 – Attack 150 Users |
| Threads (usuarios concurrentes) | 150 |
| Ramp-up | 15 segundos |
| Loops por thread | 5 |
| Total solicitudes esperadas | 750 |
| Endpoint | `POST /projects` vía HTTPS ALB |
| Payload | Proyecto con `empresa_id` fijo + 2 `cuentas_cloud` seeded |
| Timeout de conexión | 10 000 ms |
| Timeout de respuesta | 10 000 ms |

---

### 1.2 Datos Obtenidos

#### Resultados globales (Phase 2 — con rate limiting habilitado)

| Métrica | Valor |
|---|---|
| Total de solicitudes | 751 |
| Throughput | 42.9 req/s |
| Duración total | ~17 segundos |
| Solicitudes permitidas (HTTP 201) | **40** |
| Solicitudes bloqueadas (HTTP 429) | **711** |
| **Porcentaje bloqueado** | **94.67 %** |
| Tiempo promedio general | 124 ms |
| Tiempo mínimo | 74 ms |
| Tiempo máximo | 695 ms |
| P95 global (201 + 429) | 267 ms |

#### Desglose por código de respuesta

| Código | Significado | Count | % | Avg (ms) | P95 (ms) | Max (ms) |
|---|---|---|---|---|---|---|
| **201** Created | Solicitud legítima procesada | 40 | 5.33 % | 183 | 340 | 403 |
| **429** Too Many Requests | Solicitud bloqueada por rate limiter | 711 | 94.67 % | 120 | 265 | 305 |

---

## 2. Análisis de Resultados

### 2.1 Cumplimiento de los criterios del ASR19

| Criterio | Valor esperado | Valor obtenido | Cumple |
|---|---|---|---|
| P95 solicitudes legítimas (HTTP 201) | ≤ 500 ms | **340 ms** | ✅ |
| Porcentaje de tráfico abusivo bloqueado | ≥ 90 % | **94.67 %** | ✅ |
| Sistema sin caídas ni timeouts críticos | 0 errores de conexión | 0 errores de red | ✅ |
| Solicitudes legítimas respondidas con HTTP 201 | 100 % de las no bloqueadas | 100 % | ✅ |

### 2.2 Análisis arquitectónico

**Redis como mecanismo de rate limiting**

El rate limiter utiliza Redis con el patrón INCR + EXPIRE. El TTL se fija únicamente en la primera solicitud de cada ventana (cuando el contador pasa de 0 a 1), garantizando que la ventana expire naturalmente en 10 segundos sin reiniciarse ante tráfico continuo. Esto resuelve el problema de la "ventana perpetua" donde `EXPIRE` se reiniciaba en cada request bajo alta concurrencia.

**Impacto sobre la base de datos**

Las 711 solicitudes bloqueadas retornan HTTP 429 directamente desde el middleware, antes de que cualquier lógica de negocio o consulta a PostgreSQL sea ejecutada. Esto significa que el 94.67 % del tráfico abusivo fue absorbido por Redis sin tocar la base de datos, cumpliendo el objetivo de validación temprana descrito en el ASR19.

**Latencia de solicitudes bloqueadas**

Las respuestas 429 tienen un promedio de **120 ms** y un P95 de **265 ms**. Este tiempo corresponde principalmente al overhead de TLS (ALB → instancia) y a la consulta atómica a Redis. Al no involucrar PostgreSQL ni lógica de aplicación compleja, el rate limiter añade latencia mínima.

**Latencia de solicitudes legítimas**

Las 40 solicitudes que superaron el check de rate limiting y llegaron al handler de FastAPI respondieron con un promedio de **183 ms** y P95 de **340 ms** — bien por debajo del umbral de 500 ms del ASR19.

**Separación CQRS protegida**

Al bloquear el tráfico abusivo antes de la capa de datos, la réplica de lectura (CQRS read path) utilizada para validar `CuentaCloud` y el nodo de escritura principal mantuvieron carga estable durante el experimento. No se registraron errores de conexión a la base de datos.

**Estabilidad del sistema**

Durante los 17 segundos de carga con 150 threads concurrentes:
- El ALB distribuyó correctamente las solicitudes al único nodo activo del ASG.
- El target group mantuvo estado `healthy` (health checks respondiendo HTTP 200).
- No se registraron timeouts de red ni errores de tipo 5xx.
- El throughput se mantuvo estable en ~43 req/s.

### 2.3 Comparativa esperada Phase 1 vs Phase 2

| Métrica | Phase 1 (sin rate limit) | Phase 2 (con rate limit) | Δ |
|---|---|---|---|
| Solicitudes exitosas (201) | ~200 (100 %) | 40 (5.3 %) | -92 % |
| Solicitudes bloqueadas (429) | 0 | 711 | +711 |
| P95 | ~500 ms esperado | 267 ms (global) | Mejor |
| Carga sobre PostgreSQL | Alta | Baja (bloqueada en Redis) | ↓ 94.67 % |

### 2.4 Conclusiones

1. La táctica de **rate limiting con Redis** demostró ser altamente efectiva: bloquea el 94.67 % del tráfico abusivo, superando el umbral mínimo del 90 % definido en el ASR19.
2. La **validación temprana** en el middleware garantiza que las solicitudes bloqueadas nunca lleguen a PostgreSQL, protegiendo la integridad de la base de datos bajo ataques de abuso de recursos.
3. Las **solicitudes legítimas** (aquellas dentro del límite de frecuencia) mantienen un P95 de 340 ms, cumpliendo el SLA de 500 ms del ASR19.
4. La arquitectura **CQRS + Database per Service + Redis Cache** demostró resistencia ante tráfico concurrente abusivo sin degradación observable del servicio principal.
5. El sistema mantuvo **estabilidad operativa completa** durante el experimento: sin caídas, sin errores 5xx y con health checks respondiendo normalmente durante toda la prueba.

---

## 3. Video de Demostración

> *(Enlace al video de demostración del experimento — agregar URL aquí)*

---

## 4. Uso de IAG

Durante el desarrollo de este experimento se utilizaron herramientas de Inteligencia Artificial Generativa (IAG) para:

- **Diseño e implementación del middleware `RateLimitMiddleware`**: la IAG apoyó la construcción del middleware Redis sliding-window en FastAPI, incluyendo la corrección de un bug crítico donde `EXPIRE` se reiniciaba en cada request (reemplazado por `EXPIRE` condicional solo cuando `count == 1`).
- **Construcción y ajuste del script JMeter** (`security_test.jmx`): la IAG generó la estructura del plan de pruebas con las dos fases, los grupos de threads, los colectores de resultados y el script Groovy para extracción de JWT con fallback a `AUTH_DISABLED_BYPASS`.
- **Diagnóstico de errores en tiempo real**: la IAG interpretó los logs de `cloud-init`, los errores de inicio de uvicorn y los resultados de JMeter para identificar causas raíz (bug EXPIRE, /etc/environment con espacios, nohup sin permisos de log, etc.).
- **Análisis de métricas**: la IAG calculó los percentiles P95 diferenciados por código de respuesta (201 vs 429) y generó las tablas comparativas del informe.

La validación arquitectónica final, la selección de parámetros del rate limiter (20 req/10 s) y el análisis de los resultados frente a los criterios del ASR19 fueron realizados y verificados por el equipo de desarrollo.
