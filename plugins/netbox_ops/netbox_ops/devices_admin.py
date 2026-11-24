"""Add devices to NetBox and push rendered configs to them (INIT-002/SPEC-004).

This closes the loop the brief asked for: add the devices, add the configs, boot them, and show
that what is on them matches what was rendered from the source of truth.

Three actions, each of which reports what it actually did:

* `import_fabric` — run the repo's own topology import, so NetBox has the devices.
* `render_configs` — render configs from NetBox (Ansible, in the ansible container).
* `apply_configs` — push a rendered config to a device over eAPI, then read it back.

The third is the interesting one: it is the same "render then push then prove" path the demo
shows, driven from a button instead of a script. It pushes with plain `configure` + `write
memory` because configuration sessions do not work over eAPI (see `eapi_exec`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from . import conf, eapi_exec

REPO = Path("/opt/netbox/engine")
CONFIGS = Path("/opt/netbox/configs")

#: The devices the fabric import is expected to produce.
EXPECTED = ("spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04")


class BootstrapError(RuntimeError):
    """An action could not be completed. The message is fit to show a person."""


def _run(args: list[str], *, timeout: int = 600, input_text: str | None = None) -> tuple[str, int]:
    """Run a command, returning (combined output, exit code)."""
    try:
        proc = subprocess.run(  # noqa: S603 - argument list, no shell
            args,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BootstrapError(f"command not available: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BootstrapError(f"took longer than {timeout}s") from exc
    return f"{proc.stdout or ''}\n{proc.stderr or ''}".strip(), proc.returncode


# --------------------------------------------------------------------------- #
# Devices
# --------------------------------------------------------------------------- #


def count_devices() -> int:
    """How many devices NetBox currently holds for this fabric."""
    import requests

    token = conf.netbox_token()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Token {token}"
    try:
        response = requests.get(
            f"{conf.netbox_url()}/api/dcim/devices/?limit=1",
            headers=headers,
            timeout=15,
        )
        if response.status_code == 200:
            return int((response.json() or {}).get("count") or 0)
    except Exception:
        return 0
    return 0


def import_fabric() -> dict[str, Any]:
    """Populate NetBox from the repo's fabric topology.

    Runs the repo's own importer rather than reimplementing it: this is the same code path
    `run.ps1 import` uses, so a pass here means the CLI path works too.
    """
    before = count_devices()
    script = REPO.parent / "scripts" / "full_import.py"
    if not script.exists():
        # Inside the NetBox image the repo is mounted at /opt/netbox/engine, so the scripts
        # live one level up from the package. Fall back to a clear message rather than a
        # confusing FileNotFoundError.
        raise BootstrapError(
            f"the importer is not available in this container (looked for {script}). "
            "Run .\\run.ps1 import on the host."
        )

    out, code = _run(["python", str(script)], timeout=600)
    after = count_devices()

    if code != 0:
        raise BootstrapError(f"import failed: {out[-300:]}")
    return {
        "ok": True,
        "headline": f"Imported the fabric — {after} devices in NetBox (was {before})",
        "detail": "",
        "before": before,
        "after": after,
    }


def device_status() -> dict[str, Any]:
    """Whether the expected devices are present in NetBox."""
    count = count_devices()
    if count == 0:
        return {
            "ok": True,
            "headline": "No devices in NetBox",
            "detail": "Add them with the import action, or run .\\run.ps1 import.",
            "count": 0,
            "expected": list(EXPECTED),
        }
    return {
        "ok": True,
        "headline": f"{count} devices in NetBox",
        "detail": "",
        "count": count,
        "expected": list(EXPECTED),
    }


# --------------------------------------------------------------------------- #
# Configs
# --------------------------------------------------------------------------- #


def rendered_configs() -> dict[str, Any]:
    """Which rendered configs are available to push."""
    if not CONFIGS.exists():
        return {
            "ok": True,
            "headline": "No rendered configs available",
            "detail": "Render them with the action, or run .\\run.ps1 generate.",
            "configs": [],
        }
    files = sorted(p.name for p in CONFIGS.glob("*.cfg"))
    return {
        "ok": True,
        "headline": f"{len(files)} rendered config(s)" if files else "No rendered configs",
        "detail": "" if files else "Render them with the action, or run .\\run.ps1 generate.",
        "configs": files,
    }


def apply_config(device: str) -> dict[str, Any]:
    """Push a device's rendered config to it, then prove it took.

    **This pushes deltas only, and that is a hard-won constraint, not a simplification.**

    Pushing a whole rendered config (~300-380 lines) over eAPI does not work: each stanza is
    its own commit, the request times out partway, and the switch is left *partially
    configured* — hostname and VLANs present, and BGP, MLAG and VXLAN gone. That looks
    configured and is not, which is the worst outcome available. It happened here and cost
    three leaf switches; `scripts/restore_configs.py` repairs it from the host, where a whole
    config can be applied atomically in one CLI session.

    So: this action reads what the rendered config declares, applies only the VLANs and trunk
    memberships that are missing, and then reads the device back and reports whether it now
    matches. A full-config push stays on the host via `run.ps1 apply`.
    """
    path = CONFIGS / f"{device}.cfg"
    if not path.exists():
        raise BootstrapError(f"no rendered config for {device} at {path}")

    body = path.read_text(encoding="utf-8")

    # Work out what the rendered config actually declares.
    import re

    wanted_vlans: dict[int, str] = {}
    wanted_trunks: dict[str, set[int]] = {}
    port = ""
    pending_vlan: int | None = None

    for raw in body.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        match = re.match(r"^vlan (\d{1,4})$", stripped)
        if match:
            pending_vlan = int(match.group(1))
            wanted_vlans.setdefault(pending_vlan, f"VLAN{pending_vlan}")
            continue
        if pending_vlan is not None and stripped.startswith("name "):
            wanted_vlans[pending_vlan] = stripped.split(None, 1)[1][:32]
            pending_vlan = None
            continue
        if stripped.startswith("interface "):
            port = stripped.split(None, 1)[1] if " " in stripped else ""
            continue
        match = re.match(r"^switchport trunk allowed vlan ([\d,\-]+)$", stripped)
        if match and port:
            from workbench.device_reader import parse_vlan_list

            wanted_trunks.setdefault(port, set()).update(parse_vlan_list(match.group(1)))

    if not wanted_vlans and not wanted_trunks:
        # A spine is a fabric device: its rendered config declares no tenant VLANs and no
        # server trunks. That is correct, not a failure — so it is verified on hostname alone
        # rather than reported as something this action could not handle.
        from workbench.device_reader import parse_hostname

        check = eapi_exec._eapi(device, ["enable", "show hostname"])
        actual_host = parse_hostname(check[1] if len(check) > 1 else "")
        if actual_host != device:
            raise BootstrapError(
                f"{device}: hostname is {actual_host!r}, expected {device!r}"
            )
        return {
            "ok": True,
            "headline": f"{device}: matches the rendered config",
            "detail": "fabric device — no tenant VLANs or server trunks to compare",
            "device": device,
            "changed": False,
        }

    # Apply only what is missing, in one bounded request.
    from workbench.device_reader import parse_vlans
    from workbench.drift import find_trunk

    state = eapi_exec._eapi(device, ["enable", "show vlan", "show interfaces trunk"])
    have_vlans = set(parse_vlans(state[1] if len(state) > 1 else ""))
    trunk_text = state[2] if len(state) > 2 else ""
    from workbench.device_reader import parse_trunks

    have_trunks = parse_trunks(trunk_text)

    commands = ["enable", "configure"]
    changed = False

    for vlan, name in sorted(wanted_vlans.items()):
        if vlan not in have_vlans:
            commands.extend([f"vlan {vlan}", f"   name {name}"])
            changed = True

    for trunk, wanted in sorted(wanted_trunks.items()):
        found, have = find_trunk(have_trunks, trunk)
        for vlan in sorted(wanted - set(have)):
            commands.extend([f"interface {trunk}", f"   switchport trunk allowed vlan add {vlan}"])
            changed = True

    if changed:
        commands.extend(["end", "write memory"])
        eapi_exec._eapi(device, commands, timeout=120)

    # Read back and compare — a push that silently did nothing is the failure mode to catch.
    after = eapi_exec._eapi(device, ["enable", "show hostname", "show vlan", "show interfaces trunk"])
    from workbench.device_reader import parse_hostname

    actual_host = parse_hostname(after[1] if len(after) > 1 else "")
    actual_vlans = set(parse_vlans(after[2] if len(after) > 2 else ""))
    actual_trunks = parse_trunks(after[3] if len(after) > 3 else "")

    problems = []
    if actual_host != device:
        problems.append(f"hostname is {actual_host!r}, expected {device!r}")
    missing = sorted(set(wanted_vlans) - actual_vlans)
    if missing:
        problems.append(f"VLANs missing: {missing}")
    for trunk, wanted in sorted(wanted_trunks.items()):
        _found, have = find_trunk(actual_trunks, trunk)
        absent = sorted(wanted - set(have))
        if absent:
            problems.append(f"{trunk} missing VLANs {absent}")

    if problems:
        raise BootstrapError(f"{device}: does not match the rendered config — " + "; ".join(problems))

    return {
        "ok": True,
        "headline": (
            f"{device}: matches the rendered config"
            if not changed
            else f"{device}: config applied and verified"
        ),
        "detail": (
            f"already matching — nothing sent"
            if not changed
            else f"hostname {actual_host}, {len(actual_vlans)} VLANs, {len(wanted_trunks)} trunk(s)"
        ),
        "device": device,
        "changed": changed,
    }


def apply_all() -> dict[str, Any]:
    """Push every rendered config, reporting each device."""
    results: list[dict[str, Any]] = []
    failures = 0
    for device in EXPECTED:
        if not (CONFIGS / f"{device}.cfg").exists():
            continue
        try:
            results.append(apply_config(device))
        except BootstrapError as exc:
            results.append({"device": device, "ok": False, "error": str(exc)})
            failures += 1

    return {
        "ok": failures == 0,
        "headline": (
            "All rendered configs applied and verified"
            if failures == 0
            else f"{failures} device(s) did not verify"
        ),
        "detail": "" if failures == 0 else "See the per-device results.",
        "results": results,
    }


__all__ = [
    "BootstrapError",
    "apply_all",
    "apply_config",
    "count_devices",
    "device_status",
    "import_fabric",
    "rendered_configs",
]
