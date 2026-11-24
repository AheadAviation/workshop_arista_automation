"""Real-world Arista EVPN/VXLAN Day 2 operation catalogue (INIT-005/SPEC-001).

Six genuine Day 2 operations on a NetBox-driven EVPN/VXLAN leaf-spine fabric:

1. `provision_vlan`            — Provision a new Tenant VLAN / VNI (L2 + optional L3 SVI in VRF CORE)
2. `configure_port`            — Configure or reassign a server-facing leaf switch port (access/trunk/MTU/state)
3. `provision_mlag_portchannel`— Provision a dual-homed server/storage MLAG Port-Channel across an MLAG pair (with real MTU)
4. `update_fabric_services`    — Add or remove an NTP or DNS server across all 6 fabric switches
5. `retire_vlan`               — Decommission a Tenant VLAN / VNI across all leaves and NetBox
6. `remediate_drift`           — Detect and remediate live switch drift against NetBox intent

Every operation is Source-of-Truth-first: inputs naming network objects come live from NetBox,
Preview shows both the NetBox update and exact per-switch EOS CLI diff, and Approve & Apply
updates NetBox first, pushes over eAPI, verifies live state, and writes an audit entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

OPERATION_KINDS: tuple[str, ...] = (
    "provision_vlan",
    "configure_port",
    "provision_mlag_portchannel",
    "update_fabric_services",
    "retire_vlan",
    "remediate_drift",
)

FIELD_SOURCES: tuple[str, ...] = (
    "ipam.vlan",          # VLANs from NetBox IPAM merged with tenant-core config context
    "dcim.leaf",          # Leaf switches from NetBox (leaf-01..leaf-04)
    "dcim.interface",     # Server-facing interfaces on leaf switches (filtered by selected device)
    "mlag.pair",          # Fabric MLAG pairs (MLAG-PAIR-1, MLAG-PAIR-2)
    "static",             # Fixed validated choices (MTU, mode, admin state, service type)
    "text",               # Validated text input (new VLAN name, description, SVI CIDR, NTP/DNS IP)
    "number",             # Validated integer input (new VLAN ID, Port-Channel ID)
)


@dataclass(frozen=True)
class Field:
    """One input an operation requires."""

    key: str
    label: str
    source: str
    help: str = ""
    required: bool = True
    choices: tuple[tuple[str, str], ...] = ()
    default: str = ""
    placeholder: str = ""
    #: Optional parent field key (e.g. `device`) when choices depend on another field.
    depends_on: str = ""

    def __post_init__(self) -> None:
        if self.source not in FIELD_SOURCES:
            raise ValueError(f"{self.key}: unknown field source {self.source!r}")
        if self.source == "static" and not self.choices:
            raise ValueError(f"{self.key}: a static field needs choices")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "source": self.source,
            "help": self.help,
            "required": self.required,
            "default": self.default,
            "placeholder": self.placeholder,
            "depends_on": self.depends_on,
            "choices": [{"value": v, "label": lbl} for v, lbl in self.choices],
        }


@dataclass(frozen=True)
class Operation:
    """A real-world Day 2 operation and its declared inputs."""

    key: str
    name: str
    kind: str
    summary: str
    sentence: str
    fields: tuple[Field, ...] = field(default_factory=tuple)
    vlan_field: str = ""

    def __post_init__(self) -> None:
        if self.kind not in OPERATION_KINDS:
            raise ValueError(f"{self.key}: unknown kind {self.kind!r}")
        keys = {f.key for f in self.fields}
        if self.vlan_field and self.vlan_field not in keys:
            raise ValueError(f"{self.key}: vlan_field {self.vlan_field!r} is not a declared field")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "kind": self.kind,
            "summary": self.summary,
            "fields": [f.to_dict() for f in self.fields],
        }


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        key="provision_vlan",
        name="Provision Tenant VLAN / VNI",
        kind="provision_vlan",
        summary="Create a new EVPN/VXLAN tenant VLAN across all 4 leaves (VLAN, Vxlan1 VNI, optional Anycast SVI in VRF CORE) and record it in NetBox.",
        sentence="Provision VLAN {vlan_id} ({vlan_name}, VNI {vni}) across all 4 leaves{svi_note}.",
        fields=(
            Field(
                key="vlan_id",
                label="VLAN ID",
                source="number",
                placeholder="e.g. 150",
                help="Unused VLAN ID between 2 and 4000 (VNI defaults to 10000 + VLAN ID).",
            ),
            Field(
                key="vlan_name",
                label="VLAN Name",
                source="text",
                placeholder="e.g. APP_TIER",
                help="Short alphanumeric/underscore name (max 32 chars, no spaces).",
            ),
            Field(
                key="svi_ip",
                label="Anycast Gateway IP/Prefix (optional)",
                source="text",
                required=False,
                placeholder="e.g. 10.100.150.1/24",
                help="Leave blank for L2-only VXLAN, or enter an IPv4/prefix to create an Anycast SVI in VRF CORE.",
            ),
            Field(
                key="mtu",
                label="SVI MTU",
                source="static",
                default="9000",
                help="MTU applied to the Vlan interface when an SVI is created.",
                choices=(
                    ("9000", "9000 (jumbo)"),
                    ("1500", "1500 (standard)"),
                ),
            ),
        ),
    ),
    Operation(
        key="configure_port",
        name="Configure / Reassign Switch Port",
        kind="configure_port",
        summary="Configure a real server-facing leaf interface from NetBox: access or trunk mode, VLAN, description, MTU, and admin state.",
        sentence="Configure {device} {interface} ({mode}, VLAN {vlan}, MTU {mtu}, {admin_state}).",
        vlan_field="vlan",
        fields=(
            Field(
                key="device",
                label="Leaf Switch",
                source="dcim.leaf",
                help="Live leaf switches from NetBox DCIM.",
            ),
            Field(
                key="interface",
                label="Server-Facing Interface",
                source="dcim.interface",
                depends_on="device",
                help="Downlink ports on the selected leaf (uplinks & MLAG peer-links are protected).",
            ),
            Field(
                key="mode",
                label="Switchport Mode",
                source="static",
                default="access",
                help="Access port for a single VLAN, or 802.1Q trunk.",
                choices=(
                    ("access", "Access (single VLAN)"),
                    ("trunk", "Trunk (tagged VLAN)"),
                ),
            ),
            Field(
                key="vlan",
                label="VLAN",
                source="ipam.vlan",
                help="Populated live from NetBox IPAM and tenant-core config context.",
            ),
            Field(
                key="mtu",
                label="Interface MTU",
                source="static",
                default="9214",
                choices=(
                    ("9214", "9214 (jumbo)"),
                    ("1500", "1500 (standard)"),
                ),
            ),
            Field(
                key="admin_state",
                label="Admin State",
                source="static",
                default="up",
                choices=(
                    ("up", "Enabled (no shutdown)"),
                    ("down", "Shutdown (administratively down)"),
                ),
            ),
            Field(
                key="description",
                label="Port Description",
                source="text",
                required=False,
                placeholder="e.g. ESXi-01 NIC0",
                help="Written to both the NetBox interface and switch interface description.",
            ),
        ),
    ),
    Operation(
        key="provision_mlag_portchannel",
        name="Provision Dual-Homed MLAG Port-Channel",
        kind="provision_mlag_portchannel",
        summary="Turn up a dual-homed server or storage LACP Port-Channel across both leaves of an MLAG pair with real MTU and trunk VLAN.",
        sentence="Provision Port-Channel{po_id} (MLAG {po_id}) on {pair_label} using {member_interface}, VLAN {vlan}, MTU {mtu}.",
        vlan_field="vlan",
        fields=(
            Field(
                key="pair",
                label="MLAG Pair",
                source="mlag.pair",
                help="Both leaves in the selected pair are configured symmetrically.",
            ),
            Field(
                key="po_id",
                label="Port-Channel / MLAG ID",
                source="number",
                default="10",
                placeholder="e.g. 10",
                help="Port-Channel and MLAG ID (1–1999; 2000 is reserved for the MLAG peer-link).",
            ),
            Field(
                key="member_interface",
                label="Member Interface (same port on both leaves)",
                source="static",
                default="Ethernet5",
                help="Server-facing downlink port on each leaf in the MLAG pair.",
                choices=(
                    ("Ethernet5", "Ethernet5"),
                    ("Ethernet6", "Ethernet6"),
                    ("Ethernet7", "Ethernet7"),
                    ("Ethernet8", "Ethernet8"),
                ),
            ),
            Field(
                key="vlan",
                label="Allowed Tenant VLAN",
                source="ipam.vlan",
                help="VLAN allowed on the dual-homed trunk Port-Channel.",
            ),
            Field(
                key="mtu",
                label="Frame Size (MTU)",
                source="static",
                default="9214",
                help="Applied directly to the Port-Channel and member interfaces on both leaves.",
                choices=(
                    ("9214", "9214 (jumbo — storage / VMware)"),
                    ("1500", "1500 (standard)"),
                ),
            ),
            Field(
                key="description",
                label="Server / Array Name",
                source="text",
                required=False,
                placeholder="e.g. PURE-ARRAY-01",
                help="Applied as description on Port-Channel and member interfaces in NetBox and EOS.",
            ),
        ),
    ),
    Operation(
        key="update_fabric_services",
        name="Update Fabric Services (NTP / DNS)",
        kind="update_fabric_services",
        summary="Add or remove an NTP or DNS server in NetBox fabric-global and push to all 6 spine/leaf switches.",
        sentence="{action_word} {service_type} server {server_ip} in VRF MGMT across all 6 fabric switches.",
        fields=(
            Field(
                key="service_type",
                label="Service",
                source="static",
                default="ntp",
                choices=(
                    ("ntp", "NTP Server (VRF MGMT)"),
                    ("dns", "DNS Name Server (VRF MGMT)"),
                ),
            ),
            Field(
                key="action",
                label="Action",
                source="static",
                default="add",
                choices=(
                    ("add", "Add server"),
                    ("remove", "Remove server"),
                ),
            ),
            Field(
                key="server_ip",
                label="Server IPv4 Address",
                source="text",
                placeholder="e.g. 172.30.30.10",
                help="IPv4 address reachable from VRF MGMT.",
            ),
        ),
    ),
    Operation(
        key="retire_vlan",
        name="Retire Tenant VLAN / VNI",
        kind="retire_vlan",
        summary="Decommission an existing tenant VLAN across all 4 leaves (remove SVI, Vxlan1 VNI mapping, VLAN) and remove it from NetBox.",
        sentence="Retire VLAN {vlan} from all 4 leaves and NetBox.",
        vlan_field="vlan",
        fields=(
            Field(
                key="vlan",
                label="Tenant VLAN to Retire",
                source="ipam.vlan",
                help="Select an existing tenant VLAN from NetBox. Infrastructure MLAG VLANs (4080+) are protected.",
            ),
        ),
    ),
    Operation(
        key="remediate_drift",
        name="Detect & Remediate Config Drift",
        kind="remediate_drift",
        summary="Compare live running VLANs and trunks on the fabric against NetBox intent and remediate any drifted leaf switch.",
        sentence="Check and remediate live configuration drift against NetBox intent ({target_label}).",
        fields=(
            Field(
                key="target",
                label="Target Scope",
                source="static",
                default="all-leaves",
                help="Inspect all 4 leaves, all 6 fabric switches (including spines for NTP/DNS), or a single MLAG pair.",
                choices=(
                    ("all-leaves", "All 4 leaf switches (leaf-01..leaf-04)"),
                    ("all-fabric", "All 6 fabric switches (spines + leaves)"),
                    ("pair-1", "MLAG Pair 1 (leaf-01, leaf-02)"),
                    ("pair-2", "MLAG Pair 2 (leaf-03, leaf-04)"),
                ),
            ),
        ),
    ),
)

BY_KEY: dict[str, Operation] = {op.key: op for op in OPERATIONS}


class UnknownOperation(ValueError):
    """An operation that is not in the catalogue."""


def operation(key: str) -> Operation:
    found = BY_KEY.get(str(key or "").strip().lower())
    if found is None:
        raise UnknownOperation(
            f"{key!r} is not an operation this tool offers. "
            f"Available: {', '.join(sorted(BY_KEY))}"
        )
    return found


def operations_dicts() -> list[dict[str, Any]]:
    """The catalogue as plain data, for the page to render dynamically."""
    return [op.to_dict() for op in OPERATIONS]


__all__ = [
    "BY_KEY",
    "FIELD_SOURCES",
    "OPERATION_KINDS",
    "OPERATIONS",
    "Field",
    "Operation",
    "UnknownOperation",
    "operation",
    "operations_dicts",
]
