"""Page views for the AHEAD ZTP Portal.

Each view renders a full-page portal template that extends NetBox's base layout
(so auth/nav/theme are inherited) and mounts the ported portal UI + assets. The
interactive behavior lives in the plugin's static JS, which calls the plugin API
under /api/plugins/ztp/.
"""
from django.views.generic import TemplateView

from . import conf as config


class _PortalView(TemplateView):
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["use_mock_infoblox"] = config.use_mock_infoblox()
        return ctx


class IndexView(_PortalView):
    template_name = "netbox_ztp_portal/index.html"


class ArchitectView(_PortalView):
    template_name = "netbox_ztp_portal/architect.html"


class DatacenterView(_PortalView):
    template_name = "netbox_ztp_portal/datacenter.html"


class TrackingView(_PortalView):
    template_name = "netbox_ztp_portal/tracking.html"
