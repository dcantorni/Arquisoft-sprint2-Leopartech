from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

# Ensure absolute imports since this is the root module
from routers import cloud_accounts, resources, metrics, health, projects

# Basic logging config
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="Manejador Cloud API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)        # POST/GET /projects  (ASR16 — no HTTP hop)
app.include_router(cloud_accounts.router)  # /cloud-accounts
app.include_router(resources.router)
app.include_router(metrics.router)
app.include_router(health.router)
