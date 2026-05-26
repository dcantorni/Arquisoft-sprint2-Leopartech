from django.urls import path
from . import views

# CHANGE 6: added cloud/ prefix so ALB forwards /cloud/* and Django matches correctly.
# Nginx (local dev) also forwards /cloud/cloud-accounts with the full prefix
# via proxy_pass http://cloud_service/cloud/; — consistent between envs.
urlpatterns = [
    path('cloud/cloud-accounts', views.CuentaCloudListCreateView.as_view(), name='cuenta-cloud-list-create'),
    path('cloud/cloud-accounts/<uuid:cuenta_id>', views.CuentaCloudDetailView.as_view(), name='cuenta-cloud-detail'),
    path('cloud/cloud-accounts/<uuid:cuenta_id>/validate', views.CuentaCloudValidateView.as_view(), name='cuenta-cloud-validate'),
    path('cloud/resources', views.RecursoCloudListCreateView.as_view(), name='recurso-cloud-list-create'),
    path('cloud/resources/<uuid:recurso_id>', views.RecursoCloudDetailView.as_view(), name='recurso-cloud-detail'),
    path('cloud/metrics', views.MetricaConsumoView.as_view(), name='metrica-consumo'),
    path('health', views.HealthCheckView.as_view(), name='health-check'),
]
