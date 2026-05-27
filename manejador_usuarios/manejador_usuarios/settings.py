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
    'corsheaders.middleware.CorsMiddleware',      # 1st — CORS
    'projects.rate_limiter.RateLimitMiddleware',  # 2nd — rate limiting
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'projects.middleware.TenantAuthMiddleware',   # last — auth
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

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.environ.get('REDIS_URL', 'redis://redis:6379/0'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'SOCKET_CONNECT_TIMEOUT': 5,
            'SOCKET_TIMEOUT': 5,
            'IGNORE_EXCEPTIONS': True,
        },
        'TIMEOUT': int(os.environ.get('REDIS_CACHE_TTL', '300')),
    }
}

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

# Cache TTLs (seconds)
CUENTA_CLOUD_CACHE_TTL = int(os.environ.get('CUENTA_CLOUD_CACHE_TTL', '300'))
EMPRESA_CACHE_TTL = int(os.environ.get('EMPRESA_CACHE_TTL', '300'))

RATE_LIMIT_ENABLED = os.environ.get('RATE_LIMIT_ENABLED', 'true').lower() == 'true'
RATE_LIMIT_REQUESTS = int(os.environ.get('RATE_LIMIT_REQUESTS', '10'))
RATE_LIMIT_WINDOW = int(os.environ.get('RATE_LIMIT_WINDOW', '60'))

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
