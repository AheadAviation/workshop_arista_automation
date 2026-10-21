"""A mock Meraki dashboard that remembers what the demos did to it.

``meraki_mock.build_world`` always describes the same dashboard. The change and
level-1 demos *change* it (a firmware upgrade, a port moved to another VLAN, an
injected outage), and a later command has to see that, so the differences from the
fresh world are kept in ``output/changes/dashboard_state.json``.

``plants-meraki`` loads the world through here too, so after a change the sync
reports what the dashboard now says. ``change reset`` deletes the file.

Everything is a MOCK. Nothing here talks to a real Meraki organisation.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import meraki_mock
from .common import REPO_ROOT
from .meraki_client import MerakiClient

STATE_FILE = REPO_ROOT / "output" / "changes" / "dashboard_state.json"


def _epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def load_state(path: Path = STATE_FILE) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save_state(state: dict[str, Any], path: Path = STATE_FILE) -> None:
    if not state:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def reset_state(path: Path = STATE_FILE) -> bool:
    """Forget every change. True if there was anything to forget."""
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def apply_state(world: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Lay saved differences over a fresh world (names that no longer exist are skipped)."""
    by_name = {device["name"]: device for device in world["devices"]}
    for name, firmware in state.get("firmware", {}).items():
        if name in by_name:
            by_name[name]["firmware"] = firmware
    for item in world["availabilities"]:
        if item["name"] in state.get("status", {}):
            item["status"] = state["status"][item["name"]]
    for serial, changed in state.get("ports", {}).items():
        ports = meraki_mock.switch_ports(world, serial)
        for port_id, values in changed.items():
            if ports is not None and port_id in ports:
                ports[port_id].update(values)
    for name, count in state.get("clients", {}).items():
        if name in world["clients"]:
            world["clients"][name] = count
    return world


def diff_state(fresh: dict[str, Any], world: dict[str, Any]) -> dict[str, Any]:
    """What ``world`` has that a fresh one does not. Empty when nothing was changed."""
    state: dict[str, Any] = {}
    original = {device["name"]: device for device in fresh["devices"]}
    firmware = {
        d["name"]: d["firmware"]
        for d in world["devices"]
        if d["name"] in original and d["firmware"] != original[d["name"]]["firmware"]
    }
    was = {item["name"]: item["status"] for item in fresh["availabilities"]}
    status = {
        item["name"]: item["status"]
        for item in world["availabilities"]
        if item["name"] in was and item["status"] != was[item["name"]]
    }
    ports: dict[str, dict[str, Any]] = {}
    for serial, current in world.get("ports", {}).items():
        baseline = meraki_mock.switch_ports(fresh, serial) or {}
        changed = {pid: dict(p) for pid, p in current.items() if p != baseline.get(pid)}
        if changed:
            ports[serial] = changed
    clients = {
        name: count
        for name, count in world.get("clients", {}).items()
        if name in fresh["clients"] and count != fresh["clients"][name]
    }
    for key, value in (("firmware", firmware), ("status", status), ("ports", ports), ("clients", clients)):
        if value:
            state[key] = value
    return state


def load_world(plan: dict[str, Any], path: Path = STATE_FILE) -> dict[str, Any]:
    """The mock dashboard as it is now: the sample organisation plus every saved change."""
    return apply_state(meraki_mock.build_world(plan), load_state(path))


class Dashboard:
    """The mock, running for the length of one command, with a client pointed at it.

    ``with Dashboard(plan) as dash:`` ... ``dash.client`` for API calls. What the
    command changed is saved when the block ends.
    """

    def __init__(self, plan: dict[str, Any], path: Path = STATE_FILE):
        self.plan = plan
        self.path = path
        self.world = load_world(plan, path)
        self.server: meraki_mock.MockMeraki | None = None
        self.client: MerakiClient | None = None

    @property
    def org_id(self) -> str:
        return self.world["org"]["id"]

    def __enter__(self) -> Dashboard:
        self.server = meraki_mock.start(self.world)
        self.client = MerakiClient(self.server.base_url, meraki_mock.DEFAULT_KEY)
        return self

    def __exit__(self, *_exc: object) -> None:
        try:
            save_state(diff_state(meraki_mock.build_world(self.plan), self.world), self.path)
        finally:
            if self.server is not None:
                meraki_mock.stop(self.server)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Every device the dashboard knows, by name, with its availability."""
        assert self.client is not None
        status = {a["serial"]: a["status"] for a in self.client.availabilities(self.org_id)}
        return {
            d["name"]: {
                "name": d["name"],
                "serial": d["serial"],
                "model": d["model"],
                "network_id": d["networkId"],
                "product": d["productType"],
                "lan_ip": d.get("lanIp") or "",
                "firmware": d.get("firmware") or "",
                "status": status.get(d["serial"], "unknown"),
            }
            for d in self.client.devices(self.org_id)
        }

    def client_counts(self, names: list[str]) -> dict[str, int]:
        """Wireless clients on each named access point right now (other devices are skipped)."""
        assert self.client is not None
        serial = {d["name"]: d["serial"] for d in self.world["devices"] if d["productType"] == "wireless"}
        return {name: len(self.client.device_clients(serial[name])) for name in names if name in serial}

    def dhcp_scopes(self, site_code: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """The site's MX and the DHCP scopes it serves; (None, []) if the site has no MX."""
        assert self.client is not None
        network = meraki_mock.network_id(site_code)
        mx = sorted(
            (d for d in self.world["devices"] if d["networkId"] == network and d["productType"] == "appliance"),
            key=lambda d: d["name"],
        )
        if not mx:
            return None, []
        return mx[0], self.client.dhcp_subnets(mx[0]["serial"])

    def uplink_mbps(self, site_code: str) -> dict[str, float]:
        """Each WAN uplink's busiest direction over the latest interval, in Mbps."""
        assert self.client is not None
        history = self.client.uplink_usage(meraki_mock.network_id(site_code))
        if not history:
            return {}
        latest = history[-1]
        try:
            seconds = (_epoch(latest["endTime"]) - _epoch(latest["startTime"])) or 300
        except (KeyError, ValueError):
            seconds = 300
        return {
            item["interface"]: round(max(item["received"], item["sent"]) * 8 / seconds / 1_000_000, 1)
            for item in latest["byInterface"]
        }

    def inject_clients(self, name: str, count: int | None) -> None:
        """Simulate an access point that is up but nobody can join (None: back to normal)."""
        if name not in self.world["clients"]:
            raise LookupError(f"The dashboard has no access point named '{name}'.")
        fresh = meraki_mock.build_world(self.plan)["clients"]
        self.world["clients"][name] = fresh[name] if count is None else count

    def inject_status(self, name: str, status: str) -> None:
        """Simulate an outage (or a recovery). A demo lever the real API does not have."""
        for item in self.world["availabilities"]:
            if item["name"] == name:
                item["status"] = status
                return
        raise LookupError(f"The dashboard has no device named '{name}'.")
