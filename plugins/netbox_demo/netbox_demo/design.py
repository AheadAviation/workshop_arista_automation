"""The demo fabric's design: addressing, ASNs and tenants (INIT-001/SPEC-005).

This is the underlay plan that would normally live in NetBox config contexts.
It is written down here as one explicit, testable table so the demo can always
render a coherent fabric, and so `seeder.py` has a single source to write into
NetBox rather than two places to keep in sync.

Anything an operator changes in NetBox at demo time (a new tenant VLAN, a service
baseline edit) is read back by `intent.py` and wins over the defaults here.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

#: Site the demo fabric lives in. Distinct from any other site so the plugin can
#: scope every destructive action to it.
SITE_NAME = "NetBox Demo"
SITE_SLUG = "netbox-demo"

#: Device name prefix, matching `local-lab/lab-agent/contract.py`.
DEVICE_PREFIX = "demo-"

SPINES: tuple[str, ...] = ("demo-spine-01", "demo-spine-02")
LEAVES: tuple[str, ...] = ("demo-leaf-01", "demo-leaf-02", "demo-leaf-03", "demo-leaf-04")
ALL_DEVICES: tuple[str, ...] = SPINES + LEAVES

#: Management network, deliberately not the workshop lab's 172.30.30.0/24.
MGMT_SUBNET = "172.31.30.0/24"
MGMT_GATEWAY = "172.31.30.1"

MGMT_IPS: dict[str, str] = {
    "demo-spine-01": "172.31.30.11",
    "demo-spine-02": "172.31.30.12",
    "demo-leaf-01": "172.31.30.21",
    "demo-leaf-02": "172.31.30.22",
    "demo-leaf-03": "172.31.30.23",
    "demo-leaf-04": "172.31.30.24",
}

#: Underlay: /31 point-to-point links on the 100.64.0.0/24 range.
UNDERLAY_SUBNET = "100.64.0.0/24"
UNDERLAY_MTU = 9214

#: Router IDs and VTEP addresses.
ROUTER_IDS: dict[str, str] = {
    "demo-spine-01": "10.255.0.1",
    "demo-spine-02": "10.255.0.2",
    "demo-leaf-01": "10.255.0.11",
    "demo-leaf-02": "10.255.0.12",
    "demo-leaf-03": "10.255.0.13",
    "demo-leaf-04": "10.255.0.14",
}

VTEP_IPS: dict[str, str] = {
    "demo-leaf-01": "10.255.1.1",
    "demo-leaf-02": "10.255.1.2",
    "demo-leaf-03": "10.255.1.3",
    "demo-leaf-04": "10.255.1.4",
}

SPINE_ASN = 65000
LEAF_ASN_BASE = 65001

ROLE_SLUGS = {"spine": "demo-spine", "leaf": "demo-leaf"}


@dataclass(frozen=True)
class FabricLink:
    """One routed underlay link, with a /31 and both ends' addresses."""

    left: str
    left_port: str
    left_address: str
    right: str
    right_port: str
    right_address: str
    description: str


def _build_fabric_links() -> list[FabricLink]:
    """Spine/leaf underlay links, one /31 each, allocated deterministically.

    Allocation starts at 100.64.0.0 and walks leaf-major, so the same address is
    produced on every run and in every process. A deterministic plan is what lets
    NetBox intent and rendered configuration be compared without a diff storm.
    """
    block = ipaddress.ip_network(UNDERLAY_SUBNET)
    cursor = block.network_address
    links: list[FabricLink] = []

    for leaf_index, leaf in enumerate(LEAVES):
        for spine_index, spine in enumerate(SPINES):
            link = ipaddress.ip_network((cursor, 31))
            spine_ip, leaf_ip = link.network_address + 0, link.network_address + 1
            links.append(
                FabricLink(
                    left=spine,
                    left_port=f"Ethernet{leaf_index + 1}",
                    left_address=f"{spine_ip}/{link.prefixlen}",
                    right=leaf,
                    right_port=f"Ethernet{spine_index + 1}",
                    right_address=f"{leaf_ip}/{link.prefixlen}",
                    description=f"Fabric link to {leaf}",
                )
            )
            cursor = link.broadcast_address + 1

    return links


FABRIC_LINKS: list[FabricLink] = _build_fabric_links()

#: MLAG pairs: (leaf_a, leaf_b, domain_id, peer_link_ports_on_a, on_b).
MLAG_PAIRS: tuple[tuple[str, str, int], ...] = (
    ("demo-leaf-01", "demo-leaf-02", 1),
    ("demo-leaf-03", "demo-leaf-04", 2),
)

#: Ports used for the MLAG peer link on each member of a pair.
MLAG_PEER_PORTS: dict[str, tuple[str, str]] = {
    "demo-leaf-01": ("Ethernet3", "Ethernet4"),
    "demo-leaf-02": ("Ethernet3", "Ethernet4"),
    "demo-leaf-03": ("Ethernet3", "Ethernet4"),
    "demo-leaf-04": ("Ethernet3", "Ethernet4"),
}

#: Server-facing ports (the ones the demo safely changes at Day 2).
SERVER_PORT = "Ethernet6"

#: Tenant L2 segments seeded into NetBox and rendered on every leaf.
@dataclass(frozen=True)
class SeedVlan:
    vlan_id: int
    name: str
    vni: int
    svi: str
    description: str
    mtu: int = 9000


SEED_VLANS: tuple[SeedVlan, ...] = (
    SeedVlan(100, "SERVERS", 10100, "10.100.100.1/24", "Server segment"),
    SeedVlan(110, "MGMT_SERVERS", 10110, "10.100.110.1/24", "Management servers", mtu=1500),
    SeedVlan(120, "STORAGE", 10120, "10.100.120.1/24", "Storage segment"),
    SeedVlan(200, "VOIP", 10200, "10.100.200.1/24", "Voice segment", mtu=1500),
)

#: Base for VXLAN VNIs derived for VLANs added later (VLAN 300 -> 10300).
BASE_L2_VNI = 10000

#: Anycast gateway MAC used by every leaf's SVI.
ANYCAST_GATEWAY_MAC = "00:1c:73:00:00:99"


def vni_for_vlan(vlan_id: int) -> int:
    """Derive a VNI for a VLAN that was not in the seed set."""
    for seed in SEED_VLANS:
        if seed.vlan_id == vlan_id:
            return seed.vni
    return BASE_L2_VNI + vlan_id


def asn_for(device: str) -> int:
    """Every leaf is its own AS; both spines share the route-server AS."""
    if device in SPINES:
        return SPINE_ASN
    return LEAF_ASN_BASE + LEAVES.index(device)


def role_for(device: str) -> str:
    return "spine" if device in SPINES else "leaf"


def peer_address_of(device: str) -> str | None:
    """The MLAG peer's peer-link address, or None when the leaf is unpaired."""
    pair = mlag_for(device)
    return pair[2] if pair else None


def mlag_for(device: str) -> tuple[int, str, str] | None:
    """Return (domain_id, local_address, peer_address) for a leaf, if it is paired."""
    for a, b, domain_id in MLAG_PAIRS:
        if device == a:
            return domain_id, "169.254.1.1/30", "169.254.1.2"
        if device == b:
            return domain_id, "169.254.1.2/30", "169.254.1.1"
    return None
