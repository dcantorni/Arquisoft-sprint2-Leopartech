import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
DEBUG = os.environ.get('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',')

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'rest_framework',
    'projects',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'projects.middleware.TenantAuthMiddleware',
]

ROOT_URLCONF = 'manejador_usuarios.urls'
WSGI_APPLICATION = 'manejador_usuarios.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': os.environ.get('DATABASE_HOST', 'localhost'),
        'PORT': os.environ.get('DATABASE_PORT', '5432'),
        'NAME': os.environ.get('DATABASE_NAME', 'usuarios_db'),
        'USER': os.environ.get('DATABASE_USER', 'admin'),
        'PASSWORD': os.environ.get('DATABASE_PASSWORD', 'admin123'),
        'CONN_MAX_AGE': 60,
        'OPTIONS': {'connect_timeout': 10},
    }
}

# CHANGE 4: Redis removed from manejador_usuarios. Cache replaced with direct
# indexed DB queries (Empresa.id, CuentaCloud validated via Resource Service HTTP).

# Email backend (terraform injects this; default is console for local dev)
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend',
)

# RabbitMQ
RABBITMQ_URL = os.environ.get('RABBITMQ_URL', 'amqp://bite:bite_pass@rabbitmq:5672/bite_vhost')
RABBITMQ_EXCHANGE = os.environ.get('RABBITMQ_EXCHANGE', 'bite_events')
RABBITMQ_ROUTING_KEY_PROJECT = os.environ.get('RABBITMQ_ROUTING_KEY_PROJECT', 'proyecto.creado')

# Inter-service: calls manejador_cloud for CuentaCloud validation
RESOURCE_SERVICE_URL = os.environ.get('RESOURCE_SERVICE_URL', 'http://manejador_cloud:8002')

# Auth + Security services (ASR2/ASR3)
AUTH_SERVICE_URL = os.environ.get('AUTH_SERVICE_URL', 'http://manejador-autenticacion:8004')
AUTH_SERVICE_TIMEOUT = int(os.environ.get('AUTH_SERVICE_TIMEOUT', '2'))
SEGURIDAD_URL = os.environ.get('SEGURIDAD_URL', 'http://manejador-seguridad:8005')
LOCAL_JWT_SECRET = os.environ.get('LOCAL_JWT_SECRET', 'local-dev-jwt-secret-change-in-production')
RESOURCE_SERVICE_TIMEOUT = int(os.environ.get('RESOURCE_SERVICE_TIMEOUT', '2'))


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
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'DEFAULT_PARSER_CLASSES': ['rest_framework.parsers.JSONParser'],
    'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.AnonRateThrottle'],
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.environ.get('API_THROTTLE_ANON', '1000/min'),
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'UTC'
LANGUAGE_CODE = 'en-us'
