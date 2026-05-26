# Explicación del archivo main.tf — BITE.co Cloud Cost Platform

## Visión general

Este archivo Terraform despliega toda la infraestructura de BITE.co en AWS. No usa Docker en ningún punto: cada servicio corre como un proceso Django (`python3 manage.py runserver`) directamente en una instancia EC2 con Ubuntu 22.04. El código se clona desde GitHub en cada instancia al arrancar.

---

## 1. Variables configurables

| Variable | Valor por defecto | Para qué sirve |
|---|---|---|
| `region` | `us-east-1` | Región AWS donde se despliega todo |
| `project_prefix` | `bite2` | Prefijo para nombrar todos los recursos |
| `allowed_ssh_cidr` | `0.0.0.0/0` | IPs que pueden conectarse por SSH (¡restringir en producción!) |
| `repository` | GitHub URL | Repositorio que se clona en cada EC2 |
| `branch` | `main` | Rama que se despliega |
| `celery_worker_concurrency` | `4` | Procesos paralelos por worker Celery |
| `instance_type_app` | `t3.small` | Tamaño de EC2 para servicios Django |
| `instance_type_support` | `t3.micro` | Tamaño para Redis y RabbitMQ |
| `instance_type_worker` | `t3.small` | Tamaño para workers Celery |

---

## 2. Providers y datos base

- **Provider `aws`**: apunta a `us-east-1`.
- **Provider `tls`**: genera el certificado autofirmado para HTTPS (experimento ASR2).
- **`data.aws_vpc.default`**: usa la VPC por defecto de la cuenta AWS Academy.
- **`data.aws_subnets.default`**: toma las subnets en `us-east-1a` y `us-east-1b`.
- **`data.aws_ami.ubuntu`**: busca automáticamente la AMI más reciente de Ubuntu 22.04 LTS (Canonical).

---

## 3. Security Groups (Firewall por capas)

Cada tier tiene su propio grupo de seguridad con el mínimo de puertos abiertos:

| Security Group | Puertos de entrada | Quién puede conectarse |
|---|---|---|
| `ssh` | 22 | Todos (CIDR configurado) |
| `alb` | 80, 443 | Internet |
| `app` | 8001 (usuarios), 8002 (cloud), 8003 (reportes) | ALB / VPC interna |
| `db` | 5432 (PostgreSQL) | Solo VPC interna |
| `cache` | 6379 (Redis) | Solo VPC interna |
| `broker` | 5672 (AMQP), 15672 (RabbitMQ UI) | Solo VPC interna |
| `worker` | 22 | Todos (SSH only, sin HTTP) |
| `auth` | 8004 (autenticación), 8005 (seguridad), 22 | ALB + VPC interna + SSH |

---

## 4. Infraestructura compartida

### Redis (`aws_instance.redis`) — t3.micro
- Instalado directamente con `apt-get install redis-server`.
- Configurado para aceptar conexiones de toda la VPC (bind `0.0.0.0`).
- Política de evicción LRU con máximo 256 MB.
- Usado por varios servicios en databases distintas (Redis DB 0, 1, 2, 3).

### RabbitMQ (`aws_instance.rabbitmq`) — t3.micro
- Instalado con `apt-get install rabbitmq-server`.
- Crea un vhost `bite_vhost` y usuario `bite` con contraseña `bite_pass`.
- UI de administración habilitada en puerto 15672 (solo accesible desde VPC).
- Usado como broker de mensajes para Celery (experimento ASR17).

### RDS PostgreSQL (`aws_db_instance.main`) — db.t3.micro
- **Una sola instancia RDS** compartida entre todos los microservicios.
- Cada servicio tiene su propia base de datos y usuario aislado:
  - `usuarios_db` / `usuarios_user`
  - `cloud_db` / `cloud_user`
  - `reportes_db` / `reportes_user`
  - `seguridad_db` / `seguridad_user` (compartida entre autenticación y seguridad)
- No es públicamente accesible (solo desde VPC).
- Las bases de datos se crean en el arranque de cada EC2 usando las credenciales maestras.

---

## 5. Servidores de aplicación (Django)

Todos siguen el mismo patrón de arranque en `user_data`:
1. Setear variables de entorno en `/etc/environment`.
2. Clonar el repositorio en `/opt/biteco`.
3. Esperar (`nc -z`) a que sus dependencias estén listas.
4. Crear su base de datos en RDS.
5. Instalar dependencias Python.
6. Ejecutar migraciones y seed de datos.
7. Lanzar `python3 manage.py runserver` en background.

### `manejador_usuarios` — Puerto 8001
- Depende de: RDS, Redis, RabbitMQ, manejador_cloud, manejador_autenticacion.
- Variables adicionales: `RESOURCE_SERVICE_URL`, `AUTH_SERVICE_URL`.
- Expuesto via ALB en `/projects/*`.

### `manejador_cloud` — Puerto 8002
- Depende de: RDS, Redis.
- **No expuesto por el ALB** — solo accesible internamente desde la VPC.
- Usado por `manejador_usuarios` para datos de recursos cloud.

### `manejador_reportes` — Puerto 8003
- Depende de: RDS, Redis, RabbitMQ.
- Expuesto via ALB en `/events/*` y `/reports/*`.
- También actúa como productor de tareas Celery.

