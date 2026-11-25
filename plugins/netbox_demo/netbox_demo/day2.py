"""Day 2 operations and drift detection (INIT-001/SPEC-005).

Two halves, both pure enough to test without a switch:

* `plan_*` — given a request, return the NetBox change and the exact per-device
  EOS commands. No side effects.
* `drift_*` — given rendered intent and a live `show running-config`, return what
  differs. No writes.

The two-step workflow that uses them is deliberate: a demo must be able to show
"NetBox says X, the switch says Y" *before* anything is pushed, so
`DriftItem` exists as a first-class result rather than a log line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import design


@dataclass(frozen=True)
class DriftItem:
    """One difference between intent and reality."""

    device: str
    category: str
    detail: str
    intent: str = ""
    actual: str = ""
    remediation: tuple[str, ...] = ()

    @property
    def is_missing(self) -> bool:
        return not self.actual


@dataclass
class Day2Plan:
    """A staged Day 2 change: what NetBox will say, and what each switch will do."""

    operation: str
    title: str
    values: dict[str, Any] = field(default_factory=dict)
    netbox_steps: list[str] = field(default_factory=list)
    eos_commands: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def devices(self) -> list[str]:
        return sorted(self.eos_commands)

    def preview(self) -> str:
        lines: list[str] = [f"{self.title}", ""]
        lines.append("NetBox (Source of Truth) will change:")
        lines += [f"  - {step}" for step in self.netbox_steps] or ["  (nothing)"]
        lines.append("")
        for device in self.devices:
            lines.append(f"{device}:")
            lines += [f"  {cmd}" for cmd in self.eos_commands[device]]
            lines.append("")
        if self.warnings:
            lines.append("Warnings:")
            lines += [f"  ! {warning}" for warning in self.warnings]
        return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #

OPERATIONS: dict[str, dict[str, str]] = {
    "add_vlan": {
        "label": "Add tenant VLAN + VXLAN VNI + anycast SVI",
        "help": "Creates the segment in NetBox and renders it on every leaf.",
    },
    "remove_vlan": {
        "label": "Remove tenant VLAN + VNI + SVI",
        "help": "Removes the segment from NetBox and every leaf.",
    },
    "set_server_port": {
        "label": "Configure a server access port",
        "help": "Sets description, VLAN, MTU and admin state on a leaf's server port.",
    },
    "apply_baseline": {
        "label": "Apply standard fabric baseline",
        "help": "Pushes syslog, SNMP, NTP, DNS, LLDP and hardening to every switch.",
    },
}


def _validate_vlan(values: dict[str, Any]) -> tuple[int, str, str, int]:
    """Validate VLAN inputs. Raises ValueError with a message an operator can act on."""
    try:
        vid = int(values.get("vlan_id", 0))
    except (TypeError, ValueError):
        raise ValueError("vlan_id must be a number") from None
    if vid == 4094:
        raise ValueError("VLAN 4094 is reserved for the MLAG peer link")
    if not (2 <= vid <= 4093):
        raise ValueError("vlan_id must be between 2 and 4093")

    name = str(values.get("name", "")).strip().upper().replace(" ", "_")
    if not name:
        raise ValueError("name is required")
    if not re.fullmatch(r"[A-Z0-9_\-]{2,32}", name):
        raise ValueError("name may contain A-Z, 0-9, underscore and hyphen only (2-32 chars)")

    svi = str(values.get("svi", "")).strip()
    if not svi:
        raise ValueError("svi is required, e.g. 10.100.300.1/24")
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}/\d{1,2}", svi):
        raise ValueError("svi must look like 10.100.300.1/24")

    try:
        mtu = int(values.get("mtu", 9000))
    except (TypeError, ValueError):
        raise ValueError("mtu must be a number") from None
    if not (1500 <= mtu <= 9214):
        raise ValueError("mtu must be between 1500 and 9214")

    return vid, name, svi, mtu


def plan_add_vlan(values: dict[str, Any]) -> Day2Plan:
    """A new tenant segment: NetBox VLAN, VNI on each VTEP, anycast SVI."""
    vid, name, svi, mtu = _validate_vlan(values)
    vni = design.vni_for_vlan(vid)
    leaves = list(design.LEAVES)

    commands: dict[str, list[str]] = {}
    for leaf in leaves:
        commands[leaf] = [
            f"vlan {vid}",
            f"   name {name}",
            "   exit",
            f"interface Vlan{vid}",
            f"   description {name} anycast gateway",
            f"   mtu {mtu}",
            "   no autostate",
            f"   ip address virtual {svi}",
            "   exit",
            f"interface Vxlan1",
            f"   vxlan vlan {vid} vni {vni}",
            "   exit",
            "router bgp {}".format(design.asn_for(leaf)),
            "   address-family evpn",
            f"   vlan {vid}",
            f"      rd {design.ROUTER_IDS[leaf]}:{vid}",
            f"      route-target both {vni}:{vid}",
            "      redistribute learned",
            "      exit",
            "   exit",
        ]

    warnings: list[str] = []
    if mtu > 9000:
        warnings.append(
            f"MTU {mtu} is above the usual 9000; the underlay is {design.UNDERLAY_MTU}"
        )

    return Day2Plan(
        operation="add_vlan",
        title=f"Add VLAN {vid} ({name}) with VNI {vni}",
        values={"vlan_id": vid, "name": name, "svi": svi, "mtu": mtu, "vni": vni},
        netbox_steps=[
            f"Create VLAN {vid} named {name}",
            f"Assign it to the CORE tenant",
            f"Record VNI {vni} and gateway {svi} for rendering",
        ],
        eos_commands=commands,
        warnings=warnings,
    )


def plan_remove_vlan(values: dict[str, Any]) -> Day2Plan:
    """Remove a tenant segment from NetBox and every leaf."""
    try:
        vid = int(values.get("vlan_id", 0))
    except (TypeError, ValueError):
        raise ValueError("vlan_id must be a number") from None
    if vid <= 0:
        raise ValueError("vlan_id must be a number greater than zero")
    if vid in (seed.vlan_id for seed in design.SEED_VLANS):
        raise ValueError(
            f"VLAN {vid} is part of the seeded fabric; removing it would break the baseline"
        )
    if vid == 4094:
        raise ValueError("VLAN 4094 is the MLAG peer link and cannot be removed")

    commands: dict[str, list[str]] = {}
    for leaf in design.LEAVES:
        commands[leaf] = [
            f"interface Vxlan1",
            f"   no vxlan vlan {vid} vni {design.vni_for_vlan(vid)}",
            "   exit",
            f"no interface Vlan{vid}",
            f"no vlan {vid}",
        ]

    return Day2Plan(
        operation="remove_vlan",
        title=f"Remove VLAN {vid}",
        values={"vlan_id": vid},
        netbox_steps=[f"Remove VLAN {vid} from NetBox"],
        eos_commands=commands,
    )


def _validate_port(values: dict[str, Any]) -> tuple[str, int, str, int, bool]:
    device = str(values.get("device", "")).strip()
    if device not in design.LEAVES:
        raise ValueError(f"device must be one of: {', '.join(design.LEAVES)}")
    port = str(values.get("port", design.SERVER_PORT)).strip()
    if port != design.SERVER_PORT:
        raise ValueError(f"only {design.SERVER_PORT} may be changed by this plugin")

    try:
        vlan = int(values.get("vlan_id", 0))
    except (TypeError, ValueError):
        raise ValueError("vlan_id must be a number") from None
    if not (2 <= vlan <= 4093):
        raise ValueError("vlan_id must be between 2 and 4093")

    description = str(values.get("description", "")).strip() or "Server-Port"
    if len(description) > 60:
        raise ValueError("description must be 60 characters or fewer")

    try:
        mtu = int(values.get("mtu", 9000))
    except (TypeError, ValueError):
        raise ValueError("mtu must be a number") from None
    if not (1500 <= mtu <= 9214):
        raise ValueError("mtu must be between 1500 and 9214")

    enabled = values.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("0", "false", "no", "off")

    return device, vlan, description, mtu, bool(enabled)


def plan_set_server_port(values: dict[str, Any]) -> Day2Plan:
    """Change one leaf's server port: description, VLAN, MTU and admin state."""
    device, vlan, description, mtu, enabled = _validate_port(values)
    commands = [
        f"interface {design.SERVER_PORT}",
        f"   description {description}",
        "   switchport mode access",
        f"   switchport access vlan {vlan}",
        f"   mtu {mtu}",
        "   no shutdown" if enabled else "   shutdown",
        "   exit",
    ]
    return Day2Plan(
        operation="set_server_port",
        title=f"Set {design.SERVER_PORT} on {device} to VLAN {vlan}",
        values={
            "device": device,
            "port": design.SERVER_PORT,
            "vlan_id": vlan,
            "description": description,
            "mtu": mtu,
            "enabled": enabled,
        },
        netbox_steps=[
            f"Set {design.SERVER_PORT} description to {description}",
            f"Set untagged VLAN to {vlan}",
            f"Record MTU {mtu} and admin state {'up' if enabled else 'down'}",
        ],
        eos_commands={device: commands},
        warnings=[] if enabled else [f"{design.SERVER_PORT} will be administratively disabled"],
    )


