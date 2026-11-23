"""Read and drive devices over Arista eAPI (INIT-002/SPEC-002).

**Why eAPI rather than `docker exec`.** This is the architecture decision that matters in this
plugin, and it was forced by the security boundary holding:

* The engine's device driver shells out to `docker exec`. Inside NetBox there is no Docker CLI,
  and when the plugin was pointed at the Docker HTTP API the scoped proxy correctly refused
  `/exec` — because it is configured with `EXEC: 0`. That refusal is the whole point: NetBox
  must not be able to run commands inside containers.
* Arista switches, however, expose **eAPI** — JSON over HTTPS, built into EOS, designed exactly
  for this. NetBox can reach the devices on the lab management network directly.

So the plugin talks to the switches the way a network management platform is supposed to, and
NetBox keeps no Docker privilege beyond reading container state. The comparison, the approval
gate and the audit record are unchanged; only the transport differs.

The parsing is still `workbench.device_reader`'s, tested against captured EOS output — this
module only gets the JSON out of eAPI and hands the same text to the same parsers.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from workbench.contracts import Actual

from . import conf


class DeviceError(RuntimeError):
    """A device could not be read. The message is fit to show a person."""


def _eapi(device: str, commands: Sequence[str], *, timeout: float = 20.0) -> list[dict[str, Any]]:
    """Send commands to a switch's eAPI and return the per-command results.

    Takes a **list** of show commands in one request, which is what eAPI is for: one TLS
    handshake, several answers. Passing them one at a time made the page noticeably slow.
    """
    import requests

    address = conf.device_address(device)
    if not address:
        raise DeviceError(
            f"No address known for {device}. Set it in NetBox as the device's primary IP, "
            "or in the plugin's device map."
        )

    payload = {
        "jsonrpc": "2.0",
        "method": "runCmds",
        "params": {"version": 1, "cmds": list(commands), "format": "text"},
        "id": f"netbox-ops-{device}",
    }
    url = f"https://{address}/command-api"
    try:
        response = requests.post(
            url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            auth=(conf.eapi_username(), conf.eapi_password()),
            verify=False,  # lab switches use self-signed certificates
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise DeviceError(f"Could not reach {device} at {address}: {exc}") from exc

    if response.status_code == 401:
        raise DeviceError(
            f"{device} rejected the eAPI credentials. Check eapi_username / eapi_password."
        )
    if response.status_code >= 400:
        raise DeviceError(f"{device} answered {response.status_code} on eAPI")

    try:
        body = response.json()
    except ValueError as exc:
        raise DeviceError(f"{device} returned something that is not JSON") from exc

    if "error" in body:
        error = body.get("error") or {}
        message = _error_text(error.get("data"), error)

        if _is_auth_failure(message):
            raise DeviceError(
                f"{device} rejected the eAPI credentials. Check eapi_username / eapi_password, "
                f"and that the user has a role permitting eAPI. ({message})"
            )

        # A single failing command in the batch must not discard the ones that succeeded.
        # `show running-config section router bgp` legitimately fails on a switch with no BGP
        # stanza, and treating that as "the device is unreachable" made a perfectly readable
        # switch look down. Return what came back and let the caller judge completeness.
        partial = body.get("result")
        if isinstance(partial, list) and partial:
            return partial

        raise DeviceError(f"{device} rejected the request: {message}")

    return list(body.get("result") or [])


def _error_text(data: Any, error: dict) -> str:
    """Flatten eAPI's error payload to one readable line.

    `data` is a list of per-command failures in newer EOS and a dict in older builds; handling
    only one shape raised an AttributeError that hid the real rejection reason.
    """
    if isinstance(data, list):
        parts = []
        for item in data:
            if isinstance(item, dict):
                parts.append(str(item.get("errors") or item.get("message") or item))
            else:
                parts.append(str(item))
        return "; ".join(parts)[:300]
    if isinstance(data, dict):
        return str(data.get("errors") or data.get("message") or "unknown error")[:300]
    return str(error.get("message") or "unknown error")[:300]


def _is_auth_failure(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in ("unauthorized", "authentication", "bad username", "invalid credential")
    )

    return list(body.get("result") or [])


def read_device(device: str, *, platform: str = "eos") -> Actual:
    """Read one device's actual state, using the engine's parsers.

    Returns an `Actual` even when the device cannot be read — `reachable=False` with the
    reason — so a page can distinguish "nothing matches" from "I could not look".
    """
    from workbench.device_reader import (
        parse_asn,
        parse_hostname,
        parse_os_version,
        parse_trunks,
        parse_vlans,
    )

    commands = [
        "show hostname",
        "show vlan",
        "show interfaces trunk",
        "show version",
    ]

    try:
        results = _eapi(device, commands)
    except DeviceError as exc:
        return Actual(device=device, platform=platform, reachable=False, error=str(exc))

    def text(index: int) -> str:
        # eAPI returns text output as {"output": "..."} for format=text. A command that failed
        # in the batch simply is not there, which the parsers handle as "nothing found" —
        # the right answer for, say, a switch with no BGP stanza.
        if index >= len(results):
            return ""
        item = results[index]
        if isinstance(item, dict):
            return str(item.get("output") or "")
        return ""

    hostname_text = text(0)
    if not hostname_text.strip():
        # Hostname is the one command that must work: if it did not, the device is not
        # answering, and reporting it as compliant-because-empty would be a lie.
        return Actual(
            device=device,
            platform=platform,
            reachable=False,
            error=f"{device} answered eAPI but returned no hostname",
        )

    return Actual(
        device=device,
        platform=platform,
        reachable=True,
        hostname=parse_hostname(hostname_text),
        # ASN is read separately when a caller needs it; it is not part of the four commands
        # above because `show running-config section router bgp` legitimately fails on a leaf
        # with no BGP stanza, and including it made every leaf look unreachable.
        asn=0,
        vlans=parse_vlans(text(1)),
        trunk_allowed=parse_trunks(text(2)),
        os_version=parse_os_version(text(3)),
    )


__all__ = ["DeviceError", "read_device"]
