"""The Ops plugin's views (INIT-002/SPEC-003).

Thin on purpose. Each view asks `engine.py` for data and renders it; none of them does a
comparison, touches a device, or decides whether a change is allowed. That logic lives in
`workbench/` where it is tested, and duplicating it here is how two surfaces start disagreeing
about whether a switch is compliant.

CSRF: the POST endpoints are called by the plugin's own page with the session cookie, so they
are CSRF-protected like any NetBox form. The page sends the token.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_protect

from . import conf, engine
from .access import ChangeOperatorMixin, ReadOnlyOperatorMixin

logger = logging.getLogger("netbox_ops")


class OpsHomeView(ReadOnlyOperatorMixin, View):
    """One page with tabs: devices, labs, change, AWX, tickets, audit.

    The tab is a **path component**, not a query string. `{% url %}` cannot reverse a name
    that includes `?tab=...` — it treats the whole string as a view name and raises
    `NoReverseMatch`, which breaks the nav on every page in NetBox, not just this one.
    """

    template_name = "netbox_ops/home.html"

    #: The tab strip: key, label, and the **named route** for that tab. NetBox 4.1 cannot
    #: reverse a route carrying a query string or an argument from the nav, so each tab is a
    #: named route of its own.
    TABS = (
        ("devices", "Devices", "home"),
        ("live", "Live config", "live"),
        ("change", "Day-two change", "change"),
        ("labs", "Container labs", "labs"),
        ("lifecycle", "Lifecycle", "lifecycle"),
        ("awx", "AWX jobs", "awx"),
        ("tickets", "ITSM tickets", "tickets"),
        ("audit", "Audit trail", "audit"),
    )

    def get(self, request, tab: str = "devices", *args, **kwargs):
        allowed = {key: (label, route) for key, label, route in self.TABS}
        if tab not in allowed:
            raise Http404(f"unknown tab {tab!r}")

        # Reverse here rather than in the template: a broken route name then fails in the
        # view where the traceback is readable, not inside template rendering.
        from django.urls import reverse

        tabs = [
            (key, label, reverse(f"plugins:netbox_ops:{route}"))
            for key, label, route in self.TABS
        ]

        context = {
            "tab": tab,
            "tabs": tabs,
            "demo_vlan": conf.demo_vlan(),
            "demo_vlan_name": conf.demo_vlan_name(),
        }

        try:
            return self._render_tab(request, tab, context)
        except Exception:
            # Log the real exception. NetBox's server process reports a 500 with no traceback
            # in `docker compose logs`, which makes a plugin failure invisible; this is the
            # only place it will surface, so it must not be silent.
            logger.exception("netbox_ops: rendering tab %r failed", tab)
            raise

    def _render_tab(self, request, tab: str, context: dict):
        # Load only the tab being shown. Each of these can take seconds against real devices,
        # and loading all six on every request would make the page feel broken.
        if tab == "devices":
            context["fabric"] = engine.fabric_overview().to_dict()
            context["setup"] = engine.devices_status().to_dict()
        elif tab == "labs":
            context["lab"] = engine.lab_status().to_dict()
        elif tab == "live":
            context["live"] = engine.live_configs().to_dict()
        elif tab == "change":
            context["fabric"] = engine.fabric_overview().to_dict()
            context["tickets"] = engine.itsm_tickets(limit=10).to_dict()
        elif tab == "lifecycle":
            life = engine.lifecycle().to_dict()
            context["lifecycle"] = life
            # Computed here rather than in the template: Django templates cannot subtract,
            # and an "add" chain to fake it is the kind of thing that silently shows a wrong
            # number on a slide.
            total = life.get("total") or 0
            compliant = life.get("compliant") or 0
            context["drifted"] = max(total - compliant, 0)
            context["vendors"] = engine.two_vendors().to_dict()
        elif tab == "awx":
            context["awx"] = engine.awx_status().to_dict()
            context["templates"] = engine.awx_job_templates().to_dict()
        elif tab == "tickets":
            context["itsm"] = engine.itsm_status().to_dict()
            context["tickets"] = engine.itsm_tickets().to_dict()
        elif tab == "audit":
            context["audit"] = engine.audit().to_dict()

        return render(request, self.template_name, context)


@method_decorator(csrf_protect, name="post")
class ChangePreviewView(ChangeOperatorMixin, View):
    """Stage a VLAN change and show its diff. Applies nothing."""

    def post(self, request, *args, **kwargs):
        device = str(request.POST.get("device") or "").strip()
        if not device:
            messages.error(request, "A device is required.")
            return redirect("plugins:netbox_ops:home")

        result = engine.propose_vlan(device, vlan=conf.demo_vlan())
        return render(
            request,
            "netbox_ops/_change_result.html",
            {"result": result.to_dict(), "device": device, "demo_vlan": conf.demo_vlan()},
        )


@method_decorator(csrf_protect, name="post")
class ChangeApproveView(ChangeOperatorMixin, View):
    """Apply a staged change, with a named approver.

    The approver is required here as well as in the engine's gate. Two locks, deliberately:
    this is the endpoint a page button reaches, and a missing name must be a refusal at the
    boundary, not a 500 from the layer below.
    """

    def post(self, request, *args, **kwargs):
        device = str(request.POST.get("device") or "").strip()
        approver = str(request.POST.get("approver") or "").strip()

        if not device:
            messages.error(request, "A device is required.")
            return redirect("plugins:netbox_ops:home")
        if not approver:
            return render(
                request,
                "netbox_ops/_change_result.html",
                {
                    "result": {
                        "ok": False,
                        "headline": "An approver is required",
                        "detail": "No change is applied without a named person on the record.",
                    },
                    "device": device,
                    "demo_vlan": conf.demo_vlan(),
                },
                status=400,
            )

        result = engine.approve_vlan(device, approver, vlan=conf.demo_vlan())
        return render(
            request,
            "netbox_ops/_change_result.html",
            {"result": result.to_dict(), "device": device, "demo_vlan": conf.demo_vlan()},
        )


@method_decorator(csrf_protect, name="post")
class DriftInjectView(ChangeOperatorMixin, View):
    """Break a switch on purpose, so the devices view can catch it."""

    def post(self, request, *args, **kwargs):
        device = str(request.POST.get("device") or "").strip()
        if not device:
            messages.error(request, "A device is required.")
            return redirect("plugins:netbox_ops:home")

        result = engine.inject_drift(device)
        return render(
            request,
            "netbox_ops/_change_result.html",
            {"result": result.to_dict(), "device": device, "demo_vlan": conf.demo_vlan()},
        )


@method_decorator(csrf_protect, name="post")
class DriftRemediateView(ChangeOperatorMixin, View):
    """Put back whatever the device is missing, through the same gate."""

    def post(self, request, *args, **kwargs):
        device = str(request.POST.get("device") or "").strip()
        approver = str(request.POST.get("approver") or "").strip()

        if not device or not approver:
            return render(
                request,
                "netbox_ops/_change_result.html",
                {
                    "result": {
                        "ok": False,
                        "headline": "A device and a named approver are required",
                        "detail": "No change is applied without a named person on the record.",
                    },
                    "device": device,
                    "demo_vlan": conf.demo_vlan(),
                },
                status=400,
            )

        result = engine.remediate(device, approver)
        return render(
            request,
            "netbox_ops/_change_result.html",
            {"result": result.to_dict(), "device": device, "demo_vlan": conf.demo_vlan()},
        )


@method_decorator(csrf_protect, name="post")
class LabDeployView(ChangeOperatorMixin, View):
    def post(self, request, *args, **kwargs):
        result = engine.lab_deploy()
        _report(request, result)
        return redirect("plugins:netbox_ops:tab", tab="labs")


@method_decorator(csrf_protect, name="post")
class LabDestroyView(ChangeOperatorMixin, View):
    def post(self, request, *args, **kwargs):
        result = engine.lab_destroy()
        _report(request, result)
        return redirect("plugins:netbox_ops:tab", tab="labs")


def _report(request, result) -> None:
    """Put a lab action's outcome in the message queue, success or failure."""
    if result.ok:
        messages.success(request, result.headline)
    else:
        messages.error(request, f"{result.headline}: {result.detail}")