def plan_apply_baseline(values: dict[str, Any] | None = None) -> Day2Plan:
    """Push the standard services block to every switch."""
    from .intent import service_baseline

    baseline = service_baseline()
    commands: dict[str, list[str]] = {}
    for device in design.ALL_DEVICES:
        commands[device] = [
            f"logging host {baseline.syslog_host} {baseline.syslog_port}",
            f"logging vrf MGMT host {baseline.syslog_host}",
            f"snmp-server community {baseline.snmp_community} ro",
            f"snmp-server contact {baseline.snmp_contact}",
            f"snmp-server location {baseline.snmp_location}",
            *[f"ntp server vrf MGMT {server}" for server in baseline.ntp_servers],
            *[f"ip name-server vrf MGMT {server}" for server in baseline.dns_servers],
            f"ip domain-name {baseline.domain_name}",
            "lldp run",
            f"lldp timer {baseline.lldp_timer}",
            f"lldp hold-time {baseline.lldp_hold_time}",
            f"errdisable recovery interval {baseline.errdisable_interval}",
        ]
    return Day2Plan(
        operation="apply_baseline",
        title="Apply standard fabric baseline to all six switches",
        values=dict(values or {}),
        netbox_steps=["Update the fabric-global config context with these values"],
        eos_commands=commands,
    )


