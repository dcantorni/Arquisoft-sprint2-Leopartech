"""Cognito id_token validation for TenantAuthMiddleware (mirrors manejador_autenticacion)."""
import logging
import os
import time
from typing import Optional, Tuple

import jwt
import requests

logger = logging.getLogger(__name__)

# Fallback when Cognito id_token omits custom:empresa_id (matches terraform test users).
_EMAIL_TENANT_MAP = {
    'empresa_a@bite.co': '550e8400-e29b-41d4-a716-446655440001',
    'empresa_b@bite.co': '550e8400-e29b-41d4-a716-446655440002',
}

_JWKS_CACHE: dict[str, Tuple[float, dict]] = {}
_JWKS_TTL_SECONDS = 3600


def _empresa_id_from_email(email: str) -> Optional[str]:
    return _EMAIL_TENANT_MAP.get(email.strip().lower())


def _get_jwks(pool_id: str, region: str) -> dict:
    now = time.time()
    cached = _JWKS_CACHE.get(pool_id)
    if cached and now - cached[0] < _JWKS_TTL_SECONDS:
        return cached[1]
    jwks_url = (
        f'https://cognito-idp.{region}.amazonaws.com'
        f'/{pool_id}/.well-known/jwks.json'
    )
    jwks = requests.get(jwks_url, timeout=5).json()
    _JWKS_CACHE[pool_id] = (now, jwks)
    return jwks


# #region agent log
def _debug_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    import json
    import time
    entry = {
        'sessionId': 'c85b96',
        'hypothesisId': hypothesis_id,
        'location': location,
        'message': message,
        'data': data,
        'timestamp': int(time.time() * 1000),
    }
    line = json.dumps(entry, default=str) + '\n'
    for path in (
        os.environ.get('DEBUG_LOG_PATH', ''),
        '/tmp/debug-c85b96.log',
    ):
        if not path:
            continue
        try:
            with open(path, 'a', encoding='utf-8') as fh:
                fh.write(line)
            break
        except OSError:
            continue
# #endregion


def validate_cognito_id_token(token: str) -> Optional[str]:
    """
    Validate a Cognito id_token and return empresa_id, or None if invalid.
    Requires COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID, COGNITO_REGION env vars.
    """
    pool_id = os.environ.get('COGNITO_USER_POOL_ID', '')
    client_id = os.environ.get('COGNITO_CLIENT_ID', '')
    region = os.environ.get('COGNITO_REGION', 'us-east-1')
    if not pool_id or not client_id:
        return None

    try:
        jwks = _get_jwks(pool_id, region)
        header = jwt.get_unverified_header(token)
        kid = header.get('kid')

        from jwt.algorithms import RSAAlgorithm
        pub_key = None
        for jwk in jwks.get('keys', []):
            if jwk.get('kid') == kid:
                pub_key = RSAAlgorithm.from_jwk(jwk)
                break

        if not pub_key:
            logger.warning("Cognito JWK not found for kid=%s", kid)
            return None

        payload = jwt.decode(
            token,
            pub_key,
            algorithms=['RS256'],
            audience=client_id,
        )
        if payload.get('token_use') != 'id':
            return None

        empresa_id = payload.get('custom:empresa_id')
        if not empresa_id:
            email = payload.get('email', '')
            if email:
                empresa_id = _empresa_id_from_email(email)

        # #region agent log
        _debug_log('H5', 'cognito_auth.py:validate', 'cognito tenant resolution', {
            'has_custom_claim': bool(payload.get('custom:empresa_id')),
            'email': payload.get('email', ''),
            'empresa_id_resolved': bool(empresa_id),
        })
        # #endregion

        return empresa_id
    except Exception as exc:
        logger.warning("Cognito id_token validation failed: %s", exc)
        return None
