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
    path("workshop/", views.OpsHomeView.as_view(), {"tab": "workshop"}, name="workshop"),
    path("devices/", views.OpsHomeView.as_view(), {"tab": "devices"}, name="devices"),
    path("labs/", views.OpsHomeView.as_view(), {"tab": "labs"}, name="labs"),
    path("live/", views.OpsHomeView.as_view(), {"tab": "live"}, name="live"),
    path("change/", views.OpsHomeView.as_view(), {"tab": "actions"}, name="change"),
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
    # The interactive day-two operation page (INIT-005). JSON because the form re-renders as
    # the operator changes operation, and a full page reload per keystroke is not a demo.
    path("actions/", views.OpsHomeView.as_view(), {"tab": "actions"}, name="actions"),
    path("actions/catalogue.json", views.OpsCatalogueJsonView.as_view(), name="actions_catalogue"),
    path("actions/resolve/", views.OperationResolveView.as_view(), name="actions_resolve"),
    path("actions/preview/", views.OperationPreviewView.as_view(), name="actions_preview"),
    path("actions/apply/", views.OperationApplyView.as_view(), name="actions_apply"),
    # The fabric topology, with the parameters netbox_topology_views needs to draw anything.
    path("topology/", views.TopologyRedirectView.as_view(), name="topology"),

    # Guided Workshop Quick Start & Day-2 Operations actions (INIT-004/SPEC-003).
    path("workshop/action/clean/", views.WorkshopCleanView.as_view(), name="workshop_clean"),
    path("workshop/action/import/", views.WorkshopImportView.as_view(), name="workshop_import"),
    path("workshop/action/topology/", views.WorkshopTopologyGenView.as_view(), name="workshop_topology_gen"),
    path("workshop/action/configs/", views.WorkshopConfigsGenView.as_view(), name="workshop_configs_gen"),
    path("workshop/action/deploy/", views.WorkshopDeployView.as_view(), name="workshop_deploy"),
    path("workshop/action/destroy/", views.WorkshopDestroyView.as_view(), name="workshop_destroy"),
    path("workshop/action/push/", views.WorkshopPushView.as_view(), name="workshop_push"),
    path("workshop/action/verify/", views.WorkshopVerifyView.as_view(), name="workshop_verify"),
    path("workshop/action/validate/", views.WorkshopValidateView.as_view(), name="workshop_validate"),
    path("workshop/action/backup/", views.WorkshopBackupView.as_view(), name="workshop_backup"),
    path("workshop/action/rollback/", views.WorkshopRollbackView.as_view(), name="workshop_rollback"),
    path("workshop/action/diff/", views.WorkshopDiffView.as_view(), name="workshop_diff"),
    path("workshop/action/standard-config/", views.WorkshopStandardConfigView.as_view(), name="workshop_standard_config"),
    path("workshop/status.json", views.WorkshopStatusJsonView.as_view(), name="workshop_status_json"),
    path("workshop/view/topology/", views.WorkshopTopologyInspectView.as_view(), name="workshop_topology_view"),
    path("workshop/view/config/<str:device>/", views.WorkshopConfigInspectView.as_view(), name="workshop_config_view"),
]