PLANNERS = {
    "add_vlan": plan_add_vlan,
    "remove_vlan": plan_remove_vlan,
    "set_server_port": plan_set_server_port,
    "apply_baseline": plan_apply_baseline,
}


def plan(operation: str, values: dict[str, Any] | None = None) -> Day2Plan:
    """Dispatch to the planner for `operation`."""
    planner = PLANNERS.get(operation)
    if planner is None:
        raise ValueError(f"Unknown operation {operation!r}; valid: {', '.join(PLANNERS)}")
    return planner(values or {})


# --------------------------------------------------------------------------- #
# Drift detection against a live running-config
# --------------------------------------------------------------------------- #


def _parse_running_config(text: str) -> dict[str, Any]:
    """Parse the parts of an EOS running-config that drift detection compares.

    EOS indents a block's sub-commands and starts every top-level command at
    column zero, so indentation is what tells a block body from a global command.
    Tracking that (rather than "reset on the next header") is what keeps service
    lines after an `mlag configuration` block from being swallowed by it.

    Returns:
        vlans:       {vid: name}
        interfaces:  {name: {"description", "enabled", "mtu", "vlan", "channel"}}
        vxlan_vnis:  {vlan_id: vni}
        mlag:        {"domain_id", "peer_address", "enabled"}
        services:    {"snmp_community", "syslog_hosts", "ntp_servers", "lldp"}
    """
    vlans: dict[int, str] = {}
    interfaces: dict[str, dict[str, Any]] = {}
    vxlan_vnis: dict[int, int] = {}
    mlag: dict[str, Any] = {"enabled": False}
    services: dict[str, Any] = {"syslog_hosts": [], "ntp_servers": [], "lldp": False}

    kind: str | None = None
    current: str | None = None

    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("!"):
            continue

        indented = raw[:1] in (" ", "\t")
        line = raw.strip()

        if not indented:
            # A new top-level command: it either opens a block or is global.
            kind, current = None, None
            if line.startswith("interface "):
                current = line.split(None, 1)[1]
                interfaces.setdefault(
                    current,
                    {"description": "", "enabled": True, "mtu": None, "vlan": None, "channel": None},
                )
                kind = "interface"
                continue
            if line.startswith("vlan ") and line.split()[1].isdigit():
                current = line.split()[1]
                vlans.setdefault(int(current), "")
                kind = "vlan"
                continue
            if line == "mlag configuration":
                mlag["enabled"] = True
                kind = "mlag"
                continue
            if line.startswith("router bgp "):
                kind = "bgp"
                continue
            # Global command: fall through to the service checks below.
        else:
            if kind == "vlan" and current and line.startswith("name "):
                vlans[int(current)] = line.split(None, 1)[1]
                continue
            if kind == "interface" and current:
                intf = interfaces[current]
                if line.startswith("description "):
                    intf["description"] = line[len("description ") :]
                elif line == "shutdown":
                    intf["enabled"] = False
                elif line == "no shutdown":
                    intf["enabled"] = True
                elif line.startswith("mtu "):
                    try:
                        intf["mtu"] = int(line.split()[1])
                    except (IndexError, ValueError):
                        pass
                elif line.startswith("switchport access vlan "):
                    try:
                        intf["vlan"] = int(line.split()[-1])
                    except ValueError:
                        pass
                elif line.startswith("channel-group "):
                    intf["channel"] = line.split()[1]
                elif line.startswith("vxlan vlan ") and current == "Vxlan1":
                    parts = line.split()
                    # vxlan vlan <id> vni <vni>
                    if len(parts) >= 5 and parts[2].isdigit() and parts[4].isdigit():
                        vxlan_vnis[int(parts[2])] = int(parts[4])
                continue
            if kind == "mlag":
                if line.startswith("domain-id "):
                    mlag["domain_id"] = line.split(None, 1)[1]
                elif line.startswith("peer-address "):
                    mlag["peer_address"] = line.split(None, 1)[1]
                continue
            # Indented under an unknown block (BGP, route-map, ...): not ours.
            continue

        # Global services.
        if line.startswith("snmp-server community "):
            services["snmp_community"] = line.split()[2]
        elif line.startswith("logging host "):
            services["syslog_hosts"].append(line.split()[2])
        elif line.startswith("ntp server "):
            services["ntp_servers"].append(line.split()[-1])
        elif line == "lldp run":
            services["lldp"] = True

    return {
        "vlans": vlans,
        "interfaces": interfaces,
        "vxlan_vnis": vxlan_vnis,
        "mlag": mlag,
        "services": services,
    }


