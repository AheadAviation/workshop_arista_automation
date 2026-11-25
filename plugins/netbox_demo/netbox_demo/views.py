"""Plugin views: the demo runbook, drift review, Day 2 and job history (SPEC-002).

Every lab action follows the same shape so the page never lies about state:

1. A `LabJob` row is created *before* the call, status `running`.
2. `lab.run_action` is called.
3. The same row is updated with the outcome and its output.

That ordering means a crash mid-action leaves a `running` row rather than nothing,
which is the honest record. Views that only read never write.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from django.contrib import messages
from django.db import close_old_connections
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import assurance, conf, day2, design, intent, lab, renderer, seeder
from .models import DemoStep, LabAgent, LabJob, StagedChange

#: The runbook, in order. `key` is stable; `action` is a lab-agent action.
RUNBOOK: tuple[dict[str, Any], ...] = (
    {
        "key": "destroy",
        "label": "Destroy the lab",
        "action": "destroy-lab",
        "detail": "Tears down the fabric and wipes persisted switch state.",
    },
    {
        "key": "deploy_bare",
        "label": "Rebuild it bare",
        "action": "deploy-bare-lab",
        "detail": "Six switches with management, eAPI, SNMP and LLDP only. No fabric intent.",
    },
    {
        "key": "discover",
        "label": "Discover them into NetBox",
        "action": "run-discovery",
        "detail": "The local Orb Agent reads the switches and streams them to NetBox Cloud.",
    },
    {
        "key": "seed",
        "label": "Document the fabric",
        "action": "",
        "detail": "Adds cabling, tenants, VLANs and the service baseline to NetBox.",
    },
    {
        "key": "push",
        "label": "Render and push configuration",
        "action": "push-configs",
        "detail": "Builds EOS config from NetBox intent and applies it to all six switches.",
    },
    {
        "key": "verify",
        "label": "Check the fabric is healthy",
        "action": "validate-fabric",
        "detail": "Reads BGP EVPN, VXLAN, MLAG and VLAN state from every switch.",
    },
    {
        "key": "assurance",
        "label": "Confirm there is no drift",
        "action": "",
        "detail": "Review what Assurance found. Nothing reaches NetBox until you apply it.",
    },
    {
        "key": "rogue",
        "label": "Show drift being caught",
        "action": "inject-rogue-drift",
        "detail": "Injects unauthorized changes on a leaf, so Assurance can catch them live.",
    },
)


def _save_step(key: str, *, ok: bool, detail: str = "") -> None:
    DemoStep.objects.update_or_create(
        key=key,
        defaults={
            "label": next((s["label"] for s in RUNBOOK if s["key"] == key), key),
            "done": ok,
            "detail": detail[:255],
            "completed_at": timezone.now() if ok else None,
        },
    )


def _steps() -> list[dict[str, Any]]:
    done = {step.key: step for step in DemoStep.objects.all()}
    rows = []
    for step in RUNBOOK:
        row = dict(step)
        record = done.get(step["key"])
        row["done"] = bool(record and record.done)
        row["completed_at"] = record.completed_at if record else None
        row["detail_recorded"] = record.detail if record else ""
        rows.append(row)
    return rows


#: Actions whose wall time can exceed reverse-proxy (Cloudflare 100s) timeouts.
#: Run in a background thread and redirect immediately to the auto-refreshing job page.
ASYNC_ACTIONS: frozenset[str] = frozenset({"deploy-bare-lab"})


def _finish_job(job_id: int, action: str, payload: dict | None, *, step_key: str = "") -> tuple[LabJob, str]:
    job = LabJob.objects.get(pk=job_id)
    try:
        result = lab.run_action(action, payload)
    except lab.LabAgentError as exc:
        job.status = LabJob.Status.FAILED
        job.error = str(exc)
        job.save(update_fields=["status", "error"])
        if step_key:
            _save_step(step_key, ok=False, detail=str(exc))
        return job, str(exc)

    job.status = LabJob.Status.COMPLETED
    job.output = str(result.get("output", ""))[:20000]
    job.summary = {
        key: value
        for key, value in result.items()
        if key
        in (
            "devices",
            "results",
            "wiped",
            "mode",
            "scenario",
            "title",
            "configs_written",
            "eapi_ready",
            "eapi_wait_seconds",
        )
    }
    job.save(update_fields=["status", "output", "summary"])
    if step_key:
        _save_step(step_key, ok=True, detail=str(result.get("output", ""))[:120])
    return job, str(result.get("output", ""))


def _start_async_job(action: str, payload: dict | None = None, *, step_key: str = "") -> LabJob:
    agent = LabAgent.objects.filter(enabled=True).first()
    job = LabJob.objects.create(
        agent=agent, action=action, status=LabJob.Status.RUNNING, payload=payload or {}
    )

    def _worker() -> None:
        close_old_connections()
        try:
            _finish_job(job.pk, action, payload, step_key=step_key)
        except Exception as exc:  # noqa: BLE001
            LabJob.objects.filter(pk=job.pk).update(status=LabJob.Status.FAILED, error=str(exc))
        finally:
            close_old_connections()

    threading.Thread(target=_worker, name=f"netbox-demo-job-{job.pk}", daemon=True).start()
    return job


def _run_job(action: str, payload: dict | None = None, *, step_key: str = "") -> tuple[LabJob, str]:
    """Create, run and finish one job. Returns the job and a human-readable message."""
    agent = LabAgent.objects.filter(enabled=True).first()
    job = LabJob.objects.create(
        agent=agent, action=action, status=LabJob.Status.RUNNING, payload=payload or {}
    )
    return _finish_job(job.pk, action, payload, step_key=step_key)


# --------------------------------------------------------------------------- #
# Runbook
# --------------------------------------------------------------------------- #


@require_GET
def runbook(request: HttpRequest) -> HttpResponse:
    health = lab.health_or_none()
    configured, config_problem = conf.is_configured()

    return render(
        request,
        "netbox_demo/runbook.html",
        {
            "steps": _steps(),
            "health": health,
            "configured": configured,
            "config_problem": config_problem,
            "agent_url": conf.lab_agent_url(),
            "assurance": assurance.summary(),
            "devices_documented": len(intent.demo_devices()),
            "devices_expected": len(design.ALL_DEVICES),
            "missing_devices": intent.missing_devices(),
            "recent_jobs": LabJob.objects.all()[:8],
            "tab": "runbook",
        },
    )


@require_POST
def run_step(request: HttpRequest, key: str) -> HttpResponse:
    """Run one runbook step. Each key maps to a fixed action; nothing is user-supplied."""
    step = next((s for s in RUNBOOK if s["key"] == key), None)
    if step is None:
        messages.error(request, f"Unknown demo step {key!r}.")
        return redirect("plugins:netbox_demo:runbook")

    # Steps that only touch NetBox run in-process; no lab agent needed.
    if key == "seed":
        try:
            summary = seeder.seed_all()
        except Exception as exc:  # noqa: BLE001 - shown to the operator
            messages.error(request, f"Documenting the fabric failed: {exc}")
            _save_step(key, ok=False, detail=str(exc))
            return redirect("plugins:netbox_demo:runbook")
        _save_step(
            key,
            ok=True,
            detail=f"{len(summary['devices'])} devices, {len(summary['vlans'])} VLANs, "
            f"{summary['cables']} cables",
        )
        messages.success(
            request,
            f"Documented {len(summary['devices'])} devices, {len(summary['vlans'])} VLANs and "
            f"{summary['cables']} new cables in NetBox.",
        )
        return redirect("plugins:netbox_demo:runbook")

    if key == "push":
        return push_configs(request)

    if key == "assurance":
        # Nothing to run: Assurance does its own analysis. Send the operator to it.
        return redirect(conf.assurance_deviations_url())

    if step["action"] in ASYNC_ACTIONS:
        job = _start_async_job(step["action"], step_key=key)
        messages.info(
            request,
            f"{step['label']} started in background (Job #{job.pk}). This page refreshes automatically.",
        )
        return redirect(f"/plugins/demo/jobs/{job.pk}/")

    job, output = _run_job(step["action"], step_key=key)
    if job.status == LabJob.Status.FAILED:
        messages.error(request, f"{step['label']} failed: {output}")
    else:
        messages.success(request, f"{step['label']}: done.")
    return redirect(f"/plugins/demo/jobs/{job.pk}/")


@require_GET
def runbook_status(request: HttpRequest) -> JsonResponse:
    """Small JSON polled by the page so KPI cards and step badges stay live."""
    health = lab.health_or_none()
    documented = len(intent.demo_devices())
    expected = len(design.ALL_DEVICES)
    assur = assurance.summary()
    return JsonResponse(
        {
            "steps": [
                {
                    "key": row["key"],
                    "label": row["label"],
                    "done": row["done"],
                    "detail": row["detail_recorded"],
                }
                for row in _steps()
            ],
            "agent_reachable": health is not None,
            "devices_documented": documented,
            "devices_expected": expected,
            "running_devices": int(health.get("running_devices", 0)) if health else 0,
            "expected_devices": int(health.get("expected_devices", expected)) if health else expected,
            "orb_running": bool((health or {}).get("orb", {}).get("running", False)),
            "assurance_available": bool(assur.get("available", False)),
            "assurance_open": int(assur.get("open", 0)) if assur.get("available") else 0,
        }
    )


@require_POST
def reset_runbook(request: HttpRequest) -> HttpResponse:
    """Clear progress only. Does not touch the lab or NetBox data."""
    DemoStep.objects.all().delete()
    messages.success(request, "Demo progress cleared. The lab and NetBox data are untouched.")
    return redirect("plugins:netbox_demo:runbook")


# --------------------------------------------------------------------------- #
# Drift / Assurance
# --------------------------------------------------------------------------- #


@require_GET
def drift(request: HttpRequest) -> HttpResponse:
    """Assurance summary, plus live running-config drift for one switch on demand."""
    device = request.GET.get("device", "")
    items: list[day2.DriftItem] = []
    drift_error = ""
    running_excerpt = ""

    if device:
        if device not in design.ALL_DEVICES:
            drift_error = f"Unknown device {device!r}."
        else:
            try:
                live = lab.eapi_show(device, list(day2.DRIFT_READ_COMMANDS))
                running = lab.extract_eapi_text(live)
                running_excerpt = running
                rendered = renderer.render_device(intent.intent_for(device), **intent.render_kwargs())
                items = day2.detect_drift(device, rendered, running or "")
            except lab.LabAgentError as exc:
                drift_error = str(exc)

    return render(
        request,
        "netbox_demo/drift.html",
        {
            "assurance": assurance.summary(),
            "claim": assurance.zero_drift_claim(),
            "devices": design.ALL_DEVICES,
            "selected": device,
            "items": items,
            "summary": day2.summarize_drift(items) if items else None,
            "drift_error": drift_error,
            "running_excerpt": running_excerpt[:8000],
            "tab": "drift",
        },
    )


@require_POST
def inject_drift(request: HttpRequest) -> HttpResponse:
    scenario = request.POST.get("scenario", "multi_drift")
    job, output = _run_job("inject-rogue-drift", {"scenario": scenario}, step_key="rogue")
    if job.status == LabJob.Status.FAILED:
        messages.error(request, f"Injecting drift failed: {output}")
        return redirect("plugins:netbox_demo:drift")
    messages.success(
        request,
        "Drift injected on a leaf. Run discovery, then review Assurance to see it caught.",
    )
    return redirect(f"/plugins/demo/jobs/{job.pk}/")


@require_POST
def remediate_drift(request: HttpRequest) -> HttpResponse:
    scenario = request.POST.get("scenario", "multi_drift")
    job, output = _run_job("remediate-drift", {"scenario": scenario})
    if job.status == LabJob.Status.FAILED:
        messages.error(request, f"Removing drift failed: {output}")
    else:
        messages.success(request, "Drift removed. Run discovery again to see it clear.")
    return redirect(f"/plugins/demo/jobs/{job.pk}/")


@require_POST
def assurance_apply(request: HttpRequest) -> HttpResponse:
    """Apply all open demo-scoped deviations in NetBox Assurance."""
    result = assurance.apply_deviations(None, demo_only=True)
    if not result.get("ok"):
        messages.error(request, result.get("error", "Applying deviations failed."))
    else:
        count = result.get("count", 0)
        _save_step("assurance", ok=True, detail=f"Applied {count} demo deviation(s)")
        messages.success(
            request,
            f"Submitted {count} demo deviation(s) to NetBox Assurance for apply.",
        )
    return redirect("plugins:netbox_demo:drift")


@require_POST
def assurance_rediff(request: HttpRequest) -> HttpResponse:
    """Re-diff all open demo-scoped deviations in NetBox Assurance."""
    result = assurance.rediff_deviations(None, demo_only=True)
    if not result.get("ok"):
        messages.error(request, result.get("error", "Re-diffing deviations failed."))
    else:
        messages.success(
            request,
            f"Queued {result.get('count', 0)} demo deviation(s) for re-diff in NetBox Assurance.",
        )
    return redirect("plugins:netbox_demo:drift")


# --------------------------------------------------------------------------- #
# Day 2
# --------------------------------------------------------------------------- #


@require_GET
def day2_view(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "netbox_demo/day2.html",
        {
            "operations": day2.OPERATIONS,
            "devices": design.LEAVES,
            "server_port": design.SERVER_PORT,
            "staged": StagedChange.objects.all()[:10],
            "vlans": intent.tenant_summary(),
            "tab": "day2",
        },
    )


@require_POST
def day2_preview(request: HttpRequest) -> HttpResponse:
    """Stage a change and show exactly what would happen. Changes nothing."""
    operation = request.POST.get("operation", "")
    values: dict[str, Any] = {
        key: request.POST.get(key)
        for key in ("vlan_id", "name", "svi", "mtu", "device", "port", "description", "enabled")
        if request.POST.get(key) not in (None, "")
    }
    try:
        planned = day2.plan(operation, values)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("plugins:netbox_demo:day2")

    staged = StagedChange.objects.create(
        change_id=uuid.uuid4().hex[:12],
        operation=planned.operation,
        device=", ".join(planned.devices),
        values=planned.values,
        netbox_plan={"steps": planned.netbox_steps, "warnings": planned.warnings},
        eos_commands=planned.eos_commands,
        preview=planned.preview(),
    )
    return render(
        request,
        "netbox_demo/day2_preview.html",
        {"staged": staged, "plan": planned, "tab": "day2"},
    )


@require_POST
def day2_save_only(request: HttpRequest, change_id: str) -> HttpResponse:
    """Write the NetBox half of a staged change and show the resulting drift."""
    staged = get_object_or_404(StagedChange, change_id=change_id)
    try:
        _apply_netbox_half(staged)
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        messages.error(request, f"Could not write to NetBox: {exc}")
        return redirect("plugins:netbox_demo:day2")

    staged.delete()
    messages.success(
        request,
        "Saved to NetBox. The switches have not changed, so they now disagree with intent — "
        "open Drift to see exactly where.",
    )
    return redirect(f"{'/plugins/demo/drift/'}?device={design.LEAVES[0]}")


@require_POST
def day2_apply(request: HttpRequest, change_id: str) -> HttpResponse:
    """Write NetBox, then push the switch commands. Requires an explicit approval."""
    staged = get_object_or_404(StagedChange, change_id=change_id)
    approver = (request.POST.get("approver") or "").strip()
    if not approver:
        messages.error(request, "An approver name is required before changes reach a switch.")
        return redirect("plugins:netbox_demo:day2")

    try:
        _apply_netbox_half(staged)
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f"Could not write to NetBox, so nothing was pushed: {exc}")
        return redirect("plugins:netbox_demo:day2")

    job = LabJob.objects.create(
        agent=LabAgent.objects.filter(enabled=True).first(),
        action="eapi",
        status=LabJob.Status.RUNNING,
        payload={"change_id": change_id, "approver": approver, "operation": staged.operation},
    )
    failures: list[str] = []
    for device, commands in staged.eos_commands.items():
        try:
            lab.eapi_config(device, commands)
        except lab.LabAgentError as exc:
            failures.append(f"{device}: {exc}")

    if failures:
        job.status = LabJob.Status.FAILED
        job.error = "\n".join(failures)
        job.save(update_fields=["status", "error"])
        messages.error(
            request,
            f"NetBox was updated but {len(failures)} switch(es) failed: {'; '.join(failures)}",
        )
        return redirect(f"/plugins/demo/jobs/{job.pk}/")

    job.status = LabJob.Status.COMPLETED
    job.output = f"Applied {staged.operation} to {len(staged.eos_commands)} device(s), approved by {approver}."
    job.summary = {"devices": sorted(staged.eos_commands), "approver": approver}
    job.save(update_fields=["status", "output", "summary"])
    staged.delete()
    messages.success(
        request,
        f"Applied to {len(job.summary['devices'])} switch(es). Run discovery and review Assurance "
        "to confirm the drift is gone.",
    )
    return redirect(f"/plugins/demo/jobs/{job.pk}/")


def _apply_netbox_half(staged: StagedChange) -> None:
    """Write the NetBox side of a staged change. Raises on failure."""
    from ipam.models import VLAN
    from tenancy.models import Tenant

    values = staged.values
    if staged.operation == "add_vlan":
        vlan, _ = VLAN.objects.update_or_create(
            vid=int(values["vlan_id"]),
            defaults={
                "name": values["name"],
                "description": f"netbox-demo Day 2 ({values['vni']})",
                "tenant": Tenant.objects.filter(slug="core").first(),
                "status": "active",
            },
        )
        vlan.custom_field_data.update(
            {"demo_tenant": True, "demo_vni": values["vni"], "demo_svi": values["svi"]}
        )
        vlan.save()
    elif staged.operation == "remove_vlan":
        VLAN.objects.filter(vid=int(values["vlan_id"])).delete()
    elif staged.operation == "set_server_port":
        from dcim.models import Device, Interface

        device = Device.objects.filter(name=values["device"]).first()
        if device is None:
            raise ValueError(f"device {values['device']} is not in NetBox; seed the fabric first")
        port, _ = Interface.objects.get_or_create(
            device=device, name=values["port"], defaults={"enabled": True}
        )
        port.description = values.get("description", "")
        port.enabled = bool(values.get("enabled", True))
        vlan = VLAN.objects.filter(vid=int(values["vlan_id"])).first()
        if vlan is not None:
            port.untagged_vlan = vlan
        port.save()
    elif staged.operation == "apply_baseline":
        seeder.ensure_config_context()


# --------------------------------------------------------------------------- #
# Jobs and agents
# --------------------------------------------------------------------------- #


@require_GET
def job_list(request: HttpRequest) -> HttpResponse:
    jobs = LabJob.objects.order_by("-created")[:100]
    counts = LabJob.objects.values("status").annotate(n=Count("id"))
    return render(
        request,
        "netbox_demo/jobs.html",
        {"jobs": jobs, "status_counts": {row["status"]: row["n"] for row in counts}, "tab": "jobs"},
    )


@require_GET
def job_detail(request: HttpRequest, pk: int) -> HttpResponse:
    job = get_object_or_404(LabJob, pk=pk)
    return render(request, "netbox_demo/job_detail.html", {"job": job, "tab": "jobs"})


@require_GET
def live_config(request: HttpRequest) -> HttpResponse:
    """Read a switch's running config through the lab agent, for the live tab."""
    device = request.GET.get("device", design.LEAVES[0])
    config_text = ""
    error = ""
    if device not in design.ALL_DEVICES:
        error = f"Unknown device {device!r}."
    else:
        try:
            result = lab.eapi_show(device, ["show running-config"])
            config_text = lab.extract_eapi_text(result)
        except lab.LabAgentError as exc:
            error = str(exc)

    return render(
        request,
        "netbox_demo/live.html",
        {
            "devices": design.ALL_DEVICES,
            "selected": device,
            "config_text": config_text,
            "error": error,
            "tab": "live",
        },
    )


