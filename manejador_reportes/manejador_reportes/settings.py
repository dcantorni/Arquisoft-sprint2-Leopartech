import os
from pathlib import Path

# kombu / Celery removed (PROMPT 3 — replaced by Golang worker pool).

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
DEBUG = os.environ.get('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',')

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'rest_framework',
    'events',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'events.middleware.TenantAuthMiddleware',
]

ROOT_URLCONF = 'manejador_reportes.urls'
WSGI_APPLICATION = 'manejador_reportes.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': os.environ.get('DATABASE_HOST', 'localhost'),
        'PORT': os.environ.get('DATABASE_PORT', '5432'),
        'NAME': os.environ.get('DATABASE_NAME', 'reportes_db'),
        'USER': os.environ.get('DATABASE_USER', 'admin'),
        'PASSWORD': os.environ.get('DATABASE_PASSWORD', 'admin123'),
        'CONN_MAX_AGE': 60,
        'OPTIONS': {'connect_timeout': 10},
    }
}

# ── RabbitMQ (used by tasks.py pika publisher — Golang worker consumes) ───────
# PROMPT 3: Celery removed. The Django service publishes events via pika directly.
# The Golang worker_golang/ consumes from the bite_events exchange.
RABBITMQ_URL = os.environ.get(
    'RABBITMQ_URL',
    'amqp://bite:bite_pass@rabbitmq:5672/bite_vhost',
)
RABBITMQ_EXCHANGE = os.environ.get('RABBITMQ_EXCHANGE', 'bite_events')

# Auth + Security services (ASR2/ASR3)
AUTH_SERVICE_URL = os.environ.get('AUTH_SERVICE_URL', 'http://manejador-autenticacion:8004')
AUTH_SERVICE_TIMEOUT = int(os.environ.get('AUTH_SERVICE_TIMEOUT', '2'))
SEGURIDAD_URL = os.environ.get('SEGURIDAD_URL', 'http://manejador-seguridad:8005')
LOCAL_JWT_SECRET = os.environ.get('LOCAL_JWT_SECRET', 'local-dev-jwt-secret-change-in-production')

# ── Email notifications (architecture.md §2.2 API Email Provider) ──────────
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend',
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.sendgrid.net')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = True
EMAIL_FROM = os.environ.get('EMAIL_FROM', 'notificaciones@bite.co')

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
    'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.AnonRateThrottle'],
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.environ.get('API_THROTTLE_ANON', '5000/min'),
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'UTC'
LANGUAGE_CODE = 'en-us'