def detect_drift(device: str, intent_config: str, running_config: str) -> list[DriftItem]:
    """Compare rendered intent against a live running-config for one device.

    Only intent the plugin actually owns is compared: VLANs, SVI addresses,
    server-port state, VXLAN VNIs, MLAG membership and the service baseline.
    Anything else on the switch is left alone, because a switch legitimately
    holds state NetBox does not model.
    """
    expected = _parse_running_config(intent_config)
    actual = _parse_running_config(running_config)
    drift: list[DriftItem] = []

    # VLANs present in intent but absent (or renamed) on the box.
    for vid, name in sorted(expected["vlans"].items()):
        if vid not in actual["vlans"]:
            drift.append(
                DriftItem(
                    device=device,
                    category="vlan",
                    detail=f"VLAN {vid} ({name}) is in NetBox intent but missing on the switch",
                    intent=f"vlan {vid} name {name}",
                    remediation=(f"vlan {vid}", f"   name {name}"),
                )
            )
        elif name and actual["vlans"][vid] and actual["vlans"][vid] != name:
            drift.append(
                DriftItem(
                    device=device,
                    category="vlan",
                    detail=f"VLAN {vid} is named {actual['vlans'][vid]} on the switch, {name} in intent",
                    intent=name,
                    actual=actual["vlans"][vid],
                    remediation=(f"vlan {vid}", f"   name {name}"),
                )
            )

    # VLANs the switch has that nothing asked for.
    for vid in sorted(set(actual["vlans"]) - set(expected["vlans"])):
        if vid in IGNORED_VLAN_IDS:
            continue
        drift.append(
            DriftItem(
                device=device,
                category="vlan",
                detail=f"VLAN {vid} exists on the switch but not in NetBox intent",
                actual=f"vlan {vid} {actual['vlans'].get(vid, '')}".strip(),
                remediation=(f"no vlan {vid}",),
            )
        )

    # Per-interface comparison, restricted to interfaces intent mentions.
    for name, want in sorted(expected["interfaces"].items()):
        have = actual["interfaces"].get(name)
        if have is None:
            drift.append(
                DriftItem(
                    device=device,
                    category="interface",
                    detail=f"{name} is in NetBox intent but missing on the switch",
                    intent=name,
                )
            )
            continue

        if want["enabled"] != have["enabled"]:
            drift.append(
                DriftItem(
                    device=device,
                    category="interface",
                    detail=(
                        f"{name} is {'up' if have['enabled'] else 'shut down'} on the switch, "
                        f"intent says {'up' if want['enabled'] else 'shut down'}"
                    ),
                    intent=f"enabled={want['enabled']}",
                    actual=f"enabled={have['enabled']}",
                    remediation=(f"interface {name}", "   no shutdown" if want["enabled"] else "   shutdown"),
                )
            )

        if want["description"] and have["description"] != want["description"]:
            drift.append(
                DriftItem(
                    device=device,
                    category="interface",
                    detail=(
                        f"{name} description is {have['description'] or 'empty'}, "
                        f"intent says {want['description']}"
                    ),
                    intent=want["description"],
                    actual=have["description"],
                    remediation=(f"interface {name}", f"   description {want['description']}"),
                )
            )

        if want["mtu"] and have["mtu"] and want["mtu"] != have["mtu"]:
            drift.append(
                DriftItem(
                    device=device,
                    category="interface",
                    detail=f"{name} MTU is {have['mtu']} on the switch, intent says {want['mtu']}",
                    intent=str(want["mtu"]),
                    actual=str(have["mtu"]),
                    remediation=(f"interface {name}", f"   mtu {want['mtu']}"),
                )
            )

        if want["vlan"] and have["vlan"] and want["vlan"] != have["vlan"]:
            drift.append(
                DriftItem(
                    device=device,
                    category="interface",
                    detail=f"{name} is in VLAN {have['vlan']}, intent says {want['vlan']}",
                    intent=str(want["vlan"]),
                    actual=str(have["vlan"]),
                    remediation=(f"interface {name}", f"   switchport access vlan {want['vlan']}"),
                )
            )

    # VXLAN mappings.
    for vid, vni in sorted(expected["vxlan_vnis"].items()):
        if vid not in actual["vxlan_vnis"]:
            drift.append(
                DriftItem(
                    device=device,
                    category="vxlan",
                    detail=f"VLAN {vid} is not mapped to VNI {vni} on the switch",
                    intent=f"vlan {vid} vni {vni}",
                    remediation=("interface Vxlan1", f"   vxlan vlan {vid} vni {vni}"),
                )
            )
        elif actual["vxlan_vnis"][vid] != vni:
            drift.append(
                DriftItem(
                    device=device,
                    category="vxlan",
                    detail=(
                        f"VLAN {vid} maps to VNI {actual['vxlan_vnis'][vid]} on the switch, "
                        f"intent says {vni}"
                    ),
                    intent=str(vni),
                    actual=str(actual["vxlan_vnis"][vid]),
                    remediation=(
                        "interface Vxlan1",
                        f"   no vxlan vlan {vid} vni {actual['vxlan_vnis'][vid]}",
                        f"   vxlan vlan {vid} vni {vni}",
                    ),
                )
            )

    for vid in sorted(set(actual["vxlan_vnis"]) - set(expected["vxlan_vnis"])):
        drift.append(
            DriftItem(
                device=device,
                category="vxlan",
                detail=f"VLAN {vid} is mapped to VNI {actual['vxlan_vnis'][vid]} but is not in intent",
                actual=f"vlan {vid} vni {actual['vxlan_vnis'][vid]}",
                remediation=("interface Vxlan1", f"   no vxlan vlan {vid} vni {actual['vxlan_vnis'][vid]}"),
            )
        )

    # MLAG.
    if expected["mlag"]["enabled"]:
        if not actual["mlag"]["enabled"]:
            drift.append(
                DriftItem(
                    device=device,
                    category="mlag",
                    detail="MLAG is configured in intent but not on the switch",
                    intent="mlag configuration",
                )
            )
        elif (
            expected["mlag"].get("peer_address")
            and actual["mlag"].get("peer_address")
            and expected["mlag"]["peer_address"] != actual["mlag"]["peer_address"]
        ):
            drift.append(
                DriftItem(
                    device=device,
                    category="mlag",
                    detail=(
                        f"MLAG peer-address is {actual['mlag']['peer_address']}, "
                        f"intent says {expected['mlag']['peer_address']}"
                    ),
                    intent=expected["mlag"]["peer_address"],
                    actual=actual["mlag"]["peer_address"],
                    remediation=("mlag configuration", f"   peer-address {expected['mlag']['peer_address']}"),
                )
            )

    # Service baseline.
    want_snmp = expected["services"].get("snmp_community")
    have_snmp = actual["services"].get("snmp_community")
    if want_snmp and want_snmp != have_snmp:
        drift.append(
            DriftItem(
                device=device,
                category="services",
                detail=f"SNMP community is {have_snmp or 'unset'}, intent says {want_snmp}",
                intent=want_snmp,
                actual=have_snmp or "",
                remediation=(f"snmp-server community {want_snmp} ro",),
            )
        )

    for host in expected["services"].get("syslog_hosts", []):
        if host not in actual["services"].get("syslog_hosts", []):
            drift.append(
                DriftItem(
                    device=device,
                    category="services",
                    detail=f"Syslog host {host} is not configured on the switch",
                    intent=host,
                    remediation=(f"logging host {host}",),
                )
            )

    for server in expected["services"].get("ntp_servers", []):
        if server not in actual["services"].get("ntp_servers", []):
            drift.append(
                DriftItem(
                    device=device,
                    category="services",
                    detail=f"NTP server {server} is not configured on the switch",
                    intent=server,
                    remediation=(f"ntp server vrf MGMT {server}",),
                )
            )

    if expected["services"].get("lldp") and not actual["services"].get("lldp"):
        drift.append(
            DriftItem(
                device=device,
                category="services",
                detail="LLDP is enabled in intent but not running on the switch",
                intent="lldp run",
                remediation=("lldp run",),
            )
        )

    return drift


#: Commands that gather the running-config evidence drift detection needs.
DRIFT_READ_COMMANDS: tuple[str, ...] = ("show running-config",)

#: VLAN IDs the fabric creates implicitly, so their presence on a switch is not
#: drift: the MLAG peer link, plus EOS's own default VLAN.
IGNORED_VLAN_IDS: frozenset[int] = frozenset({1, 4094})


def summarize_drift(items: list[DriftItem]) -> dict[str, Any]:
    """Counts by category, for the UI and for a compact job summary."""
    by_category: dict[str, int] = {}
    for item in items:
        by_category[item.category] = by_category.get(item.category, 0) + 1
    return {
        "total": len(items),
        "by_category": by_category,
        "devices": sorted({item.device for item in items}),
    }
