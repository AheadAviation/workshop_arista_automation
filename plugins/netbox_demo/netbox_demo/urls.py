"""URL routes for netbox_demo (INIT-001/SPEC-002).

Every route is a real URL name referenced by `navigation.py` or a template. A
nav item pointing at a name that does not exist is a load-time 500 in NetBox, so
these two files are kept in step deliberately.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.runbook, name="runbook"),
    path("steps/<str:key>/run/", views.run_step, name="run_step"),
    path("steps/reset/", views.reset_runbook, name="reset_runbook"),
    path("status.json", views.runbook_status, name="runbook_status"),
    # Drift and assurance
    path("drift/", views.drift, name="drift"),
    path("drift/inject/", views.inject_drift, name="inject_drift"),
    path("drift/remediate/", views.remediate_drift, name="remediate_drift"),
    path("assurance/apply/", views.assurance_apply, name="assurance_apply"),
    path("assurance/rediff/", views.assurance_rediff, name="assurance_rediff"),
    # Day 2
    path("day2/", views.day2_view, name="day2"),
    path("day2/preview/", views.day2_preview, name="day2_preview"),
    path("day2/<str:change_id>/save/", views.day2_save_only, name="day2_save"),
    path("day2/<str:change_id>/apply/", views.day2_apply, name="day2_apply"),
    # Jobs
    path("jobs/", views.job_list, name="job_list"),
    path("jobs/<int:pk>/", views.job_detail, name="job_detail"),
    # Fabric actions
    path("fabric/seed/", views.seed_fabric, name="seed_fabric"),
    path("fabric/clean/", views.clean_fabric, name="clean_fabric"),
    path("fabric/push/", views.push_configs, name="push_configs"),
    path("live/", views.live_config, name="live"),
    path("preview/", views.preview_render, name="preview"),
    path("agent/health.json", views.agent_health, name="agent_health"),
]
