"""
manejador_cloud — FastAPI service (CHANGE 2 rewrite from Django).
Port: 8002

Routing:
  /health                          → routers/health.py   (no auth)
  /cloud/cloud-accounts/**         → routers/cuentas.py  (auth required)
  /cloud/resources/**              → routers/recursos.py (auth required)
  /cloud/metrics/**                → routers/metricas.py (auth required)

All reads use the async read-replica session (get_read_db).
All writes use the async primary session (get_write_db).
Redis cache (DB 1) provides 30 s dashboard TTL (cache.py).
"""
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import health, cuentas, recursos, metricas

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

app = FastAPI(
    title="manejador_cloud",
    version="2.0.0",
    description="BITE.co Cloud Resource & Cost Management Service (FastAPI)",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(cuentas.router)
app.include_router(recursos.router)
app.include_router(metricas.router)
