"""
Auth backends: Cognito (production) and local JWT (docker compose dev mode).

When COGNITO_USER_POOL_ID env var is set, all auth goes through Cognito.
When it is NOT set, local HS256 JWT tokens are issued against the usuarios_locales table.
This fallback lets `docker compose up` work without any AWS credentials.
"""
import logging
from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _utcnow():
    return datetime.now(tz=timezone.utc)


# ── Local JWT ─────────────────────────────────────────────────────────────────

def local_login(email, password):
    from .models import UsuarioLocal
    try:
        user = UsuarioLocal.objects.get(email=email, activo=True)
    except UsuarioLocal.DoesNotExist:
        return None

    if not user.check_password(password):
        return None

    return _issue_local_tokens(user)


def _issue_local_tokens(user):
    now = _utcnow()
    access_payload = {
        'sub': str(user.id),
        'email': user.email,
        'empresa_id': str(user.empresa_id),
        'rol': user.rol,
        'type': 'access',
        'iat': int(now.timestamp()),
        'exp': int((now + timedelta(seconds=settings.LOCAL_JWT_ACCESS_EXPIRY)).timestamp()),
    }
    refresh_payload = {
        'sub': str(user.id),
        'email': user.email,
        'empresa_id': str(user.empresa_id),
        'rol': user.rol,
        'type': 'refresh',
        'iat': int(now.timestamp()),
        'exp': int((now + timedelta(seconds=settings.LOCAL_JWT_REFRESH_EXPIRY)).timestamp()),
    }
    access_token = jwt.encode(access_payload, settings.LOCAL_JWT_SECRET, algorithm='HS256')
    refresh_token = jwt.encode(refresh_payload, settings.LOCAL_JWT_SECRET, algorithm='HS256')
    return {
        'access_token': access_token,
        'refresh_token': refresh_token,
        'token_type': 'Bearer',
        'expires_in': settings.LOCAL_JWT_ACCESS_EXPIRY,
        'rol': user.rol,
    }


def local_refresh(refresh_token_str):
    try:
        payload = jwt.decode(
            refresh_token_str,
            settings.LOCAL_JWT_SECRET,
            algorithms=['HS256'],
        )
    except jwt.PyJWTError as e:
        logger.warning("Local refresh token invalid: %s", e)
        return None

    if payload.get('type') != 'refresh':
        return None

    from .models import UsuarioLocal
    try:
        user = UsuarioLocal.objects.get(id=payload['sub'], activo=True)
    except UsuarioLocal.DoesNotExist:
        return None

    tokens = _issue_local_tokens(user)
    return {
        'access_token': tokens['access_token'],
        'token_type': 'Bearer',
        'expires_in': settings.LOCAL_JWT_ACCESS_EXPIRY,
    }


def local_validate(token):
    try:
        payload = jwt.decode(
            token,
            settings.LOCAL_JWT_SECRET,
            algorithms=['HS256'],
        )
    except jwt.PyJWTError as e:
        logger.warning("Local token validation failed: %s", e)
        return None

    if payload.get('type') != 'access':
        return None

    return {
        'user_id': payload.get('sub'),
        'email': payload.get('email', ''),
        'empresa_id': payload.get('empresa_id'),
        'rol': payload.get('rol', 'ANALYST'),
        'valid': True,
    }


# ── Cognito ───────────────────────────────────────────────────────────────────

def cognito_login(email, password):
    import boto3
    from botocore.exceptions import ClientError

    client = boto3.client('cognito-idp', region_name=settings.COGNITO_REGION)
    try:
        response = client.initiate_auth(
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={'USERNAME': email, 'PASSWORD': password},
            ClientId=settings.COGNITO_CLIENT_ID,
        )
        result = response['AuthenticationResult']
        # Decode the ID token to extract the rol claim without verifying (just for the login response)
        try:
            id_payload = jwt.decode(result['IdToken'], options={"verify_signature": False})
            rol = id_payload.get('custom:rol', 'ANALYST')
        except Exception:
            rol = 'ANALYST'
        return {
            'access_token': result['AccessToken'],
            'refresh_token': result.get('RefreshToken', ''),
            'id_token': result['IdToken'],
            'token_type': 'Bearer',
            'expires_in': result.get('ExpiresIn', 3600),
            'rol': rol,
        }
    except ClientError as e:
        code = e.response['Error']['Code']
        if code in ('NotAuthorizedException', 'UserNotFoundException'):
            return None
        logger.error("Cognito login error: %s", e)
        raise


def cognito_refresh(refresh_token_str):
    import boto3
    from botocore.exceptions import ClientError

    client = boto3.client('cognito-idp', region_name=settings.COGNITO_REGION)
    try:
        response = client.initiate_auth(
            AuthFlow='REFRESH_TOKEN_AUTH',
            AuthParameters={'REFRESH_TOKEN': refresh_token_str},
            ClientId=settings.COGNITO_CLIENT_ID,
        )
        result = response['AuthenticationResult']
        return {
            'access_token': result['AccessToken'],
            'id_token': result['IdToken'],
            'token_type': 'Bearer',
            'expires_in': result.get('ExpiresIn', 3600),
        }
    except ClientError:
        return None


def cognito_validate(token):
    """
    Validate a Cognito access token using the GetUser API.

    Why GetUser instead of local JWT decode:
    - Cognito access tokens use 'client_id' not 'aud', so jwt.decode(audience=...)
      raises InvalidAudienceError for access tokens.
    - Custom attributes (custom:empresa_id) are NOT included in access tokens,
      only in ID tokens. GetUser returns all user attributes.
    - GetUser rejects expired/revoked tokens server-side — no local key management.
    """
    import boto3
    from botocore.exceptions import ClientError

    try:
        client = boto3.client('cognito-idp', region_name=settings.COGNITO_REGION)
        user_info = client.get_user(AccessToken=token)

        attrs = {a['Name']: a['Value'] for a in user_info.get('UserAttributes', [])}
        email = attrs.get('email', '')
        empresa_id = attrs.get('custom:empresa_id')

        # custom:empresa_id may not be set on the Cognito user — fall back to local DB
        if not empresa_id:
            try:
                from autenticacion.models import Usuario
                user = Usuario.objects.filter(email=email).first()
                if user and user.empresa_id:
                    empresa_id = str(user.empresa_id)
            except Exception as db_err:
                logger.warning("DB fallback for empresa_id failed: %s", db_err)

        return {
            'user_id': user_info.get('Username'),
            'email': email,
            'empresa_id': empresa_id,
            'rol': attrs.get('custom:rol', 'ANALYST'),
            'valid': True,
        }
    except ClientError as e:
        logger.warning("Cognito GetUser failed: %s", e.response['Error']['Message'])
        return None
    except Exception as e:
        logger.warning("Cognito token validation failed: %s", e)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def do_login(email, password):
    if settings.USE_COGNITO:
        return cognito_login(email, password)
    return local_login(email, password)


def do_refresh(refresh_token_str):
    if settings.USE_COGNITO:
        return cognito_refresh(refresh_token_str)
    return local_refresh(refresh_token_str)


def do_validate(token):
    if settings.USE_COGNITO:
        return cognito_validate(token)
    return local_validate(token)
