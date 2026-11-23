"""AWX integration (INIT-002/SPEC-007, implementation in SPEC-005).

Reads AWX over its REST API: is it reachable, what job templates exist, what ran last. Can
launch a template, because "show the AWX" is only convincing if a job actually runs.

**Not configured is a state, not an error.** If `awx_url` is empty the page says so plainly,
because an empty list reads as "AWX is up and has no templates" — a very different and much
more alarming thing to show a customer.
"""

from __future__ import annotations

from typing import Any

from . import conf


class AwxError(RuntimeError):
    """AWX could not be reached or refused the call."""


def _client():
    """A small requests session with AWX's token header. Never puts the token in a URL."""
    import requests

    base = conf.awx_url()
    if not base:
        raise AwxNotConfigured("AWX is not configured. Set awx_url and awx_token in PLUGINS_CONFIG.")

    session = requests.Session()
    token = conf.awx_token()
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    session.headers["Accept"] = "application/json"
    return session, base


class AwxNotConfigured(AwxError):
    """AWX has no URL configured. Distinct from unreachable, and reported differently."""


def _get(path: str, timeout: float = 15.0) -> dict[str, Any]:
    import requests

    session, base = _client()
    try:
        response = session.get(f"{base}{path}", timeout=timeout)
    except requests.RequestException as exc:
        raise AwxError(f"Could not reach AWX at {base}: {exc}") from exc
    if response.status_code == 401:
        raise AwxError(f"AWX rejected the token (401). Check awx_token.")
    if response.status_code >= 400:
        raise AwxError(f"AWX answered {response.status_code} for {path}")
    try:
        return response.json()
    except ValueError as exc:
        raise AwxError(f"AWX returned something that is not JSON for {path}") from exc


def status() -> dict[str, Any]:
    """Whether AWX is configured, reachable, and what version it reports."""
    try:
        ping = _get("/api/v2/ping/")
    except AwxNotConfigured as exc:
        return {
            "ok": True,
            "configured": False,
            "reachable": False,
            "headline": "AWX is not configured",
            "detail": str(exc),
        }
    except AwxError as exc:
        return {
            "ok": False,
            "configured": True,
            "reachable": False,
            "headline": "Could not reach AWX",
            "detail": str(exc),
        }

    version = str(ping.get("version") or "unknown")
    return {
        "ok": True,
        "configured": True,
        "reachable": True,
        "version": version,
        "headline": f"AWX {version} is up",
        "detail": "",
    }


def job_templates(limit: int = 25) -> dict[str, Any]:
    """The job templates a presenter can point at, and the last run of each."""
    try:
        payload = _get(f"/api/v2/job_templates/?page_size={int(limit)}")
    except AwxNotConfigured as exc:
        return {"ok": True, "configured": False, "templates": [], "headline": "AWX is not configured", "detail": str(exc)}
    except AwxError as exc:
        return {"ok": False, "configured": True, "templates": [], "headline": "Could not read AWX", "detail": str(exc)}

    templates: list[dict[str, Any]] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        templates.append(
            {
                "id": item.get("id"),
                "name": item.get("name") or "",
                "description": item.get("description") or "",
                "last_run": item.get("last_job_run") or "",
                "last_status": item.get("last_job_failed"),
                "playbook": item.get("playbook") or "",
            }
        )

    templates.sort(key=lambda t: t["name"].lower())
    return {
        "ok": True,
        "configured": True,
        "templates": templates,
        "headline": f"{len(templates)} job template(s)" if templates else "No job templates yet",
        "detail": "" if templates else "The AWX bootstrap may not have run yet.",
    }


def launch(template_id: int) -> dict[str, Any]:
    """Launch a job template. Used by the demo's "run the triage playbook" step."""
    import requests

    try:
        session, base = _client()
    except AwxNotConfigured as exc:
        return {"ok": True, "configured": False, "headline": "AWX is not configured", "detail": str(exc)}

    try:
        response = session.post(
            f"{base}/api/v2/job_templates/{int(template_id)}/launch/",
            json={},
            timeout=20,
        )
    except requests.RequestException as exc:
        return {"ok": False, "headline": "Could not reach AWX", "detail": str(exc)}

    if response.status_code >= 400:
        return {"ok": False, "headline": "AWX refused the launch", "detail": f"{response.status_code}"}

    try:
        job = response.json()
    except ValueError:
        return {"ok": False, "headline": "AWX returned something that is not JSON", "detail": ""}

    return {
        "ok": True,
        "configured": True,
        "job_id": job.get("id"),
        "headline": f"Launched job {job.get('id')}",
        "detail": "",
    }


__all__ = ["AwxError", "AwxNotConfigured", "job_templates", "launch", "status"]
