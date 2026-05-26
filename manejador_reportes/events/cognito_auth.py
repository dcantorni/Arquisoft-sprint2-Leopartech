"""Cognito id_token validation for TenantAuthMiddleware (mirrors manejador_autenticacion)."""
import logging
import os
from typing import Optional

import jwt
import requests

logger = logging.getLogger(__name__)


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
        jwks_url = (
            f'https://cognito-idp.{region}.amazonaws.com'
            f'/{pool_id}/.well-known/jwks.json'
        )
        jwks = requests.get(jwks_url, timeout=5).json()
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

        return payload.get('custom:empresa_id')
    except Exception as exc:
        logger.warning("Cognito id_token validation failed: %s", exc)
        return None
