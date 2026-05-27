import logging
import json
import redis
from config import settings

logger = logging.getLogger(__name__)

redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

class CuentaCloudCache:
    KEY_VALIDATION = 'cuenta_cloud:{id}:activa'
    KEY_DETAIL = 'cuenta_cloud:{id}:detail'

    @classmethod
    def set_validation(cls, cuenta_id: str, is_active: bool):
        try:
            redis_client.setex(cls.KEY_VALIDATION.format(id=cuenta_id), settings.CUENTA_CLOUD_CACHE_TTL, str(is_active))
        except Exception as exc:
            logger.warning("Redis set validation error for %s: %s", cuenta_id, exc)

    @classmethod
    def get_validation(cls, cuenta_id: str) -> bool | None:
        try:
            val = redis_client.get(cls.KEY_VALIDATION.format(id=cuenta_id))
            if val is not None:
                return val.lower() == 'true'
            return None
        except Exception as exc:
            logger.warning("Redis get validation error for %s: %s", cuenta_id, exc)
            return None

    @classmethod
    def set_detail(cls, cuenta_id: str, data: dict):
        try:
            redis_client.setex(cls.KEY_DETAIL.format(id=cuenta_id), settings.CUENTA_CLOUD_CACHE_TTL, json.dumps(data))
        except Exception as exc:
            logger.warning("Redis set detail error for %s: %s", cuenta_id, exc)

    @classmethod
    def get_detail(cls, cuenta_id: str) -> dict | None:
        try:
            val = redis_client.get(cls.KEY_DETAIL.format(id=cuenta_id))
            if val:
                return json.loads(val)
            return None
        except Exception as exc:
            logger.warning("Redis get detail error for %s: %s", cuenta_id, exc)
            return None

    @classmethod
    def invalidate(cls, cuenta_id: str):
        try:
            redis_client.delete(
                cls.KEY_VALIDATION.format(id=cuenta_id),
                cls.KEY_DETAIL.format(id=cuenta_id)
            )
        except Exception as exc:
            logger.warning("Redis invalidate error for %s: %s", cuenta_id, exc)


class RecursoCloudCache:
    KEY_DETAIL = 'recurso:{id}'
    KEY_LIST = 'recursos_lista:{cuenta_id}'

    @classmethod
    def get_detail(cls, recurso_id: str) -> dict | None:
        try:
            val = redis_client.get(cls.KEY_DETAIL.format(id=recurso_id))
            if val:
                return json.loads(val)
            return None
        except Exception as exc:
            logger.warning("Redis get recurso error for %s: %s", recurso_id, exc)
            return None

    @classmethod
    def set_detail(cls, recurso_id: str, data: dict):
        try:
            redis_client.setex(cls.KEY_DETAIL.format(id=recurso_id), settings.RECURSO_CACHE_TTL, json.dumps(data))
        except Exception as exc:
            logger.warning("Redis set recurso error for %s: %s", recurso_id, exc)

    @classmethod
    def get_list(cls, cuenta_id: str) -> list | None:
        try:
            val = redis_client.get(cls.KEY_LIST.format(cuenta_id=cuenta_id))
            if val:
                return json.loads(val)
            return None
        except Exception as exc:
            logger.warning("Redis get list error for cuenta %s: %s", cuenta_id, exc)
            return None

    @classmethod
    def set_list(cls, cuenta_id: str, data: list):
        try:
            redis_client.setex(cls.KEY_LIST.format(cuenta_id=cuenta_id), settings.RECURSO_CACHE_TTL, json.dumps(data))
        except Exception as exc:
            logger.warning("Redis set list error for cuenta %s: %s", cuenta_id, exc)

    @classmethod
    def invalidate_detail(cls, recurso_id: str):
        try:
            redis_client.delete(cls.KEY_DETAIL.format(id=recurso_id))
        except Exception as exc:
            logger.warning("Redis invalidate recurso error for %s: %s", recurso_id, exc)

    @classmethod
    def invalidate_list(cls, cuenta_id: str):
        try:
            redis_client.delete(cls.KEY_LIST.format(cuenta_id=cuenta_id))
        except Exception as exc:
            logger.warning("Redis invalidate list error for cuenta %s: %s", cuenta_id, exc)
