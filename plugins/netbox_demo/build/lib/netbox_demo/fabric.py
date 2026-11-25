"""Fabric intent: the shape of a switch's configuration, independent of NetBox.

`renderer.py` turns one of these into EOS text and `intent.py` builds one from
NetBox objects. Keeping the shape separate from both means the renderer can be
tested against a literal, and the NetBox reading can change without touching
the text the switches receive.

Every field here is intent. Anything a switch invents on its own is drift, and
belongs in `day2.py` where it is compared rather than rendered.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServiceBaseline:
    """Standard fabric services every switch in the demo should carry."""

    syslog_host: str = "172.31.30.1"
    syslog_port: int = 514
    snmp_community: str = "FABRIC-RO"
    snmp_contact: str = "netops@demo.local"
    snmp_location: str = "NETBOX-DEMO-FABRIC"
    ntp_servers: tuple[str, ...] = ("172.31.30.1",)
    dns_servers: tuple[str, ...] = ("172.31.30.1",)
    domain_name: str = "demo.local"
    timezone: str = "UTC"
    lldp_timer: int = 30
    lldp_hold_time: int = 120
    errdisable_interval: int = 300
    ssh_idle_timeout: int = 30
    console_idle_timeout: int = 15
    banner: str = "AUTHORIZED ACCESS ONLY - NetBox Demo Fabric"


@dataclass(frozen=True)
class FabricInterface:
    """A point-to-point routed fabric link."""

    name: str
    description: str
    address: str
    mtu: int = 9214


@dataclass(frozen=True)
class Peer:
    """A BGP neighbor."""

    address: str
    remote_as: int
    description: str
    peer_group: str


@dataclass(frozen=True)
class TenantVlan:
    """A tenant L2 segment, stretched by EVPN."""

    vlan_id: int
    name: str
    vni: int
    svi_address: str
    mtu: int = 9000
    ip_helpers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ServerPort:
    """A server-facing access or trunk port."""

    name: str
    description: str
    mode: str  # "access" or "trunk"
    access_vlan: int | None = None
    trunk_vlans: tuple[int, ...] = ()
    mtu: int = 9000
    enabled: bool = True
    port_channel: str | None = None


@dataclass(frozen=True)
class MlagDomain:
    """An MLAG pair's shared configuration."""

    domain_id: int
    peer_address: str
    peer_link: str = "Port-Channel2000"
    peer_link_vlan: int = 4094
    peer_link_members: tuple[str, ...] = ()
    local_address: str = "169.254.1.1/30"


@dataclass
class DeviceIntent:
    """Everything one switch should be configured with."""

    hostname: str
    role: str  # "spine" | "leaf" | "border"
    asn: int
    router_id: str
    mgmt_vrf: str = "MGMT"
    mgmt_address: str = ""
    mgmt_gateway: str = "172.31.30.1"
    vtep_address: str | None = None
    underlay_mtu: int = 9214
    fabric_interfaces: list[FabricInterface] = field(default_factory=list)
    peers: list[Peer] = field(default_factory=list)
    vlans: list[TenantVlan] = field(default_factory=list)
    server_ports: list[ServerPort] = field(default_factory=list)
    mlag: MlagDomain | None = None
    services: ServiceBaseline = field(default_factory=ServiceBaseline)
    base_l2_vni: int = 10000
    anycast_gateway_mac: str = "00:1c:73:00:00:99"

    @property
    def is_leaf(self) -> bool:
        return self.role in ("leaf", "border")

    @property
    def is_spine(self) -> bool:
        return self.role == "spine"

    def total_addresses(self) -> int:
        """Count configured addresses, used by the pre-push sanity check."""
        return (
            (1 if self.mgmt_address else 0)
            + (1 if self.router_id else 0)
            + (1 if self.vtep_address else 0)
            + len(self.fabric_interfaces)
            + sum(1 for v in self.vlans if v.svi_address)
        )