@require_GET
def preview_render(request: HttpRequest) -> HttpResponse:
    """Show the exact config that would be pushed, without pushing it."""
    device = request.GET.get("device", design.LEAVES[0])
    config_text = ""
    problems: list[str] = []
    if device not in design.ALL_DEVICES:
        problems.append(f"Unknown device {device!r}.")
    elif intent.missing_devices():
        problems.append(
            "These devices are not in NetBox yet: "
            + ", ".join(intent.missing_devices())
            + ". Run discovery and document the fabric first."
        )
    else:
        intents = intent.intent_for_all()
        configs = renderer.render_all(intents, **intent.render_kwargs())
        problems = renderer.sanity_check(configs)
        config_text = configs.get(device, "")

    return render(
        request,
        "netbox_demo/preview.html",
        {
            "devices": design.ALL_DEVICES,
            "selected": device,
            "config_text": config_text,
            "problems": problems,
            "tab": "preview",
        },
    )


@require_POST
def push_configs(request: HttpRequest) -> HttpResponse:
    """Render from NetBox intent and push to every switch."""
    if intent.missing_devices():
        messages.error(
            request,
            "Not every device is documented yet: "
            + ", ".join(intent.missing_devices())
            + ". Run discovery and document the fabric first.",
        )
        return redirect("plugins:netbox_demo:runbook")

    configs = renderer.render_all(intent.intent_for_all(), **intent.render_kwargs())
    problems = renderer.sanity_check(configs)
    if problems:
        messages.error(request, "Refusing to push: " + "; ".join(problems))
        return redirect("plugins:netbox_demo:preview")

    job, output = _run_job("push-configs", {"configs": configs}, step_key="push")
    if job.status == LabJob.Status.FAILED:
        messages.error(request, f"Push failed: {output}")
    else:
        messages.success(request, f"Pushed configuration to {len(configs)} switches.")
    return redirect(f"/plugins/demo/jobs/{job.pk}/")


