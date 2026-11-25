"""API routes for netbox_demo, mounted at /api/plugins/demo/."""

from django.urls import path
from netbox.api.routers import NetBoxRouter

from . import views

router = NetBoxRouter()
router.register("lab-agents", views.LabAgentViewSet)
router.register("lab-jobs", views.LabJobViewSet)
router.register("staged-changes", views.StagedChangeViewSet)

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
    path("intent/", views.FabricIntentView.as_view(), name="intent"),
    path("drift/", views.DriftView.as_view(), name="drift"),
    path("assurance/", views.AssuranceSummaryView.as_view(), name="assurance"),
    path("assurance/<str:action>/", views.AssuranceActionView.as_view(), name="assurance_action"),
    path("actions/<str:action>/", views.LabActionView.as_view(), name="action"),
    *router.urls,
]
