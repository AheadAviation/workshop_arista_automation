"""Thin client for the local lab agent (INIT-001/SPEC-003).

The only module in the plugin that speaks to the lab host. Every call is a fixed
path with no interpolation, so a compromised NetBox cannot turn this into remote
command execution: it can only ask for the actions `lab-agent` already offers.
"""

from __future__ import annotations

from typing import Any

import requests

from . import conf

#: Fixed paths, mirrored from `local-lab/lab-agent/agent.py`.
ACTION_PATHS: dict[str, str] = {
    "destroy-lab": "/actions/destroy-lab",
    "deploy-bare-lab": "/actions/deploy-bare-lab",
    "run-discovery": "/actions/run-discovery",
    "push-configs": "/actions/push-configs",
    "validate-fabric": "/actions/validate-fabric",
    "eapi": "/actions/eapi",
    "inject-rogue-drift": "/actions/inject-rogue-drift",
    "remediate-drift": "/actions/remediate-drift",
}

AGENT_ACTIONS: tuple[str, ...] = tuple(ACTION_PATHS)


class LabAgentError(RuntimeError):
    """The lab agent could not be reached or refused the request."""


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {conf.lab_agent_token()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _url(path: str) -> str:
    base = conf.lab_agent_url()
    if not base:
        raise LabAgentError(
            "No lab agent configured. Set lab_agent_url in PLUGINS_CONFIG['netbox_demo']."
        )
    return f"{base}{path}"


def _request(method: str, path: str, *, json_body: dict | None = None) -> dict[str, Any]:
    if not conf.lab_agent_token():
        raise LabAgentError(
            "No lab agent token configured. Set lab_agent_token in PLUGINS_CONFIG['netbox_demo']."
        )
    try:
        resp = requests.request(
            method,
            _url(path),
            headers=_headers(),
            json=json_body,
            timeout=conf.lab_agent_timeout(),
            verify=conf.verify_tls(),
        )
    except requests.exceptions.ConnectionError as exc:
        raise LabAgentError(
            f"Cannot reach the lab agent at {conf.lab_agent_url()}. "
            f"Is it running and is Tailscale up? ({exc.__class__.__name__})"
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise LabAgentError(
            f"The lab agent did not answer within {conf.lab_agent_timeout()}s."
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise LabAgentError(f"Lab agent request failed: {exc}") from exc

    if resp.status_code == 401:
        raise LabAgentError(
            "The lab agent rejected the token. lab_agent_token here must match "
            "LAB_AGENT_TOKEN on the lab host."
        )
    try:
        body = resp.json()
    except ValueError as exc:
        raise LabAgentError(
            f"The lab agent returned a non-JSON response ({resp.status_code})."
        ) from exc
    if not isinstance(body, dict):
        raise LabAgentError("The lab agent returned an unexpected response shape.")
    return body


def health() -> dict[str, Any]:
    """Agent, ContainerLab and Orb Agent status. Raises when unreachable."""
    return _request("GET", "/health")


def health_or_none() -> dict[str, Any] | None:
    """Health for page rendering: never raises, so a page still loads when the lab is down."""
    try:
        return health()
    except LabAgentError:
        return None


def run_action(action: str, payload: dict | None = None) -> dict[str, Any]:
    """Invoke one allowlisted action. Raises `LabAgentError` on any failure."""
    if action not in ACTION_PATHS:
        raise LabAgentError(f"Unknown lab action {action!r}")
    result = _request("POST", ACTION_PATHS[action], json_body=payload or {})
    if not result.get("ok", False):
        message = result.get("error") or result.get("output") or "the lab agent reported failure"
        raise LabAgentError(str(message).strip())
    return result


def eapi_show(device: str, commands: list[str]) -> dict[str, Any]:
    """Read-only device access, used for the live-config and drift tabs."""
    return run_action("eapi", {"device": device, "mode": "show", "commands": commands, "format": "text"})


def extract_eapi_text(response: dict[str, Any]) -> str:
    """Return the text output from an `eapi_show` response.

    The lab agent prepends ``enable`` before the requested show command(s), so
    ``result[0]`` is ``{"output": ""}`` and the actual command output is in the
    subsequent entry (usually ``result[-1]``).
    """
    rows = response.get("result") or []
    if not isinstance(rows, list):
        return ""
    for row in reversed(rows):
        if isinstance(row, dict):
            text = row.get("output", "")
            if text:
                return str(text)
        elif row:
            return str(row)
    return ""


def eapi_config(device: str, commands: list[str]) -> dict[str, Any]:
    """Change a device, used only by an approved Day 2 apply."""
    return run_action("eapi", {"device": device, "mode": "config", "commands": commands})
