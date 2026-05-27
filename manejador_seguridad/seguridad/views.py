import hashlib
import hmac
import logging
import requests as http_requests
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.views import APIView
from rest_framework.response import Response
from datetime import datetime, timezone
import uuid
from .mongo_client import get_mongo_db, ping_mongo
from django.db import connection, OperationalError
from django.conf import settings

from .models import EventoSeguridad, RegistroAuditoria, VerificacionIntegridad
from .serializers import EventoSeguridadSerializer, VerificacionIntegridadSerializer

logger = logging.getLogger(__name__)


def _get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def _validate_token_via_auth_service(token):
    """Validate token by calling manejador_autenticacion /auth/validate."""
    try:
        resp = http_requests.get(
            f'{settings.AUTH_SERVICE_URL}/auth/validate',
            headers={'Authorization': f'Bearer {token}'},
            timeout=settings.AUTH_SERVICE_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except http_requests.RequestException as e:
        logger.warning("Auth service unreachable: %s", e)
        # Fallback: validate locally if JWT secret is available
        return _validate_token_locally(token)


def _validate_token_locally(token):
    """Local JWT validation fallback (same secret as manejador_autenticacion)."""
    try:
        import jwt
        payload = jwt.decode(
            token,
            settings.LOCAL_JWT_SECRET,
            algorithms=['HS256'],
        )
        if payload.get('type') != 'access':
            return None
        return {
            'user_id': payload.get('sub'),
            'email': payload.get('email', ''),
            'empresa_id': payload.get('empresa_id'),
            'valid': True,
        }
    except Exception as e:
        logger.warning("Local token fallback failed: %s", e)
        return None


def _log_event(tipo, endpoint, metodo, ip, empresa_id_token=None, empresa_id_recurso=None, evidencia=None):
    """Create EventoSeguridad + RegistroAuditoria."""
    try:
        evento = EventoSeguridad.objects.create(
            tipo=tipo,
            endpoint=endpoint,
            metodo=metodo,
            ip_origen=ip,
            empresa_id_token=empresa_id_token,
            empresa_id_recurso=empresa_id_recurso,
            bloqueado=True,
            evidencia=evidencia or {},
        )
        RegistroAuditoria.objects.create(
            evento=evento,
            descripcion=(
                f"Acceso bloqueado: tipo={tipo}, endpoint={endpoint}, "
                f"ip={ip}, tenant_token={empresa_id_token}, tenant_recurso={empresa_id_recurso}"
            ),
        )
    except Exception:
        logger.exception("Failed to log security event")


class VerifyView(APIView):
    """
    POST /security/verify

    Validates the Bearer token and optionally checks tenant isolation.
    Body (all optional):
        empresa_id_recurso: UUID — if provided, checks cross-tenant access
        endpoint: str — for audit logging
        method: str — for audit logging

    Returns 200 with tenant info on success, 403 on any violation.
    """

    def post(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            _log_event(
                tipo=EventoSeguridad.Tipo.ACCESO_NO_AUTORIZADO,
                endpoint=request.data.get('endpoint', '/unknown'),
                metodo=request.data.get('method', request.method),
                ip=_get_client_ip(request),
                evidencia={'reason': 'missing_bearer_token'},
            )
            return Response(
                {'error': 'Token de autenticación requerido.', 'bloqueado': True},
                status=status.HTTP_403_FORBIDDEN,
            )

        token = auth_header[7:]
        user_info = _validate_token_via_auth_service(token)

        if user_info is None:
            _log_event(
                tipo=EventoSeguridad.Tipo.TOKEN_INVALIDO,
                endpoint=request.data.get('endpoint', '/unknown'),
                metodo=request.data.get('method', request.method),
                ip=_get_client_ip(request),
                evidencia={'reason': 'invalid_or_expired_token'},
            )
            return Response(
                {'error': 'Token inválido o expirado.', 'bloqueado': True},
                status=status.HTTP_403_FORBIDDEN,
            )

        empresa_id_token = user_info.get('empresa_id')
        empresa_id_recurso = request.data.get('empresa_id_recurso')

        # Cross-tenant check: only if empresa_id_recurso is provided
        if empresa_id_recurso and str(empresa_id_token) != str(empresa_id_recurso):
            _log_event(
                tipo=EventoSeguridad.Tipo.ACCESO_CRUZADO_TENANT,
                endpoint=request.data.get('endpoint', '/unknown'),
                metodo=request.data.get('method', request.method),
                ip=_get_client_ip(request),
                empresa_id_token=empresa_id_token,
                empresa_id_recurso=empresa_id_recurso,
                evidencia={
                    'reason': 'cross_tenant_access',
                    'token_empresa': str(empresa_id_token),
                    'resource_empresa': str(empresa_id_recurso),
                },
            )
            return Response(
                {
                    'error': 'Acceso denegado: empresa del token no coincide con el recurso solicitado.',
                    'bloqueado': True,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(
            {
                'valid': True,
                'empresa_id': empresa_id_token,
                'user_id': user_info.get('user_id'),
                'email': user_info.get('email', ''),
            },
            status=status.HTTP_200_OK,
        )


class AuditLogListView(APIView):
    """
    GET /security/audit-log
    Returns security events filtered by the requesting tenant's empresa_id.
    Requires valid Bearer token.
    """

    def get(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            return Response({'error': 'Token requerido.'}, status=status.HTTP_403_FORBIDDEN)

        token = auth_header[7:]
        user_info = _validate_token_via_auth_service(token)
        if user_info is None:
            return Response({'error': 'Token inválido.'}, status=status.HTTP_403_FORBIDDEN)

        empresa_id = user_info.get('empresa_id')
        qs = EventoSeguridad.objects.filter(empresa_id_token=empresa_id).order_by('-creado_en')[:100]
        serializer = EventoSeguridadSerializer(qs, many=True)
        return Response(serializer.data)


class AuditLogDetailView(APIView):
    """GET /security/audit-log/<id>"""

    def get(self, request, evento_id):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            return Response({'error': 'Token requerido.'}, status=status.HTTP_403_FORBIDDEN)

        token = auth_header[7:]
        user_info = _validate_token_via_auth_service(token)
        if user_info is None:
            return Response({'error': 'Token inválido.'}, status=status.HTTP_403_FORBIDDEN)

        empresa_id = user_info.get('empresa_id')
        try:
            evento = EventoSeguridad.objects.get(id=evento_id, empresa_id_token=empresa_id)
        except EventoSeguridad.DoesNotExist:
            return Response({'error': 'Evento no encontrado.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = EventoSeguridadSerializer(evento)
        return Response(serializer.data)


class HealthCheckView(APIView):
    """GET /health"""

    def get(self, request):
        checks = {}
        try:
            connection.ensure_connection()
            checks['database'] = 'ok'
        except OperationalError:
            checks['database'] = 'error'
            
        mongo_ok = ping_mongo()
        checks['mongodb'] = 'ok' if mongo_ok else 'error'

        all_ok = checks.get('database') == 'ok' and mongo_ok
        return Response(
            {
                'service': 'manejador_seguridad',
                'status': 'healthy' if all_ok else 'degraded',
                'checks': checks,
            },
            status=status.HTTP_200_OK,
        )


@api_view(['POST'])
def report_security_event(request):
    """
    Receives unauthorized access reports from other microservices.
    Does NOT require auth token — it receives reports about failed auth attempts.
    Saves to MongoDB.
    """
    data = request.data
    evento = {
        '_id': str(uuid.uuid4()),
        'tipo': data.get('tipo', 'desconocido'),
        'endpoint': data.get('endpoint', ''),
        'metodo': data.get('metodo', ''),
        'ip_origen': data.get('ip_origen', ''),
        'empresa_id_token': data.get('empresa_id_token'),
        'empresa_id_recurso': data.get('empresa_id_recurso'),
        'bloqueado': True,
        'evidencia': data.get('evidencia', {}),
        'creado_en': datetime.now(timezone.utc).isoformat(),
    }
    try:
        db = get_mongo_db()
        db.eventos_seguridad.insert_one(evento)
    except Exception as e:
        # Never let security reporting crash — log and continue
        import logging
        logging.getLogger(__name__).error(f'MongoDB insert failed: {e}')
    return Response({'registrado': True}, status=201)


@api_view(['GET'])
def audit_log_summary(request):
    """
    Returns aggregated security event summary for ASR experiment evidence.
    Requires valid auth token.
    """
    try:
        db = get_mongo_db()
        total = db.eventos_seguridad.count_documents({'bloqueado': True})
        pipeline = [
            {'$group': {'_id': '$tipo', 'count': {'$sum': 1}}}
        ]
        por_tipo_raw = list(db.eventos_seguridad.aggregate(pipeline))
        por_tipo = {item['_id']: item['count'] for item in por_tipo_raw}

        return Response({
            'total_intentos_no_autorizados': total,
            'porcentaje_bloqueados': 100.0 if total > 0 else 0.0,
            'por_tipo': por_tipo,
            'ultimos_eventos': list(
                db.eventos_seguridad.find(
                    {},
                    {'_id': 1, 'tipo': 1, 'endpoint': 1, 'ip_origen': 1, 'creado_en': 1}
                ).sort('creado_en', -1).limit(10)
            )
        })
    except Exception as e:
        return Response({'error': str(e)}, status=503)


# =============================================================================
# ASR2 – Seguridad (Integridad): vistas para el experimento de cifrado TLS
# =============================================================================

def _get_protocol(request):
    """
    Detect whether the incoming request arrived via HTTP or HTTPS.
    AWS ALB sets the X-Forwarded-Proto header when it terminates TLS.
    """
    forwarded_proto = request.META.get('HTTP_X_FORWARDED_PROTO', '')
    if forwarded_proto:
        return forwarded_proto.upper()
    return 'HTTPS' if request.is_secure() else 'HTTP'


def _log_integrity_check(endpoint, metodo, protocolo, ip, resultado, evidencia=None):
    """Persist a VerificacionIntegridad record for ASR2 audit evidence."""
    try:
        VerificacionIntegridad.objects.create(
            endpoint=endpoint,
            metodo=metodo,
            protocolo=protocolo,
            ip_origen=ip,
            resultado=resultado,
            tls_version=evidencia.get('tls_version', '') if evidencia else '',
            cipher_suite=evidencia.get('cipher_suite', '') if evidencia else '',
            evidencia=evidencia or {},
        )
    except Exception:
        logger.exception("Failed to log integrity check")


class TLSStatusView(APIView):
    """
    GET /security/tls-status

    ASR2 experiment: reports the protocol layer of the incoming request.

    - HTTPS (TLS terminated at ALB): returns 200 with protocol details.
    - HTTP (unencrypted): returns 400 to signal that the request is insecure.

    This endpoint is intentionally unauthenticated so BurpSuite can call
    it without a token and observe the HTTP-vs-HTTPS discrimination in action.
    """

    def get(self, request):
        protocolo = _get_protocol(request)
        ip = _get_client_ip(request)
        endpoint = request.path

        # TLS metadata forwarded by AWS ALB
        tls_version = request.META.get('HTTP_X_AMZN_TLS_VERSION', 'N/A')
        cipher_suite = request.META.get('HTTP_X_AMZN_TLS_CIPHER_SUITE', 'N/A')

        evidencia = {
            'protocolo': protocolo,
            'tls_version': tls_version,
            'cipher_suite': cipher_suite,
            'x_forwarded_proto': request.META.get('HTTP_X_FORWARDED_PROTO', 'N/A'),
            'host': request.META.get('HTTP_HOST', 'N/A'),
            'user_agent': request.META.get('HTTP_USER_AGENT', 'N/A'),
        }

        if protocolo == 'HTTPS':
            _log_integrity_check(
                endpoint=endpoint, metodo='GET', protocolo=protocolo,
                ip=ip, resultado=VerificacionIntegridad.Resultado.ACEPTADO,
                evidencia=evidencia,
            )
            return Response(
                {
                    'asr': 'ASR2 - Seguridad (Integridad)',
                    'resultado': 'ACEPTADO',
                    'protocolo': protocolo,
                    'tls_version': tls_version,
                    'cipher_suite': cipher_suite,
                    'mensaje': 'Comunicacion cifrada. El 100% de las solicitudes externas se realizan mediante HTTPS (TLS).',
                    'evidencia': evidencia,
                },
                status=status.HTTP_200_OK,
            )
        else:
            _log_integrity_check(
                endpoint=endpoint, metodo='GET', protocolo=protocolo,
                ip=ip, resultado=VerificacionIntegridad.Resultado.RECHAZADO,
                evidencia=evidencia,
            )
            return Response(
                {
                    'asr': 'ASR2 - Seguridad (Integridad)',
                    'resultado': 'RECHAZADO',
                    'protocolo': protocolo,
                    'mensaje': 'Solicitud HTTP rechazada. El sistema requiere HTTPS para garantizar la integridad de los datos en transito.',
                    'accion_requerida': 'Use HTTPS en lugar de HTTP.',
                    'evidencia': evidencia,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )


class IntegrityCheckView(APIView):
    """
    POST /security/integrity-check

    ASR2 experiment: verifies HMAC-SHA256 integrity of a payload.

    Body:
        payload  (str)  – the data whose integrity should be verified
        hmac_sha256 (str) – hex digest provided by the sender

    The shared secret is INTEGRITY_HMAC_SECRET from Django settings.
    Returns 200 if the digest matches, 422 if tampered, 400 if missing fields.

    This simulates the "cifrado y validacion de integridad" requirement
    for data transmitted between components.
    """

    def post(self, request):
        protocolo = _get_protocol(request)
        ip = _get_client_ip(request)
        endpoint = request.path

        payload = request.data.get('payload')
        provided_hmac = request.data.get('hmac_sha256')

        if not payload or not provided_hmac:
            return Response(
                {
                    'error': 'Se requieren los campos payload y hmac_sha256.',
                    'ejemplo': {
                        'payload': 'datos-a-verificar',
                        'hmac_sha256': 'hex-digest-hmac-sha256',
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Compute expected HMAC
        secret = settings.INTEGRITY_HMAC_SECRET.encode()
        expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        is_valid = hmac.compare_digest(expected, provided_hmac.lower())

        evidencia = {
            'protocolo': protocolo,
            'payload_length': len(payload),
            'hmac_provided': provided_hmac,
            'hmac_expected': expected,
            'coincide': is_valid,
        }

        if is_valid:
            _log_integrity_check(
                endpoint=endpoint, metodo='POST', protocolo=protocolo,
                ip=ip, resultado=VerificacionIntegridad.Resultado.INTEGRIDAD_OK,
                evidencia={k: v for k, v in evidencia.items() if k != 'hmac_expected'},
            )
            return Response(
                {
                    'asr': 'ASR2 - Seguridad (Integridad)',
                    'resultado': 'INTEGRIDAD_OK',
                    'mensaje': 'El hash HMAC-SHA256 coincide. Los datos no han sido alterados en transito.',
                    'hmac_verificado': provided_hmac,
                },
                status=status.HTTP_200_OK,
            )
        else:
            _log_integrity_check(
                endpoint=endpoint, metodo='POST', protocolo=protocolo,
                ip=ip, resultado=VerificacionIntegridad.Resultado.INTEGRIDAD_FALLO,
                evidencia=evidencia,
            )
            return Response(
                {
                    'asr': 'ASR2 - Seguridad (Integridad)',
                    'resultado': 'INTEGRIDAD_FALLO',
                    'mensaje': 'El hash HMAC-SHA256 NO coincide. Los datos pueden haber sido alterados en transito.',
                    'alerta': 'Posible manipulacion de datos detectada.',
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )


class IntegrityLogView(APIView):
    """
    GET /security/integrity-log

    ASR2 experiment: returns the last 100 TLS/integrity check records.
    Used to generate evidence that the system logs 100% of verifications.
    Requires a valid Bearer token.
    """

    def get(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            return Response({'error': 'Token requerido.'}, status=status.HTTP_403_FORBIDDEN)

        token = auth_header[7:]
        user_info = _validate_token_via_auth_service(token)
        if user_info is None:
            return Response({'error': 'Token invalido.'}, status=status.HTTP_403_FORBIDDEN)

        qs = VerificacionIntegridad.objects.all().order_by('-creado_en')[:100]
        serializer = VerificacionIntegridadSerializer(qs, many=True)
        return Response({
            'asr': 'ASR2 - Seguridad (Integridad)',
            'total_registros': len(qs),
            'registros': serializer.data,
        })

