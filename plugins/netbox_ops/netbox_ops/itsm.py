"""ITSM integration — Zammad (INIT-002/SPEC-007, implementation in SPEC-006).

Two jobs:

1. **Show the tickets**, so "the ITSM ticket" is a real thing on the page rather than a claim.
2. **Carry the approval**, so a day-two change is approved in the ticketing system the customer
   already runs, not in a bespoke button. That is the whole point of the integration: the
   approval lives where their process lives.

Zammad's API is a token-in-header REST API. The token is never placed in a URL, because URLs
end up in logs and in screenshots.
"""

from __future__ import annotations

from typing import Any

from . import conf


class ItsmError(RuntimeError):
    """The ticketing system could not be reached or refused the call."""


class ItsmNotConfigured(ItsmError):
    """No ITSM URL configured. Distinct from unreachable, and reported differently."""


def _session_and_base():
    import requests

    base = conf.itsm_url()
    if not base:
        raise ItsmNotConfigured(
            "ITSM is not configured. Set itsm_url and itsm_token in PLUGINS_CONFIG."
        )
    session = requests.Session()
    token = conf.itsm_token()
    if token:
        session.headers["Authorization"] = f"Token token={token}"
    session.headers["Accept"] = "application/json"
    return session, base


def _get(path: str, timeout: float = 15.0) -> Any:
    import requests

    session, base = _session_and_base()
    try:
        response = session.get(f"{base}{path}", timeout=timeout)
    except requests.RequestException as exc:
        raise ItsmError(f"Could not reach the ITSM system at {base}: {exc}") from exc
    if response.status_code == 401:
        raise ItsmError("ITSM rejected the token (401). Check itsm_token.")
    if response.status_code >= 400:
        raise ItsmError(f"ITSM answered {response.status_code} for {path}")
    try:
        return response.json()
    except ValueError as exc:
        raise ItsmError(f"ITSM returned something that is not JSON for {path}") from exc


def status() -> dict[str, Any]:
    """Whether ticketing is configured and reachable."""
    try:
        _get("/api/v1/users/me")
    except ItsmNotConfigured as exc:
        return {
            "ok": True,
            "configured": False,
            "reachable": False,
            "headline": "ITSM is not configured",
            "detail": str(exc),
        }
    except ItsmError as exc:
        return {
            "ok": False,
            "configured": True,
            "reachable": False,
            "headline": "Could not reach the ITSM system",
            "detail": str(exc),
        }

    return {
        "ok": True,
        "configured": True,
        "reachable": True,
        "headline": "ITSM is up",
        "detail": "",
    }


def tickets(limit: int = 25) -> dict[str, Any]:
    """Recent tickets, newest first."""
    try:
        payload = _get(f"/api/v1/tickets?per_page={int(limit)}&sort_by=created_at&order_by=desc")
    except ItsmNotConfigured as exc:
        return {"ok": True, "configured": False, "tickets": [], "headline": "ITSM is not configured", "detail": str(exc)}
    except ItsmError as exc:
        return {"ok": False, "configured": True, "tickets": [], "headline": "Could not read tickets", "detail": str(exc)}

    items = payload.get("tickets") if isinstance(payload, dict) else payload
    tickets_out: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        # Zammad returns state/priority as *ids*, not names. Resolving them costs extra calls
        # per ticket, so the raw id is shown with a readable fallback rather than pretending
        # to know the name.
        state = item.get("state_id")
        tickets_out.append(
            {
                "id": item.get("id"),
                "number": item.get("number") or "",
                "title": item.get("title") or "",
                "state": item.get("state") or (f"state {state}" if state else ""),
                "priority": item.get("priority") or "",
                "group": item.get("group") or "",
                "created_at": item.get("created_at") or "",
                "url": f"{conf.itsm_url()}/#ticket/zoom/{item.get('id')}" if item.get("id") else "",
            }
        )

    return {
        "ok": True,
        "configured": True,
        "tickets": tickets_out,
        "headline": f"{len(tickets_out)} ticket(s)" if tickets_out else "No tickets yet",
        "detail": "" if tickets_out else "Open a day-two change and the ticket will appear here.",
    }


def create_change_ticket(
    title: str, body: str, *, group: str = "Users"
) -> dict[str, Any]:
    """Open a change ticket. Returns its id so the change can be tied to it.

    The group defaults to `Users` because that is the group a stock Zammad seeds; inventing a
    name like "Network Operations" produced a 422 `No lookup value found for 'group'`, which is
    a confusing way to say the group does not exist. A site with its own groups sets this in
    config.
    """
    import requests

    try:
        session, base = _session_and_base()
    except ItsmNotConfigured as exc:
        return {"ok": True, "configured": False, "headline": "ITSM is not configured", "detail": str(exc)}

    payload = {
        "title": title,
        "article": {"body": body, "type": "note", "internal": False},
        "group": group,
        "type": "Change",
        # Zammad requires a customer on every ticket. The demo uses the API user as the
        # customer, because a lab has no customer directory to look one up in.
        "customer_id": 1,
    }
    try:
        response = session.post(f"{base}/api/v1/tickets", json=payload, timeout=20)
    except requests.RequestException as exc:
        return {"ok": False, "headline": "Could not reach the ITSM system", "detail": str(exc)}

    if response.status_code >= 400:
        # Include the body: Zammad's 422s name the exact missing or invalid field, and throwing
        # that away turns a one-line fix into a guessing game.
        try:
            detail = response.json()
            message = str(detail.get("error_human") or detail.get("error") or detail)
        except ValueError:
            message = response.text[:200]
        return {
            "ok": False,
            "headline": "ITSM refused the ticket",
            "detail": f"{response.status_code}: {message[:250]}",
        }

    try:
        ticket = response.json()
    except ValueError:
        return {"ok": False, "headline": "ITSM returned something that is not JSON", "detail": ""}

    return {
        "ok": True,
        "configured": True,
        "ticket_id": ticket.get("id"),
        "number": ticket.get("number"),
        "headline": f"Opened ticket {ticket.get('number') or ticket.get('id')}",
        "detail": "",
    }


def approve_ticket(ticket_id: int, note: str = "Approved via NetBox Ops") -> dict[str, Any]:
    """Add an approval note to a ticket.

    Deliberately a note rather than a state change: Zammad's states are site-configurable and
    guessing at a state name is how you silently fail to approve something. The note is the
    record of the human decision; the state is the customer's own process.
    """
    import requests

    try:
        session, base = _session_or_error()
    except ItsmError as exc:
        return {"ok": False, "headline": "ITSM is not available", "detail": str(exc)}

    payload = {"ticket_id": int(ticket_id), "body": note, "type": "note", "internal": False}
    try:
        response = session.post(f"{base}/api/v1/ticket_articles", json=payload, timeout=20)
    except requests.RequestException as exc:
        return {"ok": False, "headline": "Could not reach the ITSM system", "detail": str(exc)}

    if response.status_code >= 400:
        return {"ok": False, "headline": "ITSM refused the approval note", "detail": f"{response.status_code}"}

    return {"ok": True, "headline": f"Recorded approval on ticket {ticket_id}", "detail": ""}


def _session_or_error():
    return _session_and_base()


__all__ = [
    "ItsmError",
    "ItsmNotConfigured",
    "approve_ticket",
    "create_change_ticket",
    "status",
    "tickets",
]