---

## 6. Autenticación y Seguridad — RESPUESTA DIRECTA

> **¿Son una sola EC2 con Docker o dos EC2 separadas?**
>
> Son **DOS instancias EC2 completamente separadas**. No hay Docker. Cada una corre un proceso Django directamente en la máquina.

### `manejador_autenticacion` (líneas 1110–1187) — Puerto 8004
- EC2 propia con `instance_type_app` (t3.small).
- Depende de: RDS y Cognito User Pool.
- Corre en puerto **8004**.
- Tiene acceso directo a Cognito (`COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`).
- Expuesto via ALB en `/auth/*`.
- Comparte `seguridad_db` con `manejador_seguridad` (mismo usuario y base de datos).

### `manejador_seguridad` (líneas 1193–1263) — Puerto 8005
- EC2 propia con `instance_type_app` (t3.small).
- Depende de: RDS y **manejador_autenticacion** (espera a que el puerto 8004 esté disponible).
- Corre en puerto **8005**.
- Llama a `manejador_autenticacion` internamente vía `AUTH_SERVICE_URL`.
- Expuesto via ALB en `/security/*` (incluye los endpoints del experimento ASR2: `/security/tls-status`, `/security/integrity-check`, `/security/integrity-log`).

**Ambas instancias comparten el mismo Security Group `auth`**, que permite entrada desde el ALB (8004) y desde la VPC (8004 y 8005).

---

## 7. Celery Worker Pool

- Definido con `for_each = toset(["a"])` → actualmente **1 instancia**.
- Corre en el directorio de `manejador_reportes` (consume las mismas tareas).
- Se conecta a RabbitMQ como broker y a Redis como backend de resultados.
- Sin ingreso HTTP — solo SSH.
- Diseñado para escalar horizontalmente agregando más letras al `toset`.

---

## 8. Application Load Balancer (ALB)

- **Tipo**: Application Load Balancer (no interno).
- **Puerto 80 (HTTP)**: redirige **todo** el tráfico a HTTPS con HTTP 301 (experimento ASR2).
- **Puerto 443 (HTTPS)**: termina TLS con un certificado autofirmado generado por Terraform.

### Rutas HTTPS configuradas

| Path | Target | Experimento |
|---|---|---|
| `/auth/*` | manejador_autenticacion (8004) | ASR3 |
| `/security/*` | manejador_seguridad (8005) | ASR2 |
| `/projects/*` | manejador_usuarios (8001) | ASR16 |
| `/events/*` | manejador_reportes (8003) | ASR17 |
| `/reports/*` | manejador_reportes (8003) | ASR17 |
| Default (HTTPS) | manejador_seguridad | ASR2 |

---

## 9. Cognito User Pool (ASR3)

- Pool de usuarios con autenticación por email.
- Atributos custom: `empresa_id` (UUID de 36 chars) y `rol` (ADMIN/MANAGER/etc).
- Cliente SPA sin `client_secret` (compatible con apps JavaScript en el browser).
- Soporta: `USER_PASSWORD_AUTH`, `REFRESH_TOKEN_AUTH`, `USER_SRP_AUTH`.

---

## 10. Frontend (S3)

- Bucket S3 con website estático habilitado.
- Archivos: `index.html`, `dashboard.html`, `metrics.html`, `config.js`.
- `config.js` se genera desde una plantilla (`config.js.tpl`) inyectando el DNS del ALB automáticamente.
- Acceso público habilitado (bucket policy que permite `s3:GetObject` a todos).

---

## 11. Outputs (valores que Terraform imprime al finalizar)

Los outputs más útiles:

| Output | Descripción |
|---|---|
| `alb_dns_name` | DNS del ALB — host para JMeter |
| `alb_usuarios_url` | `https://<alb>/projects` — experimento ASR16 |
| `alb_reportes_url` | `https://<alb>/events/batch` — experimento ASR17 |
| `asr2_tls_status_url_https` | `https://<alb>/security/tls-status` — experimento ASR2 |
| `frontend_s3_url` | URL del sitio estático |
| `cognito_user_pool_id` | ID del pool de Cognito |
| `rds_endpoint` | Endpoint RDS PostgreSQL |

---

## Diagrama de componentes

```
Internet
    │
    ▼
[ALB] ── puerto 80 → redirige a 443
    │
    └── puerto 443 (TLS, cert autofirmado)
         ├── /auth/*      → manejador_autenticacion (EC2, :8004)
         ├── /security/*  → manejador_seguridad     (EC2, :8005)
         ├── /projects/*  → manejador_usuarios       (EC2, :8001)
         ├── /events/*    → manejador_reportes        (EC2, :8003)
         └── /reports/*   → manejador_reportes        (EC2, :8003)

                              VPC interna
manejador_usuarios ──────────► manejador_cloud       (EC2, :8002)
manejador_usuarios ──────────► manejador_autenticacion
manejador_seguridad ─────────► manejador_autenticacion

Todos los servicios ──────────► RDS PostgreSQL (db.t3.micro)
                   ──────────► Redis (EC2, :6379)
manejador_reportes ──────────► RabbitMQ (EC2, :5672)
                                    │
                                    ▼
                             worker_pool (EC2 Celery)

[S3] ← frontend estático (HTML/JS) con config.js apuntando al ALB
```
