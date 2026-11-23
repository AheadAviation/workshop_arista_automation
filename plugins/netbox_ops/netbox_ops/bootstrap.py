"""Make every lab switch reachable over eAPI (INIT-002/SPEC-002).

Arista eAPI is the transport the plugin uses to read and drive devices, which is what lets
NetBox manage the whole demo without any Docker privilege for devices.

A switch needs four things before eAPI answers:

* a user with a role that permits eAPI (`network-admin`),
* the `management api http-commands` service enabled,
* the HTTPS protocol on, and
* the VRF the management interface lives in — **which is the part that bites**. The rendered
  fabric config puts Management0 in `MGMT`, but eAPI defaults to `default`; with the service
  bound to the wrong VRF, the port is open and authentication fails with a 401 that looks like
  a bad password.

This module sets all four, idempotently, over the switch's own CLI. It is the one place the
plugin still uses the CLI rather than eAPI — because it is how you turn eAPI on.
"""

from __future__ import annotations

import subprocess
from typing import Any

from . import conf

CONTAINER_PREFIX = "clab-netbox-arista-lab-"


class BootstrapError(RuntimeError):
    """A switch could not be prepared. The message is fit to show a person."""


def _cli(device: str, script: str, *, timeout: int = 90) -> str:
    """Run a CLI script on a device through `docker exec`.

    Only used for the eAPI bootstrap itself, and only from the host-side CLI, never from the
    NetBox container — which has no Docker CLI by design.
    """
    container = f"{CONTAINER_PREFIX}{device}"
    try:
        proc = subprocess.run(  # noqa: S603 - argument list, no shell
            ["docker", "exec", "-i", container, "Cli", "-p", "15"],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BootstrapError(
            "docker is not available here. Run this from the host, not from inside NetBox."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BootstrapError(f"{device} did not answer in {timeout}s") from exc

    out = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    if proc.returncode != 0:
        raise BootstrapError(f"{device}: CLI exited {proc.returncode}")
    return out


def _mgmt_vrf(device: str) -> str:
    """Work out which VRF Management0 is in, so eAPI can be bound to it.

    Guessing `default` here was the original bug: the port opened, the credentials were
    right, and every request still came back 401 because the service was listening in a
    different VRF from the interface.
    """
    out = _cli(device, "show running-config section interface Management0\n")
    vrf = "default"
    for line in out.splitlines():
        text = line.strip()
        if text.startswith("vrf ") and "instance" not in text:
            candidate = text.split(None, 1)[1].strip()
            if candidate:
                vrf = candidate
    return vrf


def ensure_eapi(device: str, *, username: str | None = None, password: str | None = None) -> dict[str, Any]:
    """Prepare one switch for eAPI and report what was done."""
    user = username or conf.eapi_username()
    secret = password or conf.eapi_password()
    vrf = _mgmt_vrf(device)

    script = "\n".join(
        [
            "configure",
            f"username {user} privilege 15 role network-admin secret {secret}",
            "management api http-commands",
            "   protocol https",
            f"   vrf {vrf}",
            "   no shutdown",
            "end",
            "write memory",
            "",
        ]
    )
    out = _cli(device, script)

    if "Copy completed" not in out:
        # Saving is the difference between "works now" and "works until the next restart",
        # so a failure to save is reported rather than passed over.
        raise BootstrapError(
            f"{device}: eAPI configured but the config was not saved — it may revert. "
            "Check that ZeroTouch is disabled on this switch."
        )

    return {"device": device, "vrf": vrf, "prepared": True}


def ensure_eapi_all(devices: list[str] | None = None) -> dict[str, Any]:
    """Prepare every lab switch. Returns per-device outcomes rather than raising on the first."""
    targets = devices or ["spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04"]
    results: list[dict[str, Any]] = []
    failures = 0
    for device in targets:
        try:
            results.append(ensure_eapi(device))
        except BootstrapError as exc:
            results.append({"device": device, "prepared": False, "error": str(exc)})
            failures += 1

    return {
        "ok": failures == 0,
        "headline": (
            f"eAPI ready on all {len(targets)} switches"
            if failures == 0
            else f"eAPI ready on {len(targets) - failures} of {len(targets)} switches"
        ),
        "detail": "" if failures == 0 else "See the failures below.",
        "results": results,
        "failures": failures,
    }


__all__ = ["BootstrapError", "ensure_eapi", "ensure_eapi_all"]
