"""Live device state: the running config, and the diff between two reads (INIT-002/SPEC-004).

This is what makes the demo show rather than claim. Three things:

* **The running config, read live.** Pulled from the switch over eAPI each time it is asked
  for, never cached and never stored. A presenter can edit a switch in a terminal and watch
  this change — which is the point.
* **A saved "before".** Captured immediately before a push so the change can be shown as a
  real diff rather than a description of one.
* **The diff.** Unified, line-level, of before against after. This is the artefact the
  audience reads: here is what the device had, here is what it has now.

Secrets are redacted from anything shown or stored. A switch's running config contains
password hashes; a demo shown to a hospital CISO must not put those on a screen, and a
snapshot kept on disk must not keep them either.
"""

from __future__ import annotations

import re
from typing import Any

from . import conf, eapi_exec

#: Lines whose value is a secret and must never be shown or stored.
_SECRET_RE = re.compile(
    r"(secret\s+sha512\s+)\S+"
    r"|(secret\s+\d\s+)\S+"
    r"|(password\s+\d\s+)\S+"
    r"|(snmp-server community\s+)\S+"
    r"|(neighbor\s+\S+\s+password\s+\d\s+)\S+",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """Replace secret values with a placeholder, keeping the line readable.

    Applied to everything that leaves this module. A running config is full of password
    hashes, and putting them on a projector or into a saved snapshot is a real leak — not a
    theoretical one, since these hashes are the switch's actual credentials.
    """

    def _sub(match: re.Match[str]) -> str:
        for group in match.groups():
            if group:
                return f"{group}<redacted>"
        return "<redacted>"

    return _SECRET_RE.sub(_sub, text)


def running_config(device: str, *, container_up: bool | None = None) -> dict[str, Any]:
    """The device's running config, read live and redacted.

    Never cached. Each call is a real read, so what the page shows is what the switch is
    configured with at that moment. When a switch is not deployed yet or still booting EOS,
    returns a clean operator-friendly state instead of a raw socket exception.
    """
    if container_up is False:
        return {
            "ok": False,
            "state": "not_deployed",
            "device": device,
            "config": "",
            "lines": 0,
            "headline": f"{device} is not deployed yet",
            "detail": "No running configuration exists yet. Run Workshop Step 6 (Deploy Fabric) to boot this switch.",
        }

    try:
        out = eapi_exec._eapi(device, ["enable", "show running-config"], timeout=15)
    except eapi_exec.ApplyError as exc:
        msg = str(exc)
        booting = container_up is True and ("Connection refused" in msg or "timed out" in msg)
        return {
            "ok": False,
            "state": "booting" if booting else "unreachable",
            "device": device,
            "config": "",
            "lines": 0,
            "headline": f"{device} is still booting EOS" if booting else f"Could not read {device}",
            "detail": (
                "Container is running and EOS eAPI is initializing (~45-60s after deploy). Click Refresh in a moment."
                if booting
                else msg
            ),
        }

    raw = out[-1] if out else ""
    text = redact(raw)
    return {
        "ok": True,
        "state": "ready",
        "device": device,
        "config": text,
        "lines": len(text.splitlines()),
        "headline": f"{device}: {len(text.splitlines())} lines",
        "detail": "",
    }


def running_configs(devices: list[str] | None = None) -> dict[str, Any]:
    """Running configs for several devices at once, read in parallel for the side-by-side view."""
    from concurrent.futures import ThreadPoolExecutor

    from . import labs

    targets = devices or ["spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04"]

    # Check container running state once so undeployed switches return instantly without waiting on timeouts.
    up_map: dict[str, bool] = {}
    try:
        lab = labs.lab_status()
        for node in lab.get("nodes") or []:
            name = str(node.get("name") or "")
            if name:
                up_map[name] = bool(node.get("up"))
    except Exception:
        pass

    def _read(dev: str) -> dict[str, Any]:
        return running_config(dev, container_up=up_map.get(dev, False) if up_map else None)

    with ThreadPoolExecutor(max_workers=min(len(targets), 6) or 1) as pool:
        results = list(pool.map(_read, targets))
    readable = sum(1 for r in results if r["ok"])
    not_deployed = sum(1 for r in results if r.get("state") == "not_deployed")
    if readable == len(targets):
        headline = f"Live config from all {len(targets)} devices"
    elif not_deployed == len(targets):
        headline = "No switches deployed yet (0 of 6 running)"
    else:
        headline = f"Live config from {readable} of {len(targets)} devices"
    return {
        "ok": readable == len(targets),
        "devices": results,
        "headline": headline,
    }


# --------------------------------------------------------------------------- #
# Before / after
# --------------------------------------------------------------------------- #

#: Keyed by device name, in memory only. A snapshot exists to show a diff during one demo,
#: not to be kept — the audit record is what persists.
_SNAPSHOTS: dict[str, str] = {}


def snapshot(device: str) -> dict[str, Any]:
    """Capture the device's config as the "before" for the next change."""
    result = running_config(device)
    if not result["ok"]:
        return result
    _SNAPSHOTS[device] = result["config"]
    return {
        "ok": True,
        "device": device,
        "lines": result["lines"],
        "headline": f"Captured {device}'s config before the change",
        "detail": f"{result['lines']} lines",
    }


def snapshot_of(device: str) -> str | None:
    return _SNAPSHOTS.get(device)


def clear_snapshot(device: str | None = None) -> None:
    if device:
        _SNAPSHOTS.pop(device, None)
    else:
        _SNAPSHOTS.clear()


def diff_against_snapshot(device: str) -> dict[str, Any]:
    """A unified diff of the config captured before a change against the config now."""
    import difflib

    before = _SNAPSHOTS.get(device)
    if before is None:
        return {
            "ok": True,
            "device": device,
            "diff": "",
            "changed_lines": 0,
            "headline": f"No snapshot for {device}",
            "detail": "A change captures one automatically; nothing to compare yet.",
        }

    current = running_config(device)
    if not current["ok"]:
        return current

    before_lines = before.splitlines()
    after_lines = current["config"].splitlines()

    # Drop the password-hash noise: EOS re-salts it on every read, so it always differs and
    # would drown the real change in noise. Same reasoning as the engine's diff cleaner.
    def useful(line: str) -> bool:
        return "secret sha512" not in line and "! Command:" not in line

    before_clean = [ln for ln in before_lines if useful(ln)]
    after_clean = [ln for ln in after_lines if useful(ln)]

    diff_lines = list(
        difflib.unified_diff(
            before_clean,
            after_clean,
            fromfile=f"{device} (before)",
            tofile=f"{device} (now)",
            lineterm="",
            n=2,
        )
    )
    # Drop the ---/+++ header lines; the page already labels the two sides.
    body = [ln for ln in diff_lines if not ln.startswith(("--- ", "+++ "))]
    diff = "\n".join(body)

    return {
        "ok": True,
        "device": device,
        "diff": diff,
        "changed_lines": sum(1 for ln in body if ln.startswith(("+", "-"))),
        "headline": (
            f"{device}: {len(body)} diff line(s)"
            if body
            else f"{device}: no change since the snapshot"
        ),
        "detail": "",
    }


__all__ = [
    "clear_snapshot",
    "diff_against_snapshot",
    "redact",
    "running_config",
    "running_configs",
    "snapshot",
    "snapshot_of",
]
