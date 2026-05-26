import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
DEBUG = os.environ.get('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',')

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'corsheaders',
    'rest_framework',
    'autenticacion',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
]

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

ROOT_URLCONF = 'manejador_autenticacion.urls'
WSGI_APPLICATION = 'manejador_autenticacion.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': os.environ.get('DATABASE_HOST', 'localhost'),
        'PORT': os.environ.get('DATABASE_PORT', '5432'),
        'NAME': os.environ.get('DATABASE_NAME', 'seguridad_db'),
        'USER': os.environ.get('DATABASE_USER', 'postgres'),
        'PASSWORD': os.environ.get('DATABASE_PASSWORD', 'postgres'),
        'CONN_MAX_AGE': 60,
        'OPTIONS': {'connect_timeout': 10},
    }
}

# Auth mode: Cognito when pool ID is set, local JWT otherwise
COGNITO_USER_POOL_ID = os.environ.get('COGNITO_USER_POOL_ID', '')
COGNITO_CLIENT_ID = os.environ.get('COGNITO_CLIENT_ID', '')
COGNITO_REGION = os.environ.get('COGNITO_REGION', 'us-east-1')
USE_COGNITO = bool(COGNITO_USER_POOL_ID)

LOCAL_JWT_SECRET = os.environ.get('LOCAL_JWT_SECRET', 'local-dev-jwt-secret-change-in-production')
LOCAL_JWT_ACCESS_EXPIRY = int(os.environ.get('LOCAL_JWT_ACCESS_EXPIRY', '3600'))
LOCAL_JWT_REFRESH_EXPIRY = int(os.environ.get('LOCAL_JWT_REFRESH_EXPIRY', '86400'))

# ── Redis token-validation cache (DB 0) ──────────────────────────────────────
# Cache-aside: GET /auth/validate results are stored for TOKEN_CACHE_TTL seconds.
# Eliminates repeated DB/Cognito round-trips on every cross-service auth check.
# Falls back to direct validation on Redis errors (non-blocking).
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
TOKEN_CACHE_TTL = 300  # 5 minutes — must be ≤ LOCAL_JWT_ACCESS_EXPIRY

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'format': '%(asctime)s %(name)s %(levelname)s %(message)s %(pathname)s %(lineno)d',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': os.environ.get('LOG_LEVEL', 'INFO'),
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': os.environ.get('DJANGO_LOG_LEVEL', 'WARNING'),
            'propagate': False,
        },
    },
}

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'DEFAULT_PARSER_CLASSES': ['rest_framework.parsers.JSONParser'],
    'DEFAULT_AUTHENTICATION_CLASSES': [],
    'DEFAULT_PERMISSION_CLASSES': [],
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'UTC'
LANGUAGE_CODE = 'en-us'
