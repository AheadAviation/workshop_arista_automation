"""Page views for the AHEAD ZTP Portal.

Each view renders a full-page portal template that extends NetBox's base layout
(so auth/nav/theme are inherited) and mounts the ported portal UI + assets. The
interactive behavior lives in the plugin's static JS, which calls the plugin API
under /api/plugins/ztp/.

**Every view requires a logged-in user with device-change rights.**

These were bare `TemplateView`s, which render for anyone — an unauthenticated visitor
could open the fabric designer, the onboarder and the fleet tracker and see the whole
authoring UI. That was survivable only while `LOGIN_REQUIRED` was false for the original
workshop; it is not survivable now that this portal can build fabrics into NetBox.

`LoginRequiredMixin` alone is not enough either: a logged-in user with no permissions
would still see it. The permission is `dcim.change_device`, because every page here
leads to writing devices into the source of truth — the same right NetBox itself
requires to create a device.
"""
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.views.generic import TemplateView

from . import conf as config


class _PortalView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    # A refusal, not a redirect to the login page: someone already logged in but without
    # the permission should be told so, not bounced in a loop.
    raise_exception = True
    permission_required = ("dcim.change_device",)

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
