from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

# Ensure absolute imports since this is the root module
from routers import cloud_accounts, resources, metrics, health, projects
from middleware.rate_limit import RateLimitMiddleware
from cache import redis_client

# Basic logging config
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="Manejador Cloud API")

# CORSMiddleware is outermost — CORS headers appear on ALL responses (incl. 429)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# RateLimitMiddleware — Redis sliding-window counter, protects POST /projects (ASR19)
# Controlled by RATE_LIMIT_ENABLED env var (default: true).
# Phase 1 (no protection): restart uvicorn with RATE_LIMIT_ENABLED=false
# Phase 2 (with protection): restart uvicorn with RATE_LIMIT_ENABLED=true
app.add_middleware(RateLimitMiddleware, redis_client=redis_client)

app.include_router(projects.router)        # POST/GET /projects  (ASR16 — no HTTP hop)
app.include_router(cloud_accounts.router)  # /cloud-accounts
app.include_router(resources.router)
app.include_router(metrics.router)
app.include_router(health.router)
