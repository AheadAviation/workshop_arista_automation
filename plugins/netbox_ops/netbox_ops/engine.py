"""The seam between the NetBox plugin and the workbench engine (INIT-002/SPEC-001).

The plugin must not import `workbench.web`, `workbench.cli` or anything else that starts a
server or parses argv — a Django app importing a FastAPI app is how you get two event loops
fighting and a NetBox that will not boot. So everything the plugin needs goes through this one
module, which exposes plain functions with plain data in and out.

**Why an adapter at all, rather than the plugin importing `workbench.scenarios` directly.**
Two reasons, both learned the hard way in INIT-001:

1. The engine's `Lab` dataclass injects four callables. A Django view has no business
   constructing that; it should ask for "the fabric" and get data.
2. The engine can fail in ways a web request must not propagate — an unreachable switch, a
   missing container, a dead NetBox. Every function here returns a result shaped for a page,
   never raises for a network reason.

Nothing here writes to the database. Persistence is SPEC-008's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OpsResult:
    """What every adapter call returns.

    `ok` means "the call completed and the answer is meaningful". It does **not** mean the
    news is good: a fabric where nothing matches intent is `ok=True` with findings. A
    failure to look is `ok=False` with a reason, and a page must render those differently —
    that distinction is the whole reason the workbench reports an unreachable device as a
    finding rather than an empty list.
    """

    ok: bool
    data: dict[str, Any]
    headline: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "headline": self.headline,
            "detail": self.detail,
            **self.data,
        }


def _wrap(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> OpsResult:
    """Run an engine function and turn any escape into a page-shaped result.

    The plugin's views call this and never a raw engine function, so a NetBox page cannot
    show a stack trace. The same rule applies as in the workbench's own web layer.
    """
    try:
        payload = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - a page must never 500 on engine trouble
        return OpsResult(
            ok=False,
            data={},
            headline="The operation could not be completed",
            detail=f"{type(exc).__name__}: {exc}",
        )
    return OpsResult(
        ok=bool(payload.get("ok", True)),
        data=payload,
        headline=str(payload.get("headline") or ""),
        detail=str(payload.get("detail") or ""),
    )


# --------------------------------------------------------------------------- #
# The engine, imported lazily
# --------------------------------------------------------------------------- #


def _engine_lab() -> Any:
    """The engine's lab, with its device reader pointed at the Docker HTTP API.

    The engine's default reader shells out to `docker exec`, which is right when the workbench
    runs on the host and wrong inside a NetBox container that has no Docker CLI and must not
    be given one. So the lab is built with the plugin's own reader substituted: same
    comparison, same executors, same gate — only the way state is read differs.

    Imported inside the function so importing this module never pulls in the engine's device
    drivers or its HTTP client.
    """
    from workbench.scenarios import Lab

    from . import devices, eapi_exec

    lab = Lab.real()
    lab.read_actual = devices.read_device  # type: ignore[assignment]
    # The executor is swapped too: `docker exec` is unavailable inside NetBox, and eAPI is the
    # transport the switches actually expose. Same session-based preview-then-commit contract,
    # same refusal to replace a whole configuration.
    lab.preview = eapi_exec.preview  # type: ignore[assignment]
    lab.apply = eapi_exec.apply_change  # type: ignore[assignment]
    return lab


# --------------------------------------------------------------------------- #
# Fabric and devices
# --------------------------------------------------------------------------- #


def fabric_overview(*, new_vlan: int | None = None) -> OpsResult:
    """Every device's intended state, actual state and the difference."""
    from workbench.scenarios import fabric_overview as _fabric_overview

    return _wrap(_fabric_overview, _engine_lab(), new_vlan=new_vlan)


def lifecycle() -> OpsResult:
    """Compliance and operating system per device."""
    from workbench.scenarios import lifecycle as _lifecycle

    return _wrap(_lifecycle, _engine_lab())


def two_vendors() -> OpsResult:
    """The same intent and gate against a second vendor."""
    from workbench.scenarios import two_vendors as _two_vendors

    return _wrap(_two_vendors, _engine_lab())


# --------------------------------------------------------------------------- #
# The day-two change
# --------------------------------------------------------------------------- #


def propose_vlan(device: str, vlan: int = 150) -> OpsResult:
    """Stage a VLAN change on one switch and show its diff. Applies nothing."""
    from workbench.scenarios import propose_vlan as _propose

    return _wrap(_propose, _engine_lab(), device, vlan=vlan)


def approve_vlan(device: str, approver: str, vlan: int = 150) -> OpsResult:
    """Apply a previewed change, with a named human on the record."""
    from workbench.scenarios import approve as _approve

    return _wrap(_approve, _engine_lab(), device, approver=approver, vlan=vlan)


