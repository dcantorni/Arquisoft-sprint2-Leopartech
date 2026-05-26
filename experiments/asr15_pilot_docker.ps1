# ASR15 pilot test against local Docker stack
# Usage: powershell -File experiments/asr15_pilot_docker.ps1 [-Slow]

param([switch]$Slow)

$ErrorActionPreference = "Stop"
$BaseReportes = "http://localhost:8003"
$BaseWorker   = "http://localhost:8006"
$BaseRabbit   = "http://localhost:15672"
$PayloadDir   = Join-Path $PSScriptRoot "data"

function Write-Result($name, $ok, $detail = "") {
    $icon = if ($ok) { "PASS" } else { "FAIL" }
    Write-Host "[$icon] $name $(if ($detail) { "- $detail" })"
}

Write-Host "`n=== ASR15 Docker Pilot ===`n"

# 1. Health checks
foreach ($pair in @(
    @{ N = "manejador_reportes"; U = "$BaseReportes/health" },
    @{ N = "worker_golang";      U = "$BaseWorker/health" }
)) {
    try {
        $r = Invoke-RestMethod -Uri $pair.U -TimeoutSec 10
        Write-Result $pair.N ($r.status -match "healthy") ($r | ConvertTo-Json -Compress)
    } catch {
        Write-Result $pair.N $false $_.Exception.Message
    }
}

# 2. RabbitMQ
try {
    $pair = "bite:bite_pass"
    $bytes = [Text.Encoding]::ASCII.GetBytes($pair)
    $b64 = [Convert]::ToBase64String($bytes)
    $h = @{ Authorization = "Basic $b64" }
    $q = Invoke-RestMethod -Uri "$BaseRabbit/api/queues/bite_vhost/bite.eventos" -Headers $h -TimeoutSec 10
    Write-Result "RabbitMQ bite.eventos" ($q.consumers -ge 1) "consumers=$($q.consumers) messages=$($q.messages)"
} catch {
    Write-Result "RabbitMQ" $false $_.Exception.Message
}

# 3. JWT (local fallback if auth down)
$token = $null
try {
    $login = Invoke-RestMethod -Uri "http://localhost:8004/auth/login" -Method POST `
        -ContentType "application/json" `
        -Body '{"email":"empresa_a@bite.co","password":"BiteCo2024!"}' -TimeoutSec 15
    $token = $login.access_token
    Write-Result "auth login" $true "token obtained"
} catch {
    $token = docker exec arquisoft-sprint2-leopartech-manejador_reportes-1 python -c @"
import jwt,time
print(jwt.encode({'type':'access','empresa_id':'550e8400-e29b-41d4-a716-446655440001','exp':time.time()+3600}, 'local-dev-jwt-secret-change-in-production', algorithm='HS256'))
"@ 2>$null
    $token = $token.Trim()
    Write-Result "auth login" $false "using local JWT fallback"
}

# 4. Enable slow sim if requested
if ($Slow) {
    docker compose -f (Join-Path (Split-Path $PSScriptRoot -Parent) "docker-compose.yml") `
        up -d reportes_worker --force-recreate --no-deps `
        -e SIMULATE_SLOW_PROCESSING=true 2>$null
    # compose v2 doesn't support -e on up; set via env file workaround skipped — use compose override
    Write-Host "[INFO] For slow test ensure SIMULATE_SLOW_PROCESSING=true on reportes_worker"
}

# 5. POST /events/batch (curl — reliable on Windows)
$eventoId = [guid]::NewGuid().ToString()
$payloadFile = if ($Slow) { "e2e_slow_event.json" } else { "e2e_single_event.json" }
$json = Get-Content (Join-Path $PayloadDir $payloadFile) -Raw
$json = $json -replace 'e2e-test-(normal|slow)-001', $eventoId
$tmp = Join-Path $env:TEMP "asr15_batch.json"
Set-Content -Path $tmp -Value $json -NoNewline

$sw = [Diagnostics.Stopwatch]::StartNew()
$curlOut = curl.exe -s -w "`nHTTP:%{http_code}" -X POST "$BaseReportes/events/batch" `
    -H "Authorization: Bearer $token" -H "Content-Type: application/json" --data-binary "@$tmp"
$sw.Stop()
$ok = $curlOut -match "HTTP:202"
Write-Result "POST /events/batch" $ok "total=$([math]::Round($sw.Elapsed.TotalMilliseconds))ms $curlOut"
if (-not $ok) { exit 1 }

# 6. Wait for worker
Start-Sleep -Seconds $(if ($Slow) { 8 } else { 4 })

# 7. Verify PostgreSQL
$dbOut = docker exec arquisoft-sprint2-leopartech-postgres_reportes-1 psql -U postgres -d reportes_db -t -A -c "
SELECT 'evento:' || evento_id || ':procesado=' || procesado::text
FROM eventos_entrantes WHERE evento_id = '$eventoId';
SELECT 'duracion_ms:' || COALESCE(duracion_ms::text,'null')
FROM ejecuciones_analisis ORDER BY iniciado_en DESC LIMIT 1;
SELECT 'notif_count:' || COUNT(*)::text FROM notificaciones;
" 2>$null
Write-Host "`nDB evidence:"
$dbOut | ForEach-Object { Write-Host "  $_" }

$hasEvento = $dbOut -match "evento:$([regex]::Escape($eventoId)):procesado=true"
Write-Result "PostgreSQL evento procesado" $hasEvento

if ($Slow) {
    $hasNotif = ($dbOut | Select-String "notif_count:").Line -match "notif_count:[1-9]"
    $slowMs = ($dbOut | Select-String "duracion_ms:").Line -match "duracion_ms:(\d+)" 
    Write-Result "slow path duracion > 2000ms" ($dbOut -match "duracion_ms:([3-9]\d{3}|\d{5,})")
    Write-Result "notificacion creada" $hasNotif
}

Write-Host "`n=== Pilot complete ===`n"
