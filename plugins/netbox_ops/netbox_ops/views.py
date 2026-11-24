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
        ("workshop", "Workshop", "workshop"),
        ("devices", "Devices", "devices"),
        ("actions", "Day-two actions", "actions"),
        ("live", "Live config", "live"),
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
        if tab == "workshop":
            context["workshop"] = engine.workshop_status().to_dict()
        elif tab == "devices":
            context["fabric"] = engine.fabric_overview().to_dict()
            context["setup"] = engine.devices_status().to_dict()
        elif tab == "actions":
            # The interactive Day-two actions page fetches its own live NetBox catalogue via JSON;
            # skipping `fabric_overview()` avoids 10s of sequential eAPI polling on tab open.
            pass
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

        # Refuse before reaching the engine, so the reason is rendered next to the button
        # instead of buried. The engine refuses too; this is the second lock, not the only one.
        if not ticket_id:
            return render(
                request,
                "netbox_ops/_flow_result.html",
                {
                    "result": {
                        "ok": False,
                        "headline": "A change ticket is required",
                        "detail": (
                            "Choose the ticket this change was requested under, or use step 2 "
                            "to open one. Nothing is sent to a device without a change record."
                        ),
                    },
                    "device": device,
                    "step": "push",
                },
                status=400,
            )

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


class OpsCatalogueJsonView(ReadOnlyOperatorMixin, View):
    """Every operation, with each field's options read live from IPAM.

    The page fetches this once on load and re-renders the form when the operator changes
    operation. Returning the options *with* the catalogue means no per-field round trip, which
    matters when the whole point is that the form responds as you go.
    """

    def get(self, request, *args, **kwargs):
        result = engine.operations_catalogue()
        return JsonResponse(result.to_dict())


@method_decorator(csrf_protect, name="post")
class OperationResolveView(ChangeOperatorMixin, View):
    """Resolve submitted values without previewing anything.

    Separate from the preview so the page can show the resolved sentence — "Give the new storage
    array STORAGE on MLAG pair 1, at MTU 9214" — before it does any work against a switch. That
    sentence is what the operator reads to check they asked for the right thing.
    """

    def post(self, request, *args, **kwargs):
        operation = str(request.POST.get("operation") or "").strip()
        values = {k: v for k, v in request.POST.items() if k not in ("csrfmiddlewaretoken", "operation")}
        result = engine.resolve_operation(operation, values)
        if not result.ok:
            return JsonResponse(result.to_dict(), status=400)
        return JsonResponse(result.to_dict())


@method_decorator(csrf_protect, name="post")
class OperationPreviewView(ChangeOperatorMixin, View):
    """Resolve an operation and render its diff, on every switch it touches."""

    def post(self, request, *args, **kwargs):
        operation = str(request.POST.get("operation") or "").strip()
        values = {k: v for k, v in request.POST.items() if k not in ("csrfmiddlewaretoken", "operation")}
        result = engine.plan_operation(operation, values)
        status = 200 if result.ok else 400
        return JsonResponse(result.to_dict(), status=status)


@method_decorator(csrf_protect, name="post")
class OperationApplyView(ChangeOperatorMixin, View):
    """Apply a previewed operation, with a named approver on the record."""

    def post(self, request, *args, **kwargs):
        operation = str(request.POST.get("operation") or "").strip()
        change_id = str(request.POST.get("change_id") or "").strip()
        approver = str(request.POST.get("approver") or "").strip()

        if not approver:
            return JsonResponse(
                {
                    "ok": False,
                    "headline": "An approver is required",
                    "detail": "no change is applied without a named person on the record",
                },
                status=400,
            )
        if not change_id:
            return JsonResponse(
                {
                    "ok": False,
                    "headline": "Preview the change first",
                    "detail": "an approval only applies to a change that was just previewed",
                },
                status=400,
            )

        netbox_only = str(request.POST.get("netbox_only") or "0").strip().lower() in ("1", "true", "yes")
        result = engine.apply_operation(operation, {}, approver, change_id, netbox_only=netbox_only)
        status = 200 if result.ok else 400
        return JsonResponse(result.to_dict(), status=status)


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
            "workshop": engine.workshop_status().to_dict(),
        }
        return JsonResponse(payload)