@method_decorator(csrf_protect, name="post")
class ImportDevicesView(ChangeOperatorMixin, View):
    """Populate NetBox from the fabric topology."""

    def post(self, request, *args, **kwargs):
        result = engine.import_devices()
        _report(request, result)
        return redirect("plugins:netbox_ops:home")


@method_decorator(csrf_protect, name="post")
class ApplyConfigsView(ChangeOperatorMixin, View):
    """Push rendered configs to the devices and prove they took."""

    def post(self, request, *args, **kwargs):
        device = str(request.POST.get("device") or "").strip() or None
        result = engine.apply_configs(device)
        _report(request, result)
        return redirect("plugins:netbox_ops:home")


def _vlan_from(request) -> tuple[int | None, str]:
    """Read the VLAN and its name from a POST, falling back to the configured default.

    Validated here because it reaches a switch: anything outside 2-4094, or anything that is not
    a number, becomes None and the configured default is used instead of being sent.
    """
    raw = str(request.POST.get("vlan") or "").strip()
    vlan: int | None = None
    if raw:
        try:
            value = int(raw)
            if 2 <= value <= 4094:
                vlan = value
        except ValueError:
            vlan = None
    return vlan, str(request.POST.get("vlan_name") or "").strip()


@method_decorator(csrf_protect, name="post")
class BeginChangeView(ChangeOperatorMixin, View):
    """Step 1: capture the before-state and work out the change."""

    def post(self, request, *args, **kwargs):
        device = str(request.POST.get("device") or "").strip()
        if not device:
            messages.error(request, "A device is required.")
            return redirect("plugins:netbox_ops:change")
        return render(
            request,
            "netbox_ops/_flow_result.html",
            {
                "result": engine.begin_change(
                    device, vlan=_vlan_from(request)[0], vlan_name=_vlan_from(request)[1]
                ).to_dict(),
                "device": device,
                "step": "begin",
            },
        )


