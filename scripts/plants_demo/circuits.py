"""Internet circuits and their providers, as NetBox records them.

A circuit's provider holds the phone number, portal, account and SLA (in its
comments, one ``Label: value`` per line). The circuit's A end says which firewall
port it lands on, and its description starts "Primary" or "Backup".
"""

from __future__ import annotations

import re
from typing import Any

_FIELD = re.compile(r"^(NOC|Portal|Account|SLA):\s*(.+?)\s*$", re.MULTILINE)


def provider_details(comments: str) -> dict[str, str]:
    """``NOC: +1-555...`` lines from a provider's comments -> {"noc": ..., "portal": ...}."""
    return {key.lower(): value for key, value in _FIELD.findall(comments or "")}


def _circuit(record: Any, provider: Any, lands_on: str) -> dict[str, Any]:
    details = provider_details(getattr(provider, "comments", "") or "")
    description = record.description or ""
    return {
        "cid": record.cid,
        "provider": provider.name,
        "provider_slug": provider.slug,
        "type": record.type.name if record.type else "",
        "commit_mbps": (record.commit_rate or 0) // 1000,
        "role": "backup" if description.lower().startswith("backup") else "primary",
        "port": lands_on.split(" on ")[0] if lands_on else "",
        "lands_on": lands_on,
        "noc": details.get("noc", ""),
        "portal": details.get("portal", ""),
        "account": details.get("account", ""),
        "sla": details.get("sla", ""),
    }


def site_circuits(nb: Any, site_slug: str) -> list[dict[str, Any]]:
    """The site's circuits, primary first."""
    site = nb.dcim.sites.get(slug=site_slug)
    if site is None:
        return []
    found = []
    providers: dict[int, Any] = {}
    for record in nb.circuits.circuits.filter(site_id=site.id):
        pid = record.provider.id
        if pid not in providers:
            providers[pid] = nb.circuits.providers.get(pid)
        end = next(iter(nb.circuits.circuit_terminations.filter(circuit_id=record.id, term_side="A")), None)
        found.append(_circuit(record, providers[pid], (end.description if end else "") or ""))
    return sorted(found, key=lambda c: (c["role"] != "primary", c["port"]))


def all_circuits(nb: Any, tag: str) -> dict[str, dict[str, Any]]:
    """cid -> {cid, provider, site} for every circuit the add-on created."""
    out = {}
    for record in nb.circuits.circuits.filter(tag=tag):
        end = next(iter(nb.circuits.circuit_terminations.filter(circuit_id=record.id, term_side="A")), None)
        site = getattr(end, "site", None) or getattr(end, "termination", None)
        out[record.cid] = {
            "cid": record.cid,
            "provider": record.provider.name,
            "site": getattr(site, "slug", "") or "",
            "lands_on": (end.description if end else "") or "",
        }
    return out


def on_port(circuits: list[dict[str, Any]], port: str) -> dict[str, Any] | None:
    """The circuit landing on a firewall port ("wan1" or "WAN1")."""
    return next((c for c in circuits if c["port"].lower() == (port or "").lower()), None)
