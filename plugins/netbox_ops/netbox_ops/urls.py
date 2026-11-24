"""URL routes for the Ops plugin, mounted at /plugins/ops/.

Each tab gets its **own named route**. Two NetBox 4.1 constraints forced that shape:

1. `{% url %}` and `reverse()` cannot name a route carrying a query string — `home?tab=labs`
   is read as a view name and raises `NoReverseMatch`, which breaks the nav on every NetBox
   page.
2. `PluginMenuItem` in 4.1 has no `link_args` (its signature is
   `(link, link_text, staff_only, permissions, buttons)`), so a single parameterised route
   cannot be referenced from the menu.

Named routes satisfy both, and they read better in the URL bar too.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.OpsHomeView.as_view(), name="home"),

    # One route per tab, each named, so both the nav and the tab strip can reverse them.
    path("labs/", views.OpsHomeView.as_view(), {"tab": "labs"}, name="labs"),
    path("live/", views.OpsHomeView.as_view(), {"tab": "live"}, name="live"),
    path("change/", views.OpsHomeView.as_view(), {"tab": "change"}, name="change"),
    path("lifecycle/", views.OpsHomeView.as_view(), {"tab": "lifecycle"}, name="lifecycle"),
    path("awx/", views.OpsHomeView.as_view(), {"tab": "awx"}, name="awx"),
    path("tickets/", views.OpsHomeView.as_view(), {"tab": "tickets"}, name="tickets"),
    path("audit/", views.OpsHomeView.as_view(), {"tab": "audit"}, name="audit"),

    path("action/preview/", views.ChangePreviewView.as_view(), name="change_preview"),
    path("action/approve/", views.ChangeApproveView.as_view(), name="change_approve"),
    path("action/inject/", views.DriftInjectView.as_view(), name="drift_inject"),
    path("action/remediate/", views.DriftRemediateView.as_view(), name="drift_remediate"),
    path("action/lab/deploy/", views.LabDeployView.as_view(), name="lab_deploy"),
    path("action/lab/destroy/", views.LabDestroyView.as_view(), name="lab_destroy"),
    path("action/import/", views.ImportDevicesView.as_view(), name="import_devices"),
    path("action/apply/", views.ApplyConfigsView.as_view(), name="apply_configs"),
    path("action/begin/", views.BeginChangeView.as_view(), name="begin_change"),
    path("action/request/", views.RequestChangeView.as_view(), name="request_change"),
    path("action/push/", views.ApproveAndPushView.as_view(), name="approve_push"),
    path("action/reset/", views.ResetDemoView.as_view(), name="reset_demo"),

    path("status.json", views.StatusJsonView.as_view(), name="status"),
]
