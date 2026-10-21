#!/usr/bin/env python3
"""Day 2 demo scenarios for the Arista fabric: change NetBox, then regenerate and push.

Each scenario makes the NetBox edit that wiki/Day-2-Operations.md describes and
remembers the old values, so `revert` puts NetBox back exactly. The device side is
always the same afterwards: generate, preview the diff, push, validate.

    python scripts/day2_demo.py list
    python scripts/day2_demo.py apply add-vlan
    python scripts/day2_demo.py revert add-vlan      # or: revert all
    python scripts/day2_demo.py status
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

import pynetbox

STATE = Path(__file__).resolve().parent.parent / "output" / "changes" / "day2_backup.json"

NEW_VLAN = {
    "vlan_number": 150,
    "vlan_name": "NEW_VLAN",
    "vlan_ip": "10.100.150.1/24",
    "mtu": 9000,
    "description": "New application VLAN",
}
NEW_NTP = "172.30.30.2"
NEW_GATEWAY = "172.30.30.254"
DESCRIPTION = ("spine-01", "eth1", "To leaf-01 eth1 - changed in NetBox")
PAIR_ASN = (("leaf-03", "leaf-04"), 65012)
BAD_ASN = ("leaf-03", 65099)


def _context(nb: Any, name: str) -> Any:
    cc = nb.extras.config_contexts.get(name=name)
    if cc is None:
        raise SystemExit(f"Config context '{name}' not found. Run the fabric import first.")
    return cc


def _device(nb: Any, name: str) -> Any:
    dev = nb.dcim.devices.get(name=name)
    if dev is None:
        raise SystemExit(f"Device '{name}' not found in NetBox.")
    return dev


def _save_context(nb: Any, name: str, saved: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    cc = _context(nb, name)
    saved.setdefault("contexts", {})[name] = copy.deepcopy(cc.data)
    return cc, copy.deepcopy(cc.data)


def _save_asn(nb: Any, name: str, saved: dict[str, Any]) -> Any:
    dev = _device(nb, name)
    saved.setdefault("asns", {})[name] = dev.custom_fields.get("bgp_asn")
    return dev


# ------------------------------------------------------------------ scenarios
def add_vlan(nb: Any, saved: dict[str, Any]) -> list[str]:
    cc, data = _save_context(nb, "tenant-core", saved)
    segments = data["tenants"][0]["l2_segments"]
    if any(s.get("vlan_number") == NEW_VLAN["vlan_number"] for s in segments):
        raise SystemExit("VLAN 150 is already in tenant-core.")
    segments.append(dict(NEW_VLAN))
    cc.update({"data": data})
    return [
        "Config context tenant-core: added VLAN 150 NEW_VLAN, gateway 10.100.150.1/24, VNI 10150.",
        'Check: docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show vlan 150"',
        'Check: docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show vxlan vni"',
    ]


def add_ntp(nb: Any, saved: dict[str, Any]) -> list[str]:
    cc, data = _save_context(nb, "fabric-global", saved)
    servers = data.setdefault("management", {}).setdefault("ntp_servers", [])
    if NEW_NTP in servers:
        raise SystemExit(f"{NEW_NTP} is already an NTP server.")
    servers.append(NEW_NTP)
    cc.update({"data": data})
    return [
        f"Config context fabric-global: NTP servers are now {', '.join(servers)} (all 6 switches).",
        'Check: docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show running-config section ntp"',
    ]


def mgmt_gateway(nb: Any, saved: dict[str, Any]) -> list[str]:
    cc, data = _save_context(nb, "fabric-global", saved)
    data.setdefault("management", {})["gateway"] = NEW_GATEWAY
    cc.update({"data": data})
    return [
        f"Config context fabric-global: management gateway is now {NEW_GATEWAY} (all 6 switches).",
        "The switches stay reachable from the lab's own management network; revert afterwards.",
        'Check: docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ip route vrf MGMT"',
    ]


def interface_description(nb: Any, saved: dict[str, Any]) -> list[str]:
    device, name, text = DESCRIPTION
    iface = nb.dcim.interfaces.get(device=device, name=name)
    if iface is None:
        raise SystemExit(f"{device} {name} not found in NetBox.")
    saved["description"] = {"id": iface.id, "value": iface.description or ""}
    iface.update({"description": text})
    return [
        f"{device} {name} description: '{text}'.",
        f'Check: docker exec clab-netbox-arista-lab-{device} Cli -p 15 -c "show interfaces description"',
    ]


def leaf_pair_asn(nb: Any, saved: dict[str, Any]) -> list[str]:
    names, asn = PAIR_ASN
    for name in names:
        _save_asn(nb, name, saved).update({"custom_fields": {"bgp_asn": asn}})
    return [
        f"{' and '.join(names)} (one MLAG pair) move to BGP AS {asn}. Both spines follow, because "
        "their neighbor ASN comes from NetBox too.",
        'Check: docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ip bgp summary"',
    ]


def bad_asn(nb: Any, saved: dict[str, Any]) -> list[str]:
    name, asn = BAD_ASN
    _save_asn(nb, name, saved).update({"custom_fields": {"bgp_asn": asn}})
    return [
        f"A mistake on purpose: only {name} moves to AS {asn}; its MLAG partner does not.",
        "Push it and run the tests: they fail and name the sessions that dropped. Then revert and push.",
    ]


SCENARIOS: dict[str, tuple[str, Callable[[Any, dict[str, Any]], list[str]]]] = {
    "add-vlan": ("Add VLAN 150 to the CORE tenant on every leaf (config context)", add_vlan),
    "add-ntp": ("Add an NTP server for the whole fabric (config context)", add_ntp),
    "mgmt-gateway": ("Change the management default gateway (config context)", mgmt_gateway),
    "interface-description": ("Change a fabric link's description (interface)", interface_description),
    "leaf-pair-asn": ("Renumber the leaf-03/leaf-04 pair to AS 65012 (custom field)", leaf_pair_asn),
    "bad-asn": ("Break it on purpose: change one leaf of a pair (custom field)", bad_asn),
}

# What each scenario edits, so two that touch the same data are never applied together
# (reverting one would undo the other).
TOUCHES: dict[str, set[str]] = {
    "add-vlan": {"tenant-core"},
    "add-ntp": {"fabric-global"},
    "mgmt-gateway": {"fabric-global"},
    "interface-description": {"spine-01/eth1"},
    "leaf-pair-asn": {"leaf-03", "leaf-04"},
    "bad-asn": {"leaf-03"},
}


# -------------------------------------------------------------------- storage
def load() -> dict[str, Any]:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def store(state: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    if state:
        STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    elif STATE.exists():
        STATE.unlink()


def restore(nb: Any, saved: dict[str, Any]) -> list[str]:
    done = []
    for name, data in saved.get("contexts", {}).items():
        _context(nb, name).update({"data": data})
        done.append(f"config context {name}")
    for name, asn in saved.get("asns", {}).items():
        _device(nb, name).update({"custom_fields": {"bgp_asn": asn}})
        done.append(f"{name} bgp_asn {asn}")
    if "description" in saved:
        iface = nb.dcim.interfaces.get(saved["description"]["id"])
        if iface is not None:
            iface.update({"description": saved["description"]["value"]})
            done.append(f"{iface.device.name} {iface.name} description")
    return done


NEXT_STEPS = """Next, on the switches:
  .\\run.ps1 generate     # NetBox -> output/configs/*.cfg
  .\\run.ps1 diff         # what will change on each switch (nothing is applied)
  .\\run.ps1 push         # apply, check, save
  .\\run.ps1 test         # validate"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="the scenarios")
    sub.add_parser("status", help="which scenarios are applied")
    ap = sub.add_parser("apply", help="make a scenario's change in NetBox")
    ap.add_argument("scenario", choices=sorted(SCENARIOS))
    rv = sub.add_parser("revert", help="put NetBox back")
    rv.add_argument("scenario", choices=sorted(SCENARIOS) + ["all"])
    args = parser.parse_args(argv)

    if args.command == "list":
        for key, (text, _) in SCENARIOS.items():
            print(f"  {key:<22} {text}")
        return 0

    state = load()
    if args.command == "status":
        print("Applied: " + (", ".join(state) if state else "none"))
        return 0

    nb = pynetbox.api(os.environ.get("NETBOX_URL", "http://netbox:8080"), token=os.environ["NETBOX_TOKEN"])

    if args.command == "apply":
        if args.scenario in state:
            print(f"{args.scenario} is already applied. Revert it first.", file=sys.stderr)
            return 1
        clash = [n for n in state if TOUCHES[n] & TOUCHES[args.scenario]]
        if clash:
            print(f"{args.scenario} edits the same NetBox data as {', '.join(clash)}. Revert that first.", file=sys.stderr)
            return 1
        saved: dict[str, Any] = {}
        lines = SCENARIOS[args.scenario][1](nb, saved)
        state[args.scenario] = saved
        store(state)
        print("\n".join(lines))
        print("\n" + NEXT_STEPS)
        return 0

    names = list(reversed(list(state))) if args.scenario == "all" else [args.scenario]
    if not names or any(n not in state for n in names):
        print(f"Nothing to revert for {args.scenario}.", file=sys.stderr)
        return 1
    for name in names:
        done = restore(nb, state.pop(name))
        print(f"Reverted {name}: {', '.join(done)}.")
    store(state)
    print("\n" + NEXT_STEPS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