@method_decorator(csrf_protect, name="post")
class RequestChangeView(ChangeOperatorMixin, View):
    """Step 2: open the ticket that requests the change."""

    def post(self, request, *args, **kwargs):
        device = str(request.POST.get("device") or "").strip()
        requester = str(request.POST.get("requester") or "").strip()
        if not device:
            messages.error(request, "A device is required.")
            return redirect("plugins:netbox_ops:change")
        return render(
            request,
            "netbox_ops/_flow_result.html",
            {
                "result": engine.request_change(
                    device,
                    requester=requester,
                    vlan=_vlan_from(request)[0],
                    vlan_name=_vlan_from(request)[1],
                ).to_dict(),
                "device": device,
                "step": "request",
            },
        )


@method_decorator(csrf_protect, name="post")
class ApproveAndPushView(ChangeOperatorMixin, View):
    """Steps 3-6: approve by name, push, show the config diff, verify."""

    def post(self, request, *args, **kwargs):
        device = str(request.POST.get("device") or "").strip()
        approver = str(request.POST.get("approver") or "").strip()
        ticket = request.POST.get("ticket_id") or None
        if not device:
            messages.error(request, "A device is required.")
            return redirect("plugins:netbox_ops:change")

        try:
            ticket_id = int(ticket) if ticket else None
        except (TypeError, ValueError):
            ticket_id = None

        return render(
            request,
            "netbox_ops/_flow_result.html",
            {
                "result": engine.approve_and_push(
                    device,
                    approver,
                    ticket_id=ticket_id,
                    vlan=_vlan_from(request)[0],
                    vlan_name=_vlan_from(request)[1],
                ).to_dict(),
                "device": device,
                "step": "push",
            },
        )


@method_decorator(csrf_protect, name="post")
class ResetDemoView(ChangeOperatorMixin, View):
    """Put the demo back: remove the added VLAN, clear the snapshot."""

    def post(self, request, *args, **kwargs):
        device = str(request.POST.get("device") or "").strip() or None
        result = engine.reset_demo(device)
        _report(request, result)
        return redirect("plugins:netbox_ops:change")


class TopologyRedirectView(ChangeOperatorMixin, View):
    """Open the topology view with the parameters it actually needs to draw the fabric.

    `netbox_topology_views` reads its display options **only from the URL**, not from its saved
    `IndividualOptions` row — verified in `utils.get_query_settings`, where `show_unconnected`
    starts `False` and is set true only by `request.GET`. With the defaults, a fabric whose cables
    are all correct renders `{"nodes": [], "edges": []}`: a device becomes a node only if it sits
    at the end of a cable, and only when `draw_init` is true.

    So the bare URL the plugin's own menu links to shows an empty canvas, which reads as a broken
    feature. This route carries the parameters that make it work.
    """

    def get(self, request, *args, **kwargs):
        from django.urls import reverse

        base = reverse("plugins:netbox_topology_views:home")
        query = "draw_init=True&show_unconnected=True&show_cables=True&show_neighbors=True"
        return redirect(f"{base}?{query}")


class StatusJsonView(ReadOnlyOperatorMixin, View):
    """A machine-readable health check, so a rehearsal script can ask without scraping."""

    def get(self, request, *args, **kwargs):
        payload = {
            "plugin": "netbox_ops",
            "fabric": engine.fabric_overview().to_dict(),
            "labs": engine.lab_status().to_dict(),
            "awx": engine.awx_status().to_dict(),
            "itsm": engine.itsm_status().to_dict(),
        }
        return JsonResponse(payload)
