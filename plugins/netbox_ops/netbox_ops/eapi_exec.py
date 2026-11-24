"""Preview and apply a change over eAPI (INIT-002/SPEC-002).

**What works over eAPI, established by experiment rather than assumed:**

* Plain `configure` → commands → `end` → `write memory` **works**. Verified: a VLAN created
  this way exists on the switch afterwards.
* A **configuration session does not work.** eAPI runs each `runCmds` request in its own CLI
  context, so `configure session X` in one request is gone by the next; and putting the whole
  session (load, `show session-config diffs`, `commit`) into a *single* request is accepted
  with no error and applies **nothing** — the worst possible failure mode, because it looks
  like success.

So the preview is built the honest way instead: read the device's current state, compute what
the change would produce, and report the difference. That is a real preview of a real change —
it is just computed from the device's answer rather than from a session diff. Apply then
performs the change for real and reads the device back to confirm it landed.

**The safety property is unchanged and is the important part:** the change is never loaded into
an empty configuration. A targeted change therefore cannot replace the device's running config,
which is the failure that would take a switch off the network.

`replace` mode is refused outright. Nothing in this demo needs it, and over eAPI it would be a
full-configuration load with no session to protect it.
"""

from __future__ import annotations

import json
from typing import Any

from workbench.contracts import ContractError, Plan, Preview

from . import conf

_SAFE_DEVICE = set("abcdefghijklmnopqrstuvwxyz0123456789-")


class ApplyError(RuntimeError):
    """The device refused the change, or could not be reached."""


def _eapi(
    device: str,
    commands: list[str],
    *,
    timeout: float = 90.0,
    fmt: str = "text",
) -> list[str]:
    """Send commands to a switch over eAPI and return each command's text output.

    `fmt` defaults to text: `show` commands over eAPI return their output under `output` in
    text format, and json format refuses several of them outright.
    """
    import requests

    if not device or not set(device) <= _SAFE_DEVICE:
        raise ContractError(f"invalid device name {device!r}")

    address = conf.device_address(device)
    if not address:
        raise ApplyError(f"No address known for {device}")

    payload = {
        "jsonrpc": "2.0",
        "method": "runCmds",
        "params": {"version": 1, "cmds": commands, "format": fmt},
        "id": f"nbops-{device}",
    }
    try:
        response = requests.post(
            f"https://{address}/command-api",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            auth=(conf.eapi_username(), conf.eapi_password()),
            verify=False,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise ApplyError(f"Could not reach {device}: {exc}") from exc

    if response.status_code == 401:
        raise ApplyError(f"{device} rejected the eAPI credentials")
    if response.status_code >= 400:
        raise ApplyError(f"{device} answered {response.status_code}")

    try:
        body = response.json()
    except ValueError as exc:
        raise ApplyError(f"{device} returned something that is not JSON") from exc

    if "error" in body:
        raise ApplyError(f"{device} rejected the change: {_error_text(body.get('error') or {})}")

    results: list[str] = []
    for item in body.get("result") or []:
        if isinstance(item, dict):
            results.append(str(item.get("output") or ""))
        else:
            results.append(str(item))
    return results


def _error_text(error: dict[str, Any]) -> str:
    data = error.get("data")
    if isinstance(data, list):
        parts = []
        for item in data:
            if isinstance(item, dict):
                errs = item.get("errors")
                if errs:
                    parts.append("; ".join(str(e) for e in errs))
            elif item:
                parts.append(str(item))
        return ("; ".join(parts) or str(error.get("message") or "rejected"))[:250]
    if isinstance(data, dict):
        return str(data.get("errors") or data.get("message") or "rejected")[:250]
    return str(error.get("message") or "rejected")[:250]


# --------------------------------------------------------------------------- #
# Preview
# --------------------------------------------------------------------------- #


def _current_vlans(device: str) -> set[int]:
    """The VLANs present on the device right now."""
    from workbench.device_reader import parse_vlans

    out = _eapi(device, ["enable", "show vlan"])
    return set(parse_vlans(out[-1] if out else ""))


def _current_trunk(device: str, port: str) -> set[int]:
    """The VLANs allowed on one trunk right now."""
    from workbench.device_reader import parse_trunks
    from workbench.drift import find_trunk

    out = _eapi(device, ["enable", "show interfaces trunk"])
    trunks = parse_trunks(out[-1] if out else "")
    found, vlans = find_trunk(trunks, port)
    return set(vlans) if found else set()


def _planned_vlans(plan: Plan) -> tuple[set[int], dict[str, set[int]]]:
    """What VLANs and trunk memberships the plan would create.

    Parsed from the plan's own configuration — the same text that is applied — so the preview
    describes the actual change rather than a second guess at it.
    """
    import re

    vlans: set[int] = set()
    trunk: dict[str, set[int]] = {}
    port = ""

    for raw in plan.config.splitlines():
        line = raw.strip()
        match = re.match(r"^vlan (\d{1,4})$", line)
        if match:
            vlans.add(int(match.group(1)))
            continue
        if line.startswith("interface "):
            port = line.split(None, 1)[1] if " " in line else ""
            continue
        match = re.match(r"^switchport trunk allowed vlan (?:add )?([\d,\-]+)$", line)
        if match and port:
            from workbench.device_reader import parse_vlan_list

            trunk.setdefault(port, set()).update(parse_vlan_list(match.group(1)))
    return vlans, trunk


def preview(plan: Plan, **_: Any) -> Preview:
    """What this plan would change, without changing it.

    Computed by comparing the plan against the device's current state. An empty diff means the
    device already matches — the demo's definition of "no drift".
    """
    if plan.mode == "replace":
        raise ContractError(
            "refusing a replace-mode plan: it would replace the device's whole running "
            "configuration, and over eAPI there is no session to protect it. Use merge."
        )

    wanted_vlans, wanted_trunks = _planned_vlans(plan)
    have_vlans = _current_vlans(plan.device)

    lines: list[str] = []
    for vlan in sorted(wanted_vlans - have_vlans):
        lines.append(f"+vlan {vlan}")

    for port, wanted in sorted(wanted_trunks.items()):
        have = _current_trunk(plan.device, port)
        for vlan in sorted(wanted - have):
            lines.append(f"+interface {port}: allow vlan {vlan}")

    diff = "\n".join(lines)
    return Preview(
        device=plan.device,
        diff=diff,
        changed_lines=sum(1 for ln in diff.splitlines() if ln.startswith(("+", "-"))),
    )


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #


def apply_change(plan: Plan, **_: Any) -> Preview:
    """Apply the plan, verify it landed, and persist it.

    The read-back is not ceremony: eAPI accepted a whole session and applied nothing, with no
    error. Reporting success without looking is how that goes unnoticed until a customer asks.
    """
    if plan.mode == "replace":
        raise ContractError("refusing a replace-mode plan; use merge")

    before = preview(plan)
    if before.is_empty:
        return before

    commands = ["enable", "configure"]
    commands.extend(plan.config.splitlines())
    commands.extend(["end", "write memory"])
    _eapi(plan.device, commands)

    after = preview(plan)
    if not after.is_empty:
        raise ApplyError(
            f"{plan.device} accepted the change but does not reflect it afterwards: "
            f"{after.diff.replace(chr(10), '; ')[:200]}"
        )

    return Preview(
        device=plan.device,
        diff=before.diff,
        changed_lines=before.changed_lines,
    )


__all__ = ["ApplyError", "apply_change", "preview"]
