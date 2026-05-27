# Guía de configuración EC2 y comandos para ejecutar los experimentos

## Información de acceso

- **IP pública manejador_cloud:** `44.213.125.28`
- **ALB DNS:** `bite2-alb-409016959.us-east-1.elb.amazonaws.com`
- **Redis (privado):** `172.31.87.254:6379`
- **Clave SSH:** `<tu-key.pem>` (reemplazar con el nombre real del archivo)

```bash
ssh -i <tu-key.pem> ubuntu@44.213.125.28
```

---

## Experimento ASR16 — Latencia

### Paso 1: Configurar el EC2 (con rate limiting DESHABILITADO)

```bash
# Conectarse al EC2
ssh -i <tu-key.pem> ubuntu@44.213.125.28

# Actualizar código
cd /opt/biteco
sudo git pull origin main

# Detener uvicorn anterior
sudo pkill -f "uvicorn main:app" || true
sleep 2

# Iniciar uvicorn SIN rate limiting
cd /opt/biteco/manejador_cloud
AUTH_DISABLED=true \
AUTH_DISABLED_TENANT=550e8400-e29b-41d4-a716-446655440001 \
RATE_LIMIT_ENABLED=false \
  nohup uvicorn main:app --host 0.0.0.0 --port 8002 --workers 4 \
  > /tmp/manejador_cloud.log 2>&1 &

# Verificar que arrancó correctamente
sleep 4 && curl -s http://localhost:8002/health
```

Resultado esperado del health check:
```json
{"service":"manejador_cloud","status":"healthy","checks":{"database":"ok","redis":"ok"}}
```

### Paso 2: Ejecutar el test JMeter (desde la máquina local)

```bash
# Limpiar resultados anteriores
rm -rf results/asr16_report results/asr16.jtl \
       results/latency_normal_load.csv results/latency_stress_load.csv \
       results/latency_aggregate.csv results/latency_summary_all.csv \
       results/latency_normal_view.jtl

# Ejecutar el test
./apache-jmeter-5.6.3/bin/jmeter -n \
  -t experiments/latency_test.jmx \
  -l results/asr16.jtl \
  -e -o results/asr16_report \
  -JALB_HOST=bite2-alb-409016959.us-east-1.elb.amazonaws.com
```

### Paso 3: Verificar resultados

```bash
# Ver métricas clave del resultado
python3 -c "
import csv, statistics

for fname, label in [
    ('results/latency_normal_load.csv',  'Normal Load – 20 usuarios'),
    ('results/latency_stress_load.csv',  'Stress Load – 150 usuarios'),
]:
    times = []
    with open(fname) as f:
        for row in csv.DictReader(f):
            if 'POST' in row.get('label',''):
                times.append(int(row['elapsed']))
    if not times: continue
    times.sort()
    p95 = times[int(len(times)*0.95)]
    print(f'{label}')
    print(f'  Count: {len(times)}')
    print(f'  Avg:   {int(statistics.mean(times))} ms')
    print(f'  P95:   {p95} ms  (target: <= 500 ms)')
    print()
"
```

### Resultados esperados

- **Normal Load (20 usuarios):** P95 ≤ 500 ms, Avg ≤ 350 ms, Error rate 0 %
- **Stress Load (150 usuarios):** P95 ≤ 500 ms, Avg ≤ 350 ms, Error rate ≤ 1 %

---

## Experimento ASR19 — Seguridad (Phase 2: con rate limiting)

### Paso 1: Configurar el EC2 (con rate limiting HABILITADO)

```bash
# Conectarse al EC2
ssh -i <tu-key.pem> ubuntu@44.213.125.28

# Actualizar código
cd /opt/biteco
sudo git pull origin main

# Limpiar claves de rate limit anteriores en Redis
python3 -c "
import redis
r = redis.Redis(host='172.31.87.254', port=6379, db=1)
keys = r.keys('rl:*')
if keys:
    r.delete(*keys)
    print(f'Deleted {len(keys)} rate limit keys')
else:
    print('No rate limit keys to delete')
"

# Detener uvicorn anterior
sudo pkill -f "uvicorn main:app" || true
sleep 2

# Iniciar uvicorn CON rate limiting
cd /opt/biteco/manejador_cloud
AUTH_DISABLED=true \
AUTH_DISABLED_TENANT=550e8400-e29b-41d4-a716-446655440001 \
RATE_LIMIT_ENABLED=true \
RATE_LIMIT_REQUESTS=20 \
RATE_LIMIT_WINDOW=10 \
  nohup uvicorn main:app --host 0.0.0.0 --port 8002 --workers 4 \
  > /tmp/manejador_cloud.log 2>&1 &

# Verificar que arrancó correctamente
sleep 4 && curl -s http://localhost:8002/health
```

### Paso 2: Verificar que el rate limiter funciona (smoke test)

```bash
for i in $(seq 1 25); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST http://localhost:8002/projects \
    -H "Content-Type: application/json" \
    -d '{"nombre":"Test","empresa_id":"550e8400-e29b-41d4-a716-446655440001","cuentas_cloud":["550e8400-e29b-41d4-a716-446655440011"]}')
  echo "Request $i: $CODE"
done
```