# --------------------------------------------------------------------------- #
# Guided Workshop Quick Start & Day-2 Operations views (INIT-004/SPEC-003)
# --------------------------------------------------------------------------- #


def _render_workshop_result(request, title: str, result: engine.OpsResult, *, kind: str = "generic"):
    """Render any workshop action result cleanly inside NetBox's layout."""
    return render(
        request,
        "netbox_ops/_workshop_result.html",
        {
            "title": title,
            "kind": kind,
            "result": result.to_dict(),
        },
    )


@method_decorator(csrf_protect, name="post")
class WorkshopCleanView(ChangeOperatorMixin, View):
    """Step 1: destroy lab, delete generated files, and optionally wipe NetBox fabric data."""

    def post(self, request, *args, **kwargs):
        clear_netbox = str(request.POST.get("clear_netbox", "1")).strip().lower() not in ("0", "false", "no")
        result = engine.workshop_clean(clear_netbox=clear_netbox)
        return _render_workshop_result(request, "Step 1 — Clean Slate", result)


@method_decorator(csrf_protect, name="post")
class WorkshopImportView(ChangeOperatorMixin, View):
    """Step 3: import the fabric topology into NetBox via `scripts/full_import.py`."""

    def post(self, request, *args, **kwargs):
        result = engine.workshop_import()
        return _render_workshop_result(request, "Step 3 — Import Network Data into NetBox", result)


@method_decorator(csrf_protect, name="post")
class WorkshopTopologyGenView(ChangeOperatorMixin, View):
    """Step 4: generate `clab/topology-docker.clab.yml` from NetBox."""

    def post(self, request, *args, **kwargs):
        result = engine.workshop_generate_topology()
        return _render_workshop_result(request, "Step 4 — Generate ContainerLab Topology", result)


@method_decorator(csrf_protect, name="post")
class WorkshopConfigsGenView(ChangeOperatorMixin, View):
    """Step 5 / Day 2: generate all 6 Arista EOS configs via Ansible."""

    def post(self, request, *args, **kwargs):
        result = engine.workshop_generate_configs()
        return _render_workshop_result(request, "Step 5 — Generate Device Configurations", result)


@method_decorator(csrf_protect, name="post")
class WorkshopDeployView(ChangeOperatorMixin, View):
    """Step 6: deploy the generated `topology-docker.clab.yml` in ContainerLab."""

    def post(self, request, *args, **kwargs):
        result = engine.workshop_deploy()
        return _render_workshop_result(request, "Step 6 — Deploy ContainerLab Fabric", result)


@method_decorator(csrf_protect, name="post")
class WorkshopDestroyView(ChangeOperatorMixin, View):
    """Cleanup: destroy the running ContainerLab fabric."""

    def post(self, request, *args, **kwargs):
        result = engine.workshop_destroy()
        return _render_workshop_result(request, "Destroy ContainerLab Fabric", result)


@method_decorator(csrf_protect, name="post")
class WorkshopPushView(ChangeOperatorMixin, View):
    """Step 7 / Day 2: push full rendered configs to all 6 switches."""

    def post(self, request, *args, **kwargs):
        result = engine.workshop_push_configs()
        return _render_workshop_result(
            request, "Step 7 — Push Full Configurations", result, kind="devices"
        )


class WorkshopVerifyView(ReadOnlyOperatorMixin, View):
    """Step 8: live BGP, MLAG, VXLAN, EVPN, VLAN/VRF, and fabric ping checks over eAPI."""

    def get(self, request, *args, **kwargs):
        result = engine.workshop_verify()
        return _render_workshop_result(
            request, "Step 8 — Live Fabric Verification", result, kind="checks"
        )

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)


