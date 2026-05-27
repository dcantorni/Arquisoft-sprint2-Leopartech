import time
import os
import threading
from collections import defaultdict
from django.http import JsonResponse

RATE_LIMIT_ENABLED = os.environ.get('RATE_LIMIT_ENABLED', 'true').lower() == 'true'
RATE_LIMIT_REQUESTS = int(os.environ.get('RATE_LIMIT_REQUESTS', '10'))
RATE_LIMIT_WINDOW = int(os.environ.get('RATE_LIMIT_WINDOW', '60'))

_request_counts = defaultdict(list)
_lock = threading.Lock()


class RateLimitMiddleware:
    """
    Layer 2 rate limiting — Django middleware.
    Layer 1 is AWS WAF at the ALB level.
    Only applies to POST /projects.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if RATE_LIMIT_ENABLED and request.method == 'POST' and '/projects' in request.path:
            ip = self._get_client_ip(request)
            now = time.time()

            with _lock:
                _request_counts[ip] = [
                    t for t in _request_counts[ip]
                    if now - t < RATE_LIMIT_WINDOW
                ]
                if len(_request_counts[ip]) >= RATE_LIMIT_REQUESTS:
                    self._report_to_seguridad(request, ip)
                    return JsonResponse(
                        {
                            'error': 'Too many requests',
                            'retry_after': RATE_LIMIT_WINDOW,
                            'limit': RATE_LIMIT_REQUESTS,
                        },
                        status=429
                    )
                _request_counts[ip].append(now)

        return self.get_response(request)

    def _get_client_ip(self, request):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', 'unknown')

    def _report_to_seguridad(self, request, ip):
        def _send():
            try:
                import requests as req
                seguridad_url = os.environ.get('SEGURIDAD_URL', '')
                if seguridad_url:
                    req.post(
                        f'{seguridad_url}/security/events',
                        json={
                            'tipo': 'rate_limit_exceeded',
                            'endpoint': request.path,
                            'metodo': request.method,
                            'ip_origen': ip,
                            'evidencia': {
                                'limit': RATE_LIMIT_REQUESTS,
                                'window': RATE_LIMIT_WINDOW,
                            }
                        },
                        timeout=0.5
                    )
            except Exception:
                pass
        threading.Thread(target=_send, daemon=True).start()
