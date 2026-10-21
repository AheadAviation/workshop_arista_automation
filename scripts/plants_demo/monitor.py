"""Watching the gear the Meraki dashboard cannot see (MOCK).

The Juniper and Ubiquiti switches send no Meraki alerts, so something has to ask
them. Which devices to ask, and what each one carries, comes from NetBox. The
asking is mocked here: a device answers unless ``monitor_mock.unreachable`` in the
data file lists it, or ``alert poll --simulate-down`` marked it down. Real polling
would be ping or SNMP from a box at each site, or the RMM they already pay for.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .common import REPO_ROOT

STATE_FILE = REPO_ROOT / "output" / "alerts" / "probe_state.json"
NON_MERAKI_TAGS = ("juniper", "ubiquiti")
NETWORK_ROLES = {"firewall", "access-switch", "wireless-ap"}


def load_down(plan: dict[str, Any], path: Path = STATE_FILE) -> set[str]:
    """Devices that do not answer: the data file's list plus the ones marked down."""
    marked = json.loads(path.read_text(encoding="utf-8")).get("down", []) if path.exists() else []
    return set((plan.get("monitor_mock") or {}).get("unreachable", [])) | set(marked)


def mark(name: str, down: bool, path: Path = STATE_FILE) -> None:
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    names = set(state.get("down", []))
    names = names | {name} if down else names - {name}
    if names:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"down": sorted(names)}, indent=2), encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


def targets(nb: Any, addon_tag: str) -> list[Any]:
    """Network devices from the add-on that the Meraki dashboard does not manage."""
    found = {}
    for tag in NON_MERAKI_TAGS:
        for record in nb.dcim.devices.filter(tag=tag):
            slugs = {t.slug for t in record.tags}
            if addon_tag in slugs and record.role and record.role.slug in NETWORK_ROLES:
                found[record.id] = record
    return sorted(found.values(), key=lambda r: r.name)


def probe(record: Any, down: set[str]) -> dict[str, Any]:
    """One ping, as a poller would report it. ``status``: up, down, or no-ip (cannot be polled)."""
    address = getattr(record.primary_ip4, "address", None)
    ip = address.split("/")[0] if address else ""
    if not ip:
        return {"device": record.name, "ip": "", "status": "no-ip", "detail": "no IP recorded in NetBox"}
    if record.name in down:
        return {"device": record.name, "ip": ip, "status": "down", "detail": f"ping {ip}: 3 sent, 0 received"}
    rtt = 1 + int(hashlib.sha1(record.name.encode()).hexdigest()[:2], 16) % 30  # noqa: S324
    return {"device": record.name, "ip": ip, "status": "up", "detail": f"ping {ip}: 3 of 3 answered, {rtt} ms"}
