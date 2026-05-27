# Implementación del Experimento — ASR19 Seguridad
## Protección de latencia frente a tráfico abusivo

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

**Seguridad:**

---

**Link repo:** https://github.com/dcantorni/Arquisoft-sprint2-Leopartech

---

**Instancias AWS:**

- **manejador_cloud** — EC2 Auto Scaling Group `bite2-asg-cloud` | IP pública: `44.213.125.28` | FastAPI + Rate Limiter (puerto 8002)
- **manejador_autenticacion** — EC2 | IP pública: `44.203.147.86` | Auth Service (puerto 8004)
- **manejador_seguridad** — EC2 | IP pública: `32.197.226.122` | Security Service (puerto 8005)
- **Redis** — EC2 privado | IP: `172.31.87.254` | Rate limiting + Cache (puerto 6379)
- **PostgreSQL RDS** — `bite2-postgres.ctbzntbwi40b.us-east-1.rds.amazonaws.com` | Base de datos principal (puerto 5432)
- **Read Replica RDS** — `bite2-cloud-read-replica.ctbzntbwi40b.us-east-1.rds.amazonaws.com` | Lectura CQRS (puerto 5432)

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

**Configuración del Rate Limiter:**

- **Archivo:** `manejador_cloud/middleware/rate_limit.py`
- **Algoritmo:** Redis sliding-window counter (INCR + EXPIRE condicional en count == 1)
- **RATE_LIMIT_ENABLED:** true
- **RATE_LIMIT_REQUESTS:** 20 solicitudes por ventana
- **RATE_LIMIT_WINDOW:** 10 segundos por IP de cliente
- **Endpoint protegido:** POST /projects
- **IP del cliente:** Header `X-Forwarded-For` inyectado por el ALB
- **Respuesta bloqueada:** HTTP 429 + header `Retry-After: 10`

---

**Pruebas de resultados — Phase 2 (150 usuarios, rate limiting habilitado)**
=========

Herramienta: Apache JMeter 5.6.3

Configuración:

- **Usuarios concurrentes:** 150
- **Ramp-up:** 15 segundos
- **Loops por thread:** 5
- **Total solicitudes:** 751
- **Endpoint:** POST `https://bite2-alb-409016959.us-east-1.elb.amazonaws.com/projects`

Resultados:

- **Total solicitudes:** 751
- **Throughput:** 42.9 req/s
- **Duración:** ~17 segundos
- **Solicitudes bloqueadas (HTTP 429):** 711
- **Solicitudes permitidas (HTTP 201):** 40
- **Porcentaje bloqueado:** **94.67 %** ✅ (target: ≥ 90 %)
- **Tiempo promedio general:** 124 ms
- **Tiempo mínimo:** 74 ms
- **Tiempo máximo:** 695 ms
- **P95 global (201 + 429):** 267 ms

Desglose HTTP 201 (solicitudes legítimas permitidas):

- **Count:** 40 (5.33 %)
- **Avg:** 183 ms
- **P95:** 340 ms ✅ (target: ≤ 500 ms)
- **Max:** 403 ms

Desglose HTTP 429 (solicitudes abusivas bloqueadas):

- **Count:** 711 (94.67 %)
- **Avg:** 120 ms
- **P95:** 265 ms
- **Max:** 305 ms

---

**Pruebas de resultados — Verificación funcional (smoke test previo)**
=========

25 solicitudes secuenciales directamente sobre el EC2:

```
Request  1: 201  ← ventana nueva, permitida
Request  2: 201
...
Request 20: 201  ← último permitido (límite = 20)
Request 21: 429  ← rate limiter activo
Request 22: 429
...
Request 25: 429
```

Confirma que el rate limiter se activa exactamente en el request 21 y que la ventana de 10 segundos expira correctamente antes de un nuevo ciclo.

---

## Análisis de resultados

**Cumplimiento de los criterios del ASR19:**

- **P95 solicitudes legítimas (HTTP 201):** 340 ms ✅ (target: ≤ 500 ms)
- **Porcentaje de tráfico abusivo bloqueado:** 94.67 % ✅ (target: ≥ 90 %)
- **Errores de red o caídas de servicio:** 0 ✅
- **Solicitudes legítimas con HTTP 201:** 100 % de las no bloqueadas ✅

**Impacto sobre la base de datos:**

Las 711 solicitudes bloqueadas retornan HTTP 429 directamente desde el middleware Redis, antes de que cualquier lógica de negocio o consulta a PostgreSQL sea ejecutada. El 94.67 % del tráfico abusivo fue absorbido por Redis sin tocar la base de datos, cumpliendo el objetivo de validación temprana del ASR19.

**Latencia de solicitudes bloqueadas:**

Las respuestas 429 tienen un promedio de 120 ms y P95 de 265 ms. Este tiempo corresponde al overhead TLS (ALB → instancia) más la consulta atómica Redis INCR. Al no involucrar PostgreSQL ni lógica compleja, el rate limiter rechaza tráfico abusivo con mínima sobrecarga.

**Latencia de solicitudes legítimas:**

Las 40 solicitudes que superaron el rate limit respondieron con promedio de 183 ms y P95 de 340 ms — bien por debajo del umbral de 500 ms del ASR19. El mecanismo de protección no degrada la experiencia del usuario legítimo.

**Estabilidad del sistema:**

Durante los 17 segundos de carga con 150 threads concurrentes el ALB distribuyó correctamente las solicitudes, el target group mantuvo estado `healthy`, no se registraron errores 5xx ni timeouts de red, y el throughput se mantuvo estable en ~43 req/s.

**Conclusiones:**

1. La táctica de rate limiting con Redis bloqueó el **94.67 %** del tráfico abusivo, superando el umbral del 90 % definido en el ASR19.
2. La validación temprana en el middleware garantiza que las solicitudes bloqueadas nunca lleguen a PostgreSQL, protegiendo la base de datos de escrituras innecesarias.
3. Las solicitudes legítimas mantienen un P95 de **340 ms**, cumpliendo el SLA de 500 ms.
4. La arquitectura CQRS + Database per Service + Redis Cache demostró resistencia ante tráfico abusivo concurrente sin degradación del servicio principal.
5. No se presentaron caídas de servicio, timeouts críticos ni errores 5xx durante el experimento.

---

## Video de demostración

- https://youtu.be/s8Yn1drkLNE
- https://youtu.be/D_Y2EreroIE

---

## Uso de IAG

Durante el desarrollo de este experimento se utilizaron herramientas de Inteligencia Artificial Generativa (IAG) para:

- **Diseño e implementación del middleware `RateLimitMiddleware`:** la IAG apoyó la construcción del middleware Redis sliding-window en FastAPI, incluyendo la corrección de un bug crítico donde `EXPIRE` se reiniciaba en cada request (reemplazado por `EXPIRE` condicional solo cuando `count == 1`).
- **Construcción y ajuste del script JMeter** (`security_test.jmx`): la IAG generó la estructura del plan de pruebas con las dos fases, los grupos de threads, los colectores de resultados y el script Groovy para extracción de JWT con fallback a `AUTH_DISABLED_BYPASS`.
- **Diagnóstico de errores en tiempo real:** la IAG interpretó los logs de cloud-init, los errores de inicio de uvicorn y los resultados de JMeter para identificar causas raíz.
- **Análisis de métricas:** la IAG calculó los percentiles P95 diferenciados por código de respuesta (201 vs 429) y generó las tablas comparativas del informe.

La validación arquitectónica final, la selección de parámetros del rate limiter y el análisis de los resultados frente a los criterios del ASR19 fueron realizados y verificados por el equipo de desarrollo.