def inject_drift(device: str, vlan: int | None = None) -> OpsResult:
    """Remove an intended VLAN from a trunk, so the page can catch it."""
    from workbench.scenarios import inject_drift as _inject

    return _wrap(_inject, _engine_lab(), device, vlan=vlan)


def remediate(device: str, approver: str) -> OpsResult:
    """Put back whatever the device is missing relative to intent."""
    from workbench.scenarios import remediate as _remediate

    return _wrap(_remediate, _engine_lab(), device, approver=approver)


def audit(limit: int = 50) -> OpsResult:
    """The change record, newest first."""
    from workbench.scenarios import audit_view as _audit

    return _wrap(_audit, _engine_lab(), limit=limit)


# --------------------------------------------------------------------------- #
# Container labs (SPEC-002 provides the implementation this calls)
# --------------------------------------------------------------------------- #


def lab_status() -> OpsResult:
    """The running containerlab topology: nodes, their state, their management addresses."""
    from netbox_ops.labs import lab_status as _lab_status

    return _wrap(_lab_status)


def devices_status() -> OpsResult:
    """Whether the devices are in NetBox, and which configs are rendered."""
    from netbox_ops.devices_admin import device_status, rendered_configs

    status = _wrap(device_status)
    configs = _wrap(rendered_configs)
    return OpsResult(
        ok=status.ok and configs.ok,
        data={
            "devices": status.data,
            "configs": configs.data,
        },
        headline=f"{status.headline} · {configs.headline}",
        detail=status.detail or configs.detail,
    )


def import_devices() -> OpsResult:
    """Populate NetBox from the fabric topology."""
    from netbox_ops.devices_admin import import_fabric

    return _wrap(import_fabric)


def apply_configs(device: str | None = None) -> OpsResult:
    """Push rendered configs to devices and prove they took."""
    from netbox_ops import devices_admin

    if device:
        return _wrap(devices_admin.apply_config, device)
    return _wrap(devices_admin.apply_all)


# --------------------------------------------------------------------------- #
# Live config (the "here it is" view)
# --------------------------------------------------------------------------- #


def live_configs(device: str | None = None) -> OpsResult:
    """Each switch's running config, read live."""
    from netbox_ops import live_config

    if device:
        return _wrap(live_config.running_config, device)
    return _wrap(live_config.running_configs)


def config_diff(device: str) -> OpsResult:
    """What the device's config became, against the snapshot taken before the change."""
    from netbox_ops import live_config

    return _wrap(live_config.diff_against_snapshot, device)


# --------------------------------------------------------------------------- #
# The visible change flow: request -> push -> show
# --------------------------------------------------------------------------- #


def begin_change(device: str) -> OpsResult:
    from netbox_ops import change_flow

    return _wrap(change_flow.begin_change, device)


def request_change(device: str, requester: str = "") -> OpsResult:
    from netbox_ops import change_flow

    return _wrap(change_flow.request_change, device, requester=requester)


def approve_and_push(device: str, approver: str, ticket_id: int | None = None) -> OpsResult:
    from netbox_ops import change_flow

    return _wrap(change_flow.approve_and_push, device, approver, ticket_id=ticket_id)


def reset_demo(device: str | None = None) -> OpsResult:
    from netbox_ops import change_flow

    return _wrap(change_flow.reset, device)


def lab_deploy() -> OpsResult:
    from netbox_ops.labs import deploy as _deploy

    return _wrap(_deploy)


def lab_destroy() -> OpsResult:
    from netbox_ops.labs import destroy as _destroy

    return _wrap(_destroy)


# --------------------------------------------------------------------------- #
# AWX and ITSM (SPEC-007 provides these)
# --------------------------------------------------------------------------- #


def awx_status() -> OpsResult:
    from netbox_ops.awx import status as _status

    return _wrap(_status)


def awx_job_templates() -> OpsResult:
    from netbox_ops.awx import job_templates as _templates

    return _wrap(_templates)


def itsm_status() -> OpsResult:
    from netbox_ops.itsm import status as _status

    return _wrap(_status)


def itsm_tickets(limit: int = 25) -> OpsResult:
    from netbox_ops.itsm import tickets as _tickets

    return _wrap(_tickets, limit=limit)


__all__ = [
    "OpsResult",
    "approve_vlan",
    "audit",
    "awx_job_templates",
    "awx_status",
    "fabric_overview",
    "inject_drift",
    "itsm_status",
    "itsm_tickets",
    "lab_deploy",
    "lab_destroy",
    "lab_status",
    "lifecycle",
    "propose_vlan",
    "remediate",
    "two_vendors",
]
