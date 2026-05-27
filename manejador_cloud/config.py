import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database
    DATABASE_HOST: str = "localhost"
    DATABASE_READ_HOST: str = "localhost"
    DATABASE_PORT: str = "5432"
    DATABASE_NAME: str = "cloud_db"
    DATABASE_USER: str = "cloud_user"
    DATABASE_PASSWORD: str = "Cloud_2024!"

    # Redis Cache
    REDIS_URL: str = "redis://redis:6379/0"
    CUENTA_CLOUD_CACHE_TTL: int = 300
    RECURSO_CACHE_TTL: int = 600

    # Auth Validation Service
    AUTH_SERVICE_URL: str = "http://manejador-autenticacion:8004"
    AUTH_SERVICE_TIMEOUT: int = 2
    LOCAL_JWT_SECRET: str = "local-dev-jwt-secret-change-in-production"

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
