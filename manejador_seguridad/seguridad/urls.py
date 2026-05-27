from django.urls import path
from . import views

urlpatterns = [
    # ASR3 – Seguridad (Confidencialidad / Aislamiento Multi-tenant)
    path('security/verify', views.VerifyView.as_view(), name='security-verify'),
    path('security/events', views.report_security_event, name='security-events'),
    path('security/audit-log', views.AuditLogListView.as_view(), name='audit-log-list'),
    path('security/audit-log/summary', views.audit_log_summary, name='audit-log-summary'),
    path('security/audit-log/<uuid:evento_id>', views.AuditLogDetailView.as_view(), name='audit-log-detail'),

    # ASR2 – Seguridad (Integridad): cifrado TLS + validacion HMAC
    path('security/tls-status', views.TLSStatusView.as_view(), name='tls-status'),
    path('security/integrity-check', views.IntegrityCheckView.as_view(), name='integrity-check'),
    path('security/integrity-log', views.IntegrityLogView.as_view(), name='integrity-log'),

    # Health
    path('health', views.HealthCheckView.as_view(), name='health-check'),
]