@method_decorator(csrf_protect, name="post")
class WorkshopValidateView(ChangeOperatorMixin, View):
    """Step 9 / Day 2: run `playbooks/test_all.yml` (full or quick)."""

    def post(self, request, *args, **kwargs):
        quick = str(request.POST.get("quick", "0")).strip().lower() in ("1", "true", "yes")
        result = engine.workshop_validate(quick=quick)
        title = "Day 2 — Quick Validation Suite" if quick else "Step 9 — Full Validation Suite"
        return _render_workshop_result(request, title, result)


@method_decorator(csrf_protect, name="post")
class WorkshopBackupView(ChangeOperatorMixin, View):
    """Day 2: save running-config to `flash:backup.cfg` on all 6 switches."""

    def post(self, request, *args, **kwargs):
        result = engine.workshop_backup()
        return _render_workshop_result(
            request, "Day 2 — Backup Switch Configs", result, kind="devices"
        )


@method_decorator(csrf_protect, name="post")
class WorkshopRollbackView(ChangeOperatorMixin, View):
    """Day 2: restore `flash:backup.cfg` on all 6 switches via `configure replace`."""

    def post(self, request, *args, **kwargs):
        result = engine.workshop_rollback()
        return _render_workshop_result(
            request, "Day 2 — Rollback Switch Configs", result, kind="devices"
        )


class WorkshopDiffView(ReadOnlyOperatorMixin, View):
    """Day 2: diff each switch's live running config against its generated config."""

    def get(self, request, *args, **kwargs):
        result = engine.workshop_diffs()
        return _render_workshop_result(
            request, "Day 2 — Running vs. Generated Config Diff", result, kind="diffs"
        )


class WorkshopTopologyInspectView(ReadOnlyOperatorMixin, View):
    """Step 4: inspect generated `topology-docker.clab.yml`."""

    def get(self, request, *args, **kwargs):
        result = engine.workshop_read_topology()
        return _render_workshop_result(
            request, "Generated ContainerLab Topology", result, kind="file"
        )


class WorkshopConfigInspectView(ReadOnlyOperatorMixin, View):
    """Step 5: inspect one generated device `.cfg` file."""

    def get(self, request, device: str, *args, **kwargs):
        result = engine.workshop_read_config(device)
        return _render_workshop_result(
            request, f"Generated Config — {device}", result, kind="file"
        )


class WorkshopStatusJsonView(ReadOnlyOperatorMixin, View):
    """Fast live status JSON for Workshop KPIs & step badges (no AWX/ITSM overhead)."""

    def get(self, request, *args, **kwargs):
        return JsonResponse(engine.workshop_status().to_dict())


@method_decorator(csrf_protect, name="post")
class WorkshopStandardConfigView(ChangeOperatorMixin, View):
    """Step 10: preview, save to NetBox only, or apply standard fabric baseline (Syslog, SNMP, NTP, DNS, LLDP, Hardening)."""

    def post(self, request, *args, **kwargs):
        mode = str(request.POST.get("mode") or "apply").strip().lower()
        params = {
            k: v
            for k, v in request.POST.items()
            if k not in ("csrfmiddlewaretoken", "mode")
        }
        if mode == "preview":
            result = engine.workshop_preview_standard_config(params)
            return _render_workshop_result(
                request,
                "Step 10 — Preview Standard Fabric Config",
                result,
                kind="diffs",
            )
        if mode == "netbox_only":
            result = engine.workshop_apply_standard_config(params, netbox_only=True)
            return _render_workshop_result(
                request,
                "Step 10 — Standard Fabric Config (Saved to NetBox Only)",
                result,
                kind="generic",
            )
        result = engine.workshop_apply_standard_config(params, netbox_only=False)
        return _render_workshop_result(
            request,
            "Step 10 — Apply Standard Fabric Config",
            result,
            kind="devices",
        )