@require_POST
def seed_fabric(request: HttpRequest) -> HttpResponse:
    try:
        summary = seeder.seed_all()
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f"Seeding failed: {exc}")
        _save_step("seed", ok=False, detail=str(exc))
        return redirect("plugins:netbox_demo:runbook")
    _save_step("seed", ok=True, detail=f"{len(summary['devices'])} devices")
    messages.success(request, f"Documented {len(summary['devices'])} devices and {summary['cables']} cables.")
    return redirect("plugins:netbox_demo:runbook")


@require_POST
def clean_fabric(request: HttpRequest) -> HttpResponse:
    """Delete the demo data from NetBox. Deliberately scoped and explicit."""
    removed = seeder.clean_demo_data()
    DemoStep.objects.all().delete()
    messages.success(
        request,
        "Removed from NetBox: "
        + ", ".join(f"{count} {name.replace('_', ' ')}" for name, count in removed.items()),
    )
    return redirect("plugins:netbox_demo:runbook")


@require_GET
def agent_health(request: HttpRequest) -> JsonResponse:
    health = lab.health_or_none()
    if health is None:
        return JsonResponse(
            {"reachable": False, "url": conf.lab_agent_url(), "error": "lab agent unreachable"},
            status=503,
        )
    return JsonResponse({"reachable": True, **health})
