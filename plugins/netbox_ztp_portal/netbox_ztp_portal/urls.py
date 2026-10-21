"""UI URL routes. NetBox namespaces these under `plugins:netbox_ztp_portal:`."""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.IndexView.as_view(), name="index"),
    path("new-site/", views.ArchitectView.as_view(), name="architect"),
    path("new-block/", views.DatacenterView.as_view(), name="datacenter"),
    path("tracking/", views.TrackingView.as_view(), name="tracking"),
]
