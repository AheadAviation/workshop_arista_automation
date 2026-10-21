"""Plugin API routes. NetBox mounts these under /api/plugins/ztp/."""
from django.urls import path

from . import views

app_name = "netbox_ztp_portal-api"

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
    path("sites/", views.SitesView.as_view(), name="sites"),
    path("preview/", views.PreviewView.as_view(), name="preview"),
    path("tracking/", views.TrackingView.as_view(), name="tracking"),
    path("devices/", views.DevicesView.as_view(), name="devices"),
    path("deploy/", views.DeployView.as_view(), name="deploy"),
]