Resultado esperado: requests 1-20 → `201`, requests 21-25 → `429`

### Paso 3: Limpiar claves Redis antes del test JMeter

```bash
python3 -c "
import redis
r = redis.Redis(host='172.31.87.254', port=6379, db=1)
keys = r.keys('rl:*')
if keys:
    r.delete(*keys)
    print(f'Deleted {len(keys)} rate limit keys — ready for JMeter')
else:
    print('No stale keys — ready for JMeter')
"
```

### Paso 4: Ejecutar el test JMeter — Phase 2 (desde la máquina local)

> Asegurarse de que en `experiments/security_test.jmx` el Thread Group 1 esté `enabled="false"` y el Thread Group 2 esté `enabled="true"`.

```bash
# Limpiar resultados anteriores
rm -rf results/asr19_protected_report results/asr19_protected.jtl \
       results/asr19_phase2_codes.csv results/asr19_phase2_aggregate.csv \
       results/asr19_phase2_summary.csv

# Ejecutar el test
./apache-jmeter-5.6.3/bin/jmeter -n \
  -t experiments/security_test.jmx \
  -l results/asr19_protected.jtl \
  -e -o results/asr19_protected_report \
  -JALB_HOST=bite2-alb-409016959.us-east-1.elb.amazonaws.com
```

### Paso 5: Contar solicitudes bloqueadas vs permitidas

```bash
echo "Permitidas (201):"
grep ",201," results/asr19_phase2_codes.csv | wc -l

echo "Bloqueadas (429):"
grep ",429," results/asr19_phase2_codes.csv | wc -l

# Calcular porcentaje bloqueado
python3 -c "
allowed = sum(1 for line in open('results/asr19_phase2_codes.csv') if ',201,' in line)
blocked = sum(1 for line in open('results/asr19_phase2_codes.csv') if ',429,' in line)
total = allowed + blocked
print(f'Permitidas (201): {allowed}')
print(f'Bloqueadas (429): {blocked}')
print(f'Total:            {total}')
print(f'% bloqueado:      {blocked/total*100:.2f}%  (target: >= 90%)')
"
```

### Resultados esperados

- **% bloqueado:** ≥ 90 % (resultado real: 94.67 %)
- **P95 solicitudes legítimas (201):** ≤ 500 ms (resultado real: 340 ms)
- **Errores de red:** 0

---

## Cambiar entre experimentos

### De ASR19 → ASR16 (desactivar rate limiting)

```bash
# En el EC2:
sudo pkill -f "uvicorn main:app" || true; sleep 2
cd /opt/biteco/manejador_cloud
AUTH_DISABLED=true AUTH_DISABLED_TENANT=550e8400-e29b-41d4-a716-446655440001 \
RATE_LIMIT_ENABLED=false \
  nohup uvicorn main:app --host 0.0.0.0 --port 8002 --workers 4 \
  > /tmp/manejador_cloud.log 2>&1 &
sleep 4 && curl -s http://localhost:8002/health
```

### De ASR16 → ASR19 (activar rate limiting)

```bash
# En el EC2:
sudo pkill -f "uvicorn main:app" || true; sleep 2

# Limpiar claves Redis
python3 -c "import redis; r=redis.Redis(host='172.31.87.254',port=6379,db=1); keys=r.keys('rl:*'); r.delete(*keys) if keys else None; print(f'Cleared {len(keys)} keys')"

cd /opt/biteco/manejador_cloud
AUTH_DISABLED=true AUTH_DISABLED_TENANT=550e8400-e29b-41d4-a716-446655440001 \
RATE_LIMIT_ENABLED=true RATE_LIMIT_REQUESTS=20 RATE_LIMIT_WINDOW=10 \
  nohup uvicorn main:app --host 0.0.0.0 --port 8002 --workers 4 \
  > /tmp/manejador_cloud.log 2>&1 &
sleep 4 && curl -s http://localhost:8002/health
```

---

## Comandos de diagnóstico útiles

```bash
# Ver log del servidor en tiempo real
tail -f /tmp/manejador_cloud.log

# Verificar que uvicorn está corriendo
pgrep -a -f uvicorn

# Ver claves activas de rate limiting en Redis
python3 -c "
import redis
r = redis.Redis(host='172.31.87.254', port=6379, db=1)
keys = r.keys('rl:*')
for k in keys:
    print(k, '→', r.get(k), '| TTL:', r.ttl(k), 's')
"

# Health check del servicio
curl -s http://localhost:8002/health | python3 -m json.tool

# Test rápido POST /projects
curl -s -X POST http://localhost:8002/projects \
  -H "Content-Type: application/json" \
  -d '{"nombre":"Test","empresa_id":"550e8400-e29b-41d4-a716-446655440001","cuentas_cloud":["550e8400-e29b-41d4-a716-446655440011"]}' \
  | python3 -m json.tool
```
