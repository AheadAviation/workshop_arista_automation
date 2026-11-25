"""Full-stack NetBox Source-of-Record showcase builder (netbox_demo 0.3.0).

Turns the 6-switch EVPN/VXLAN fabric into a complete, clickable enterprise data
center model inside NetBox Enterprise:

* **Rack layouts & elevations**: Location hierarchy (Hall A → Row 01), 2 × 42U
  cabinets (RACK-A01, RACK-A02), switches racked at ToR U positions, 4 workload
  servers (App / DB / Storage), MLAG peer-link cables, and dual-homed server
  access cables.
* **Power infrastructure**: Redundant Utility A & B Power Panels, 4 × 208V/30A
  3-phase Power Feeds, 4 Switched Rack PDUs cabled to the feeds, and dual
  redundant PSUs on every switch and server cabled to A/B PDU outlets with real
  wattage draw so NetBox calculates live rack/feed utilization and renders full
  end-to-end Power Path Traces.
* **WAN circuits & IPAM/ASNs**: Lumen & Zayo Providers, Provider Networks,
  3 × 10G/100G Circuits terminated on `demo-spine-01`/`demo-spine-02` uplinks
  (NetBox 4.7 GenericForeignKey `CircuitTermination`), tenant prefixes linked
  to VLANs, Loopback/VTEP IPs, and private/public BGP ASNs.
* **Topology Views auto-layout**: When `netbox_topology_views` is installed,
  seeds clean hierarchical (x, y) coordinates for Circuits, Spines, Leaves,
  Servers, PDUs, Power Feeds, and Power Panels so the topology canvas opens
  ready to present.
* **Live interactive builders**: `quick_add_circuit`, `quick_add_power_feed`,
  and `quick_add_rack_device` let a presenter add new infrastructure live in
  front of an audience and immediately jump to elevations, traces, and maps.

Every created object is scoped to `Site("NetBox Demo")` and tagged `netbox-demo`
so cleanup never touches unrelated NetBox data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import design

SHOWCASE_COORDINATE_GROUP = "NetBox Demo Fabric"

# Rack layout plan: (rack_name, facility_id, description)
RACKS: tuple[tuple[str, str, str], ...] = (
    ("RACK-A01", "DC1-A01", "Spine-01, Leaf Pair 1 (MLAG-1), App & DB Tier"),
    ("RACK-A02", "DC1-A02", "Spine-02, Leaf Pair 2 (MLAG-2), App & Storage Tier"),
)

# Rack mounting for the 6 fabric switches: device -> (rack_name, position_u)
SWITCH_RACK_POSITIONS: dict[str, tuple[str, int]] = {
    "demo-spine-01": ("RACK-A01", 42),
    "demo-leaf-01": ("RACK-A01", 40),
    "demo-leaf-02": ("RACK-A01", 38),
    "demo-spine-02": ("RACK-A02", 42),
    "demo-leaf-03": ("RACK-A02", 40),
    "demo-leaf-04": ("RACK-A02", 38),
}


@dataclass(frozen=True)
class ServerSpec:
    name: str
    rack: str
    position: int
    description: str
    uplinks: tuple[tuple[str, str, str], ...]  # (local_intf, leaf_name, leaf_port)
    ip_address: str
    vlan_id: int


SERVERS: tuple[ServerSpec, ...] = (
    ServerSpec(
        name="demo-app-01",
        rack="RACK-A01",
        position=20,
        description="Production Web/App Tier Node 01 (Dual-homed MLAG-1)",
        uplinks=(("eth0", "demo-leaf-01", "Ethernet6"), ("eth1", "demo-leaf-02", "Ethernet6")),
        ip_address="10.100.100.11/24",
        vlan_id=100,
    ),
    ServerSpec(
        name="demo-db-01",
        rack="RACK-A01",
        position=16,
        description="Primary Database Cluster Node 01 (Dual-homed MLAG-1)",
        uplinks=(("eth0", "demo-leaf-01", "Ethernet7"), ("eth1", "demo-leaf-02", "Ethernet7")),
        ip_address="10.100.120.11/24",
        vlan_id=120,
    ),
    ServerSpec(
        name="demo-app-02",
        rack="RACK-A02",
        position=20,
        description="Production Web/App Tier Node 02 (Dual-homed MLAG-2)",
        uplinks=(("eth0", "demo-leaf-03", "Ethernet6"), ("eth1", "demo-leaf-04", "Ethernet6")),
        ip_address="10.100.100.12/24",
        vlan_id=100,
    ),
    ServerSpec(
        name="demo-storage-01",
        rack="RACK-A02",
        position=16,
        description="NVMe Block Storage Array 01 (Dual-homed MLAG-2)",
        uplinks=(("eth0", "demo-leaf-03", "Ethernet7"), ("eth1", "demo-leaf-04", "Ethernet7")),
        ip_address="10.100.120.12/24",
        vlan_id=120,
    ),
)

# MLAG peer-link cables: (leaf_a, port_a, leaf_b, port_b, label)
MLAG_CABLES: tuple[tuple[str, str, str, str, str], ...] = (
    ("demo-leaf-01", "Ethernet3", "demo-leaf-02", "Ethernet3", "MLAG-1 Peer-Link A"),
    ("demo-leaf-01", "Ethernet4", "demo-leaf-02", "Ethernet4", "MLAG-1 Peer-Link B"),
    ("demo-leaf-03", "Ethernet3", "demo-leaf-04", "Ethernet3", "MLAG-2 Peer-Link A"),
    ("demo-leaf-03", "Ethernet4", "demo-leaf-04", "Ethernet4", "MLAG-2 Peer-Link B"),
)

# Power feeds: (feed_name, panel_name, rack_name, feed_type, pdu_name, pdu_position)
POWER_LAYOUT: tuple[tuple[str, str, str, str, str, int], ...] = (
    ("FEED-A01-A", "PANEL-A (Utility A)", "RACK-A01", "primary", "demo-pdu-a01-a", 2),
    ("FEED-A01-B", "PANEL-B (Utility B)", "RACK-A01", "redundant", "demo-pdu-a01-b", 1),
    ("FEED-A02-A", "PANEL-A (Utility A)", "RACK-A02", "primary", "demo-pdu-a02-a", 2),
    ("FEED-A02-B", "PANEL-B (Utility B)", "RACK-A02", "redundant", "demo-pdu-a02-b", 1),
)


@dataclass(frozen=True)
class CircuitSpec:
    cid: str
    provider: str
    provider_slug: str
    provider_asn: int
    provider_network: str
    circuit_type: str
    circuit_type_slug: str
    circuit_type_color: str
    commit_rate_kbps: int
    port_speed_kbps: int
    device: str
    interface: str
    description: str
    cable_color: str


CIRCUITS: tuple[CircuitSpec, ...] = (
    CircuitSpec(
        cid="CID-LUMEN-10G-001",
        provider="Lumen Technologies",
        provider_slug="lumen-technologies",
        provider_asn=3356,
        provider_network="Lumen Global IP Backbone",
        circuit_type="IP Transit",
        circuit_type_slug="ip-transit",
        circuit_type_color="00bcd4",
        commit_rate_kbps=10_000_000,
        port_speed_kbps=10_000_000,
        device="demo-spine-01",
        interface="Ethernet5",
        description="Primary 10G DIA / IP Transit (BGP AS3356)",
        cable_color="00bcd4",
    ),
    CircuitSpec(
        cid="CID-ZAYO-10G-002",
        provider="Zayo Group",
        provider_slug="zayo-group",
        provider_asn=6461,
        provider_network="Zayo Global IP Backbone",
        circuit_type="IP Transit",
        circuit_type_slug="ip-transit",
        circuit_type_color="00bcd4",
        commit_rate_kbps=10_000_000,
        port_speed_kbps=10_000_000,
        device="demo-spine-02",
        interface="Ethernet5",
        description="Redundant 10G DIA / IP Transit (BGP AS6461)",
        cable_color="00bcd4",
    ),
    CircuitSpec(
        cid="CID-ZAYO-DCI-100G",
        provider="Zayo Group",
        provider_slug="zayo-group",
        provider_asn=6461,
        provider_network="Zayo Metro EVPN DCI",
        circuit_type="DCI Wavelength",
        circuit_type_slug="dci-wavelength",
        circuit_type_color="9c27b0",
        commit_rate_kbps=100_000_000,
        port_speed_kbps=100_000_000,
        device="demo-spine-01",
        interface="Ethernet6",
        description="100G Dark-Fiber EVPN Data Center Interconnect",
        cable_color="9c27b0",
    ),
)

# Hierarchical canvas coordinates for Topology Views (x horizontal, y vertical).
TOPOLOGY_DEVICE_COORDS: dict[str, tuple[int, int]] = {
    "demo-spine-01": (-220, -140),
    "demo-spine-02": (220, -140),
    "demo-leaf-01": (-420, 40),
    "demo-leaf-02": (-140, 40),
    "demo-leaf-03": (140, 40),
    "demo-leaf-04": (420, 40),
    "demo-app-01": (-360, 220),
    "demo-db-01": (-160, 220),
    "demo-app-02": (160, 220),
    "demo-storage-01": (360, 220),
    "demo-pdu-a01-a": (-460, 390),
    "demo-pdu-a01-b": (-180, 390),
    "demo-pdu-a02-a": (180, 390),
    "demo-pdu-a02-b": (460, 390),
}

TOPOLOGY_CIRCUIT_COORDS: dict[str, tuple[int, int]] = {
    "CID-LUMEN-10G-001": (-340, -300),
    "CID-ZAYO-DCI-100G": (-60, -300),
    "CID-ZAYO-10G-002": (260, -300),
}

TOPOLOGY_FEED_COORDS: dict[str, tuple[int, int]] = {
    "FEED-A01-A": (-460, 520),
    "FEED-A01-B": (-180, 520),
    "FEED-A02-A": (180, 520),
    "FEED-A02-B": (460, 520),
}

TOPOLOGY_PANEL_COORDS: dict[str, tuple[int, int]] = {
    "PANEL-A (Utility A)": (-240, 650),
    "PANEL-B (Utility B)": (240, 650),
}


# --------------------------------------------------------------------------- #
# Pure input validators (unit-tested without Django)
# --------------------------------------------------------------------------- #


def validate_circuit_input(
    *,
    cid: str,
    provider: str,
    commit_mbps: int | str,
    device: str,
    interface: str,
) -> dict[str, Any]:
    """Validate interactive Quick-Add Circuit inputs."""
    cid_clean = str(cid or "").strip().upper()
    if not cid_clean or not re.fullmatch(r"[A-Z0-9._-]{3,64}", cid_clean):
        raise ValueError("Circuit ID must be 3-64 alphanumeric characters (A-Z, 0-9, ., _, -).")
    provider_clean = str(provider or "").strip()
    if not provider_clean or len(provider_clean) > 100:
        raise ValueError("Provider name is required (1-100 characters).")
    try:
        mbps = int(commit_mbps)
    except (TypeError, ValueError):
        raise ValueError("Commit rate (Mbps) must be an integer.") from None
    if not (10 <= mbps <= 400_000):
        raise ValueError("Commit rate must be between 10 and 400000 Mbps.")
    dev_clean = str(device or "").strip()
    if dev_clean not in design.ALL_DEVICES:
        raise ValueError(f"Device must be one of: {', '.join(design.ALL_DEVICES)}.")
    intf_clean = str(interface or "").strip()
    if not intf_clean or not re.fullmatch(r"[A-Za-z][A-Za-z0-9/._-]{1,31}", intf_clean):
        raise ValueError("Interface name must look like Ethernet5 or Ethernet8.")
    return {
        "cid": cid_clean,
        "provider": provider_clean,
        "commit_kbps": mbps * 1000,
        "device": dev_clean,
        "interface": intf_clean,
    }


def validate_power_feed_input(
    *,
    name: str,
    panel: str,
    rack: str,
    feed_type: str,
    voltage: int | str,
    amperage: int | str,
) -> dict[str, Any]:
    """Validate interactive Quick-Add Power Feed inputs."""
    name_clean = str(name or "").strip().upper()
    if not name_clean or not re.fullmatch(r"[A-Z0-9._-]{2,64}", name_clean):
        raise ValueError("Feed name must be 2-64 characters (e.g. FEED-A01-C).")
    panel_clean = str(panel or "").strip()
    if not panel_clean:
        raise ValueError("Power panel is required.")
    rack_clean = str(rack or "").strip()
    if not rack_clean:
        raise ValueError("Rack is required.")
    ft = str(feed_type or "primary").strip().lower()
    if ft not in ("primary", "redundant"):
        raise ValueError("Feed type must be 'primary' or 'redundant'.")
    try:
        v = int(voltage)
        a = int(amperage)
    except (TypeError, ValueError):
        raise ValueError("Voltage and amperage must be integers.") from None
    if not (100 <= v <= 600):
        raise ValueError("Voltage must be between 100 and 600 V.")
    if not (10 <= a <= 200):
        raise ValueError("Amperage must be between 10 and 200 A.")
    return {
        "name": name_clean,
        "panel": panel_clean,
        "rack": rack_clean,
        "feed_type": ft,
        "voltage": v,
        "amperage": a,
    }


def validate_rack_device_input(
    *,
    name: str,
    role: str,
    rack: str,
    position: int | str,
    u_height: int | str = 2,
    uplink_leaf: str = "",
    uplink_port: str = "",
) -> dict[str, Any]:
    """Validate interactive Quick-Add Rack Device inputs."""
    name_clean = str(name or "").strip().lower()
    if not name_clean or not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,48}", name_clean):
        raise ValueError("Device name must be lowercase alphanumeric/hyphens (e.g. demo-fw-01).")
    if not name_clean.startswith(design.DEVICE_PREFIX):
        name_clean = f"{design.DEVICE_PREFIX}{name_clean}"
    role_clean = str(role or "Server").strip()
    if role_clean not in ("Server", "Firewall", "Storage", "Router"):
        raise ValueError("Role must be Server, Firewall, Storage, or Router.")
    rack_clean = str(rack or "").strip()
    if not rack_clean:
        raise ValueError("Rack is required.")
    try:
        pos = int(position)
        u = int(u_height)
    except (TypeError, ValueError):
        raise ValueError("Rack position (U) and height (U) must be integers.") from None
    if not (1 <= u <= 8):
        raise ValueError("U-height must be between 1 and 8.")
    if not (3 <= pos <= 42 - u + 1):
        raise ValueError(f"Position must be between U3 and U{42 - u + 1} (U1-U2 reserved for PDUs).")
    leaf_clean = str(uplink_leaf or "").strip()
    port_clean = str(uplink_port or "").strip()
    if leaf_clean and leaf_clean not in design.LEAVES:
        raise ValueError(f"Uplink leaf must be one of: {', '.join(design.LEAVES)}.")
    if leaf_clean and not port_clean:
        raise ValueError("Uplink port is required when an uplink leaf is selected.")
    return {
        "name": name_clean,
        "role": role_clean,
        "rack": rack_clean,
        "position": pos,
        "u_height": u,
        "uplink_leaf": leaf_clean,
        "uplink_port": port_clean,
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "item"


def _connect_cable(a_obj, b_obj, *, label: str = "", color: str = "", cable_type: str = "", tag=None) -> bool:
    """Create a Cable between two NetBox termination objects if neither is cabled."""
    from dcim.models import Cable

    if getattr(a_obj, "cable_id", None) or getattr(b_obj, "cable_id", None):
        return False
    kwargs: dict[str, Any] = {
        "a_terminations": [a_obj],
        "b_terminations": [b_obj],
        "status": "connected",
        "label": label,
        "color": color,
    }
    if cable_type:
        kwargs["type"] = cable_type
    cable = Cable(**kwargs)
    cable.save()
    if tag is not None:
        cable.tags.add(tag)
    return True


def _ensure_role(name: str, slug: str, color: str):
    from dcim.models import DeviceRole

    existing = DeviceRole.objects.filter(name__iexact=name).first() or DeviceRole.objects.filter(slug=slug).first()
    if existing is not None:
        return existing
    role, _ = DeviceRole.objects.get_or_create(slug=slug, defaults={"name": name, "color": color})
    return role


def _ensure_location_hierarchy(site, tag):
    from dcim.models import Location

    hall, _ = Location.objects.get_or_create(
        site=site,
        slug="demo-hall-a",
        defaults={"name": "Hall A", "status": "active", "description": "Primary Data Hall (NetBox Demo)"},
    )
    hall.tags.add(tag)

    row, _ = Location.objects.get_or_create(
        site=site,
        slug="demo-row-01",
        defaults={
            "name": "Cage 1 · Row 01",
            "parent": hall,
            "status": "active",
            "description": "EVPN/VXLAN Pod Row 01",
        },
    )
    if row.parent_id != hall.id:
        row.parent = hall
        row.save()
    row.tags.add(tag)
    return hall, row


# --------------------------------------------------------------------------- #
# 1. Rack layouts, workload servers, MLAG + server cables
# --------------------------------------------------------------------------- #


def seed_racks_and_servers() -> dict[str, Any]:
    """Create Location hierarchy, 2×42U Racks, rack-mount switches, 4 servers, and cables."""
    from dcim.models import Device, DeviceType, Interface, Manufacturer, Rack, RackRole
    from ipam.models import IPAddress, VLAN
    from tenancy.models import Tenant

    from . import seeder

    seeder.seed_all()
    site = seeder.ensure_site()
    tag = seeder._tag()
    tenant_core = Tenant.objects.get(slug="core")

    hall, row = _ensure_location_hierarchy(site, tag)

    rack_role, _ = RackRole.objects.get_or_create(
        slug="demo-compute-fabric",
        defaults={"name": "Compute & Fabric", "color": "3f51b5"},
    )

    racks_by_name: dict[str, Any] = {}
    for name, facility_id, desc in RACKS:
        rack, _ = Rack.objects.update_or_create(
            site=site,
            name=name,
            defaults={
                "location": row,
                "facility_id": facility_id,
                "role": rack_role,
                "tenant": tenant_core,
                "status": "active",
                "form_factor": "4-post-cabinet",
                "width": 19,
                "u_height": 42,
                "starting_unit": 1,
                "description": desc,
            },
        )
        rack.tags.add(tag)
        racks_by_name[name] = rack

    # Ensure cEOS DeviceType has u_height=1 so it mounts cleanly in Rack Elevation SVG.
    arista = seeder._ensure_manufacturer()
    ceos_dt = seeder._ensure_device_type(arista)
    if ceos_dt.u_height != 1:
        ceos_dt.u_height = 1
        ceos_dt.save(update_fields=["u_height"])

    # Rack-mount the 6 fabric switches.
    racked_switches = 0
    for dev_name, (rack_name, pos_u) in SWITCH_RACK_POSITIONS.items():
        dev = Device.objects.filter(name=dev_name).first()
        if not dev:
            continue
        rack = racks_by_name[rack_name]
        if dev.rack_id != rack.id or dev.position != pos_u or dev.face != "front" or dev.location_id != row.id:
            dev.location = row
            dev.rack = rack
            dev.position = pos_u
            dev.face = "front"
            dev.save()
        dev.tags.add(tag)
        racked_switches += 1

    # Server DeviceType & Role.
    dell, _ = Manufacturer.objects.get_or_create(slug="dell", defaults={"name": "Dell"})
    server_dt, _ = DeviceType.objects.get_or_create(
        slug="dell-poweredge-r660",
        defaults={
            "manufacturer": dell,
            "model": "PowerEdge R660",
            "u_height": 2,
            "is_full_depth": True,
        },
    )
    server_role = _ensure_role("Server", "server", "2196f3")

    servers_created: list[str] = []
    cables_created = 0

    for spec in SERVERS:
        rack = racks_by_name[spec.rack]
        srv, _ = Device.objects.update_or_create(
            name=spec.name,
            defaults={
                "site": site,
                "location": row,
                "rack": rack,
                "position": spec.position,
                "face": "front",
                "device_type": server_dt,
                "role": server_role,
                "tenant": tenant_core,
                "status": "active",
                "description": spec.description,
            },
        )
        srv.tags.add(tag)
        servers_created.append(srv.name)

        vlan = VLAN.objects.filter(vid=spec.vlan_id, group__slug=design.SITE_SLUG).first()
        primary_intf = None
        for local_port, leaf_name, leaf_port in spec.uplinks:
            srv_intf, _ = Interface.objects.get_or_create(
                device=srv,
                name=local_port,
                defaults={"type": seeder.FABRIC_IFACE_TYPE, "enabled": True, "description": f"Uplink to {leaf_name}"},
            )
            srv_intf.tags.add(tag)
            if primary_intf is None:
                primary_intf = srv_intf

            leaf_dev = Device.objects.filter(name=leaf_name).first()
            if leaf_dev:
                leaf_intf, _ = Interface.objects.get_or_create(
                    device=leaf_dev,
                    name=leaf_port,
                    defaults={
                        "type": seeder.FABRIC_IFACE_TYPE,
                        "enabled": True,
                        "description": f"Server {spec.name}",
                    },
                )
                if vlan is not None and leaf_intf.untagged_vlan_id != vlan.id:
                    leaf_intf.mode = "access"
                    leaf_intf.untagged_vlan = vlan
                    leaf_intf.save()
                leaf_intf.tags.add(tag)
                if _connect_cable(
                    srv_intf,
                    leaf_intf,
                    label=f"10G {spec.name}",
                    color="4caf50",
                    cable_type="dac-passive",
                    tag=tag,
                ):
                    cables_created += 1

        if primary_intf is not None:
            ip, _ = IPAddress.objects.get_or_create(
                address=spec.ip_address,
                defaults={"description": f"{spec.name} workload IP", "status": "active", "tenant": tenant_core},
            )
            ip.assigned_object = primary_intf
            ip.save()
            ip.tags.add(tag)
            if srv.primary_ip4_id != ip.id:
                srv.primary_ip4 = ip
                srv.save(update_fields=["primary_ip4"])

    # Cable MLAG peer-links so Topology Views renders the full MLAG diamond.
    for leaf_a, port_a, leaf_b, port_b, label in MLAG_CABLES:
        dev_a = Device.objects.filter(name=leaf_a).first()
        dev_b = Device.objects.filter(name=leaf_b).first()
        if not dev_a or not dev_b:
            continue
        ia, _ = Interface.objects.get_or_create(
            device=dev_a, name=port_a, defaults={"type": seeder.FABRIC_IFACE_TYPE, "enabled": True}
        )
        ib, _ = Interface.objects.get_or_create(
            device=dev_b, name=port_b, defaults={"type": seeder.FABRIC_IFACE_TYPE, "enabled": True}
        )
        ia.tags.add(tag)
        ib.tags.add(tag)
        if _connect_cable(ia, ib, label=label, color="ff9800", cable_type="dac-passive", tag=tag):
            cables_created += 1

    return {
        "locations": [hall.name, row.name],
        "racks": list(racks_by_name.keys()),
        "racked_switches": racked_switches,
        "servers": servers_created,
        "cables_created": cables_created,
    }


# --------------------------------------------------------------------------- #
# 2. Power panels, feeds, redundant A/B PDUs, and PSU cabling
# --------------------------------------------------------------------------- #


def seed_power() -> dict[str, Any]:
    """Seed Utility A/B Power Panels, 4 Power Feeds, 4 Switched PDUs, and dual-PSU cables."""
    from dcim.models import (
        Device,
        DeviceType,
        Manufacturer,
        PowerFeed,
        PowerOutlet,
        PowerPanel,
        PowerPort,
        Rack,
    )
    from tenancy.models import Tenant

    from . import seeder

    rack_summary = seed_racks_and_servers()
    site = seeder.ensure_site()
    tag = seeder._tag()
    tenant_core = Tenant.objects.get(slug="core")
    hall, row = _ensure_location_hierarchy(site, tag)

    panels: dict[str, Any] = {}
    for pname in ("PANEL-A (Utility A)", "PANEL-B (Utility B)"):
        panel, _ = PowerPanel.objects.update_or_create(
            site=site,
            name=pname,
            defaults={"location": hall, "description": f"Redundant {pname} 208V 3-Phase Distribution"},
        )
        panel.tags.add(tag)
        panels[pname] = panel

    apc, _ = Manufacturer.objects.get_or_create(slug="apc", defaults={"name": "APC"})
    pdu_dt, _ = DeviceType.objects.get_or_create(
        slug="apc-ap8953-pdu",
        defaults={
            "manufacturer": apc,
            "model": "AP8953 Switched Rack PDU",
            "u_height": 1,
            "is_full_depth": False,
        },
    )
    pdu_role = _ensure_role("PDU", "pdu", "f44336")

    feeds_created: list[str] = []
    pdus_by_rack_side: dict[tuple[str, str], Any] = {}
    power_cables = 0

    for feed_name, panel_name, rack_name, feed_type, pdu_name, pdu_pos in POWER_LAYOUT:
        rack = Rack.objects.get(site=site, name=rack_name)
        panel = panels[panel_name]
        feed, _ = PowerFeed.objects.update_or_create(
            power_panel=panel,
            name=feed_name,
            defaults={
                "rack": rack,
                "status": "active",
                "type": feed_type,
                "supply": "ac",
                "phase": "three-phase",
                "voltage": 208,
                "amperage": 30,
                "max_utilization": 80,
                "tenant": tenant_core,
                "description": f"{feed_type.capitalize()} 208V/30A 3-Phase feed for {rack_name}",
            },
        )
        feed.tags.add(tag)
        feeds_created.append(feed.name)

        side = "A" if feed_type == "primary" else "B"
        pdu, _ = Device.objects.update_or_create(
            name=pdu_name,
            defaults={
                "site": site,
                "location": row,
                "rack": rack,
                "position": pdu_pos,
                "face": "rear",
                "device_type": pdu_dt,
                "role": pdu_role,
                "tenant": tenant_core,
                "status": "active",
                "description": f"{rack_name} Side-{side} Switched Rack PDU ({feed_name})",
            },
        )
        pdu.tags.add(tag)
        pdus_by_rack_side[(rack_name, side)] = pdu

        pdu_in, _ = PowerPort.objects.get_or_create(
            device=pdu,
            name="Input",
            defaults={"type": "nema-l21-30p", "description": f"Input from {feed_name}"},
        )
        pdu_in.tags.add(tag)
        if _connect_cable(
            feed,
            pdu_in,
            label=feed_name,
            color="f44336" if side == "A" else "3f51b5",
            tag=tag,
        ):
            power_cables += 1

        for idx in range(1, 9):
            outlet, _ = PowerOutlet.objects.get_or_create(
                device=pdu,
                name=f"Outlet-{idx}",
                defaults={
                    "type": "iec-60320-c13",
                    "power_port": pdu_in,
                    "feed_leg": "A" if idx % 3 == 1 else ("B" if idx % 3 == 2 else "C"),
                },
            )
            outlet.tags.add(tag)

    # Wire dual redundant PSUs (PSU1 → Side A PDU, PSU2 → Side B PDU) on every switch and server.
    powered_devices = 0
    for rack_name, _, _ in RACKS:
        rack = Rack.objects.get(site=site, name=rack_name)
        pdu_a = pdus_by_rack_side[(rack_name, "A")]
        pdu_b = pdus_by_rack_side[(rack_name, "B")]
        rack_devs = list(
            Device.objects.filter(rack=rack)
            .exclude(role=pdu_role)
            .order_by("-position", "name")
        )
        for idx, dev in enumerate(rack_devs, start=1):
            is_switch = dev.name in design.ALL_DEVICES
            max_w = 350 if is_switch else 800
            alloc_w = 180 if is_switch else 450
            for psu_name, pdu, color in (("PSU1", pdu_a, "f44336"), ("PSU2", pdu_b, "3f51b5")):
                pport, _ = PowerPort.objects.update_or_create(
                    device=dev,
                    name=psu_name,
                    defaults={
                        "type": "iec-60320-c14",
                        "maximum_draw": max_w,
                        "allocated_draw": alloc_w,
                        "description": f"Redundant {psu_name} ({pdu.name})",
                    },
                )
                pport.tags.add(tag)
                outlet = PowerOutlet.objects.filter(device=pdu, name=f"Outlet-{idx}").first()
                if outlet is not None and _connect_cable(
                    pport, outlet, label=f"{dev.name}-{psu_name}", color=color, tag=tag
                ):
                    power_cables += 1
            powered_devices += 1

    return {
        "racks": rack_summary["racks"],
        "panels": list(panels.keys()),
        "feeds": feeds_created,
        "pdus": [p.name for p in pdus_by_rack_side.values()],
        "powered_devices": powered_devices,
        "power_cables_created": power_cables,
    }


# --------------------------------------------------------------------------- #
# 3. WAN Circuits, Providers, Terminations & IPAM / BGP ASNs
# --------------------------------------------------------------------------- #


def seed_circuits_and_ipam() -> dict[str, Any]:
    """Seed Providers, WAN/DCI Circuits cabled to Spines, Tenant Prefixes, Loopbacks, and ASNs."""
    from circuits.models import Circuit, CircuitTermination, CircuitType, Provider, ProviderNetwork
    from dcim.models import Device, Interface
    from ipam.models import ASN, IPAddress, Prefix, RIR, VLAN, VRF
    from tenancy.models import Tenant

    from . import seeder

    seeder.seed_all()
    site = seeder.ensure_site()
    tag = seeder._tag()
    tenant_core = Tenant.objects.get(slug="core")
    vrf_core = VRF.objects.get(name="CORE")

    # RIRs & ASNs (fabric private ASNs 65000..65004 + provider public ASNs).
    rir_private, _ = RIR.objects.get_or_create(
        slug="rfc-6996-private",
        defaults={"name": "RFC 6996 Private", "is_private": True, "description": "Private 16-bit BGP ASNs"},
    )
    rir_arin, _ = RIR.objects.get_or_create(
        slug="arin",
        defaults={"name": "ARIN", "is_private": False},
    )

    asns_created: list[int] = []
    for asn_num, desc in (
        (design.SPINE_ASN, "NetBox Demo Fabric Spines (Route Servers)"),
        *[(design.asn_for(leaf), f"NetBox Demo {leaf}") for leaf in design.LEAVES],
    ):
        asn_obj, _ = ASN.objects.get_or_create(
            asn=asn_num,
            defaults={"rir": rir_private, "tenant": tenant_core, "description": desc},
        )
        asn_obj.sites.add(site)
        asn_obj.tags.add(tag)
        asns_created.append(asn_obj.asn)

    # Tenant SVI prefixes (linked to VLANs) + Loopback & VTEP summary prefixes.
    prefixes_created: list[str] = []
    for seed in design.SEED_VLANS:
        vlan = VLAN.objects.filter(vid=seed.vlan_id, group__slug=design.SITE_SLUG).first()
        network = seeder._prefix_of(seed.svi)
        pfx, _ = Prefix.objects.update_or_create(
            prefix=network,
            vrf=vrf_core,
            defaults={
                "status": "active",
                "tenant": tenant_core,
                "vlan": vlan,
                "description": f"VLAN {seed.vlan_id} ({seed.name}) — {seed.description}",
            },
        )
        pfx.tags.add(tag)
        prefixes_created.append(str(pfx.prefix))

    for cidr, desc in (
        ("10.255.0.0/24", "NetBox Demo Fabric Loopback0 (Router IDs)"),
        ("10.255.1.0/24", "NetBox Demo Fabric Loopback1 (VXLAN VTEPs)"),
        (design.UNDERLAY_SUBNET, "NetBox Demo Fabric Underlay /31 Supernet"),
    ):
        pfx, _ = Prefix.objects.update_or_create(
            prefix=cidr,
            vrf=vrf_core,
            defaults={"status": "active", "tenant": tenant_core, "description": desc},
        )
        pfx.tags.add(tag)
        prefixes_created.append(str(pfx.prefix))

    # Document Loopback0 & Loopback1 IPs on all 6 switches so IPAM utilization lights up.
    for dev_name in design.ALL_DEVICES:
        dev = Device.objects.filter(name=dev_name).first()
        if not dev:
            continue
        for lo_name, ip_str, desc in (
            ("Loopback0", f"{design.ROUTER_IDS[dev_name]}/32", f"{dev_name} Router ID"),
            *(
                [("Loopback1", f"{design.VTEP_IPS[dev_name]}/32", f"{dev_name} VXLAN VTEP")]
                if dev_name in design.VTEP_IPS
                else []
            ),
        ):
            lo_intf, _ = Interface.objects.get_or_create(
                device=dev,
                name=lo_name,
                defaults={"type": "virtual", "enabled": True, "description": desc},
            )
            lo_intf.tags.add(tag)
            ip, _ = IPAddress.objects.get_or_create(
                address=ip_str,
                vrf=vrf_core,
                defaults={"status": "active", "tenant": tenant_core, "description": desc},
            )
            ip.assigned_object = lo_intf
            ip.save()
            ip.tags.add(tag)

    # Providers, CircuitTypes, Circuits, Terminations (A @ Site, Z @ ProviderNetwork), and Spine Cables.
    circuits_created: list[str] = []
    circuit_cables = 0
    for spec in CIRCUITS:
        provider, _ = Provider.objects.get_or_create(
            slug=spec.provider_slug,
            defaults={"name": spec.provider, "description": f"WAN Carrier ({spec.provider})"},
        )
        provider.tags.add(tag)

        prov_asn, _ = ASN.objects.get_or_create(
            asn=spec.provider_asn,
            defaults={"rir": rir_arin, "description": f"{spec.provider} Backbone ASN"},
        )
        provider.asns.add(prov_asn)

        pnet, _ = ProviderNetwork.objects.get_or_create(
            provider=provider,
            name=spec.provider_network,
            defaults={"description": f"{spec.provider} external network"},
        )
        pnet.tags.add(tag)

        ctype, _ = CircuitType.objects.get_or_create(
            slug=spec.circuit_type_slug,
            defaults={"name": spec.circuit_type, "color": spec.circuit_type_color},
        )

        circuit, _ = Circuit.objects.update_or_create(
            cid=spec.cid,
            defaults={
                "provider": provider,
                "type": ctype,
                "status": "active",
                "tenant": tenant_core,
                "commit_rate": spec.commit_rate_kbps,
                "description": spec.description,
            },
        )
        circuit.tags.add(tag)
        circuits_created.append(circuit.cid)

        term_a = CircuitTermination.objects.filter(circuit=circuit, term_side="A").first()
        if term_a is None:
            term_a = CircuitTermination(
                circuit=circuit,
                term_side="A",
                termination=site,
                port_speed=spec.port_speed_kbps,
                upstream_speed=spec.port_speed_kbps,
                description=f"Hand-off at {site.name} ({spec.device}:{spec.interface})",
            )
            term_a.save()
        term_a.tags.add(tag)

        term_z = CircuitTermination.objects.filter(circuit=circuit, term_side="Z").first()
        if term_z is None:
            term_z = CircuitTermination(
                circuit=circuit,
                term_side="Z",
                termination=pnet,
                port_speed=spec.port_speed_kbps,
                description=f"Provider PoP ({spec.provider_network})",
            )
            term_z.save()
        term_z.tags.add(tag)

        spine_dev = Device.objects.filter(name=spec.device).first()
        if spine_dev:
            uplink_intf, _ = Interface.objects.get_or_create(
                device=spine_dev,
                name=spec.interface,
                defaults={
                    "type": seeder.FABRIC_IFACE_TYPE,
                    "enabled": True,
                    "description": f"WAN {spec.cid} ({spec.provider})",
                },
            )
            uplink_intf.tags.add(tag)
            if _connect_cable(
                term_a,
                uplink_intf,
                label=spec.cid,
                color=spec.cable_color,
                cable_type="smf-os2",
                tag=tag,
            ):
                circuit_cables += 1

    return {
        "asns": asns_created,
        "prefixes": prefixes_created,
        "circuits": circuits_created,
        "circuit_cables_created": circuit_cables,
    }


# --------------------------------------------------------------------------- #
# 4. Topology Views auto-layout (graceful no-op if plugin not installed)
# --------------------------------------------------------------------------- #


def topology_plugin_available() -> bool:
    try:
        import netbox_topology_views  # noqa: F401

        return True
    except ImportError:
        return False


def seed_topology_coordinates() -> dict[str, Any]:
    """Seed clean hierarchical (x, y) coordinates in netbox_topology_views if installed."""
    if not topology_plugin_available():
        return {"available": False, "placed": 0}

    from circuits.models import Circuit
    from dcim.models import Device, PowerFeed, PowerPanel
    from netbox_topology_views.models import (
        CircuitCoordinate,
        Coordinate,
        CoordinateGroup,
        PowerFeedCoordinate,
        PowerPanelCoordinate,
    )

    from . import seeder

    site = seeder.get_site()
    if site is None:
        return {"available": True, "placed": 0}

    group, _ = CoordinateGroup.objects.get_or_create(
        name=SHOWCASE_COORDINATE_GROUP,
        defaults={"description": "Hierarchical layout for NetBox Demo EVPN/VXLAN + Power + Circuits"},
    )
    default_group = CoordinateGroup.get_default()

    placed = 0
    for grp in (group, default_group):
        for dev_name, (x, y) in TOPOLOGY_DEVICE_COORDS.items():
            dev = Device.objects.filter(name=dev_name).first()
            if dev:
                Coordinate.objects.update_or_create(device=dev, group=grp, defaults={"x": x, "y": y})
                if grp == group:
                    placed += 1
        for cid, (x, y) in TOPOLOGY_CIRCUIT_COORDS.items():
            circ = Circuit.objects.filter(cid=cid).first()
            if circ:
                CircuitCoordinate.objects.update_or_create(device=circ, group=grp, defaults={"x": x, "y": y})
                if grp == group:
                    placed += 1
        for fname, (x, y) in TOPOLOGY_FEED_COORDS.items():
            feed = PowerFeed.objects.filter(name=fname, power_panel__site=site).first()
            if feed:
                PowerFeedCoordinate.objects.update_or_create(device=feed, group=grp, defaults={"x": x, "y": y})
                if grp == group:
                    placed += 1
        for pname, (x, y) in TOPOLOGY_PANEL_COORDS.items():
            panel = PowerPanel.objects.filter(name=pname, site=site).first()
            if panel:
                PowerPanelCoordinate.objects.update_or_create(device=panel, group=grp, defaults={"x": x, "y": y})
                if grp == group:
                    placed += 1

    return {"available": True, "group": group.name, "group_id": group.pk, "placed": placed}


def seed_full_showcase() -> dict[str, Any]:
    """Build the entire NetBox Source-of-Record showcase in one idempotent call."""
    racks = seed_racks_and_servers()
    power = seed_power()
    circuits = seed_circuits_and_ipam()
    topo = seed_topology_coordinates()
    return {
        "racks": racks,
        "power": power,
        "circuits": circuits,
        "topology": topo,
    }


# --------------------------------------------------------------------------- #
# 5. Live interactive Quick-Add actions for presentations
# --------------------------------------------------------------------------- #


def quick_add_circuit(
    *,
    cid: str,
    provider: str,
    circuit_type: str = "IP Transit",
    commit_mbps: int | str = 10_000,
    device: str = "demo-spine-02",
    interface: str = "Ethernet6",
    description: str = "",
) -> dict[str, Any]:
    """Add a new WAN circuit live and cable it straight to a demo switch port."""
    from circuits.models import Circuit, CircuitTermination, CircuitType, Provider, ProviderNetwork
    from dcim.models import Device, Interface
    from tenancy.models import Tenant

    from . import seeder

    clean = validate_circuit_input(
        cid=cid,
        provider=provider,
        commit_mbps=commit_mbps,
        device=device,
        interface=interface,
    )
    site = seeder.ensure_site()
    tag = seeder._tag()
    tenant_core, _ = Tenant.objects.get_or_create(slug="core", defaults={"name": "CORE"})

    prov, _ = Provider.objects.get_or_create(
        slug=_slugify(clean["provider"]),
        defaults={"name": clean["provider"]},
    )
    prov.tags.add(tag)

    ct_name = str(circuit_type or "IP Transit").strip() or "IP Transit"
    ctype, _ = CircuitType.objects.get_or_create(
        slug=_slugify(ct_name),
        defaults={"name": ct_name, "color": "00bcd4"},
    )
    pnet, _ = ProviderNetwork.objects.get_or_create(
        provider=prov,
        name=f"{prov.name} Edge",
    )
    pnet.tags.add(tag)

    circuit, created = Circuit.objects.update_or_create(
        cid=clean["cid"],
        defaults={
            "provider": prov,
            "type": ctype,
            "status": "active",
            "tenant": tenant_core,
            "commit_rate": clean["commit_kbps"],
            "description": description.strip() or f"{ct_name} ({clean['provider']}) to {clean['device']}",
        },
    )
    circuit.tags.add(tag)

    term_a = CircuitTermination.objects.filter(circuit=circuit, term_side="A").first()
    if term_a is None:
        term_a = CircuitTermination(
            circuit=circuit,
            term_side="A",
            termination=site,
            port_speed=clean["commit_kbps"],
            upstream_speed=clean["commit_kbps"],
        )
        term_a.save()
    term_a.tags.add(tag)

    term_z = CircuitTermination.objects.filter(circuit=circuit, term_side="Z").first()
    if term_z is None:
        term_z = CircuitTermination(
            circuit=circuit,
            term_side="Z",
            termination=pnet,
            port_speed=clean["commit_kbps"],
        )
        term_z.save()
    term_z.tags.add(tag)

    cabled = False
    dev = Device.objects.filter(name=clean["device"]).first()
    if dev is not None:
        intf, _ = Interface.objects.get_or_create(
            device=dev,
            name=clean["interface"],
            defaults={
                "type": seeder.FABRIC_IFACE_TYPE,
                "enabled": True,
                "description": f"WAN {clean['cid']} ({prov.name})",
            },
        )
        intf.tags.add(tag)
        cabled = _connect_cable(term_a, intf, label=clean["cid"], color="00bcd4", cable_type="smf-os2", tag=tag)

    return {
        "cid": circuit.cid,
        "id": circuit.pk,
        "url": circuit.get_absolute_url(),
        "created": created,
        "cabled": cabled,
    }


def quick_add_power_feed(
    *,
    name: str,
    panel: str,
    rack: str,
    feed_type: str = "primary",
    voltage: int | str = 208,
    amperage: int | str = 30,
) -> dict[str, Any]:
    """Add a new Power Feed onto a Power Panel & Rack live."""
    from dcim.models import PowerFeed, PowerPanel, Rack
    from tenancy.models import Tenant

    from . import seeder

    clean = validate_power_feed_input(
        name=name,
        panel=panel,
        rack=rack,
        feed_type=feed_type,
        voltage=voltage,
        amperage=amperage,
    )
    site = seeder.ensure_site()
    tag = seeder._tag()
    tenant_core, _ = Tenant.objects.get_or_create(slug="core", defaults={"name": "CORE"})
    hall, _ = _ensure_location_hierarchy(site, tag)

    panel_obj, _ = PowerPanel.objects.get_or_create(
        site=site,
        name=clean["panel"],
        defaults={"location": hall},
    )
    panel_obj.tags.add(tag)

    rack_obj = Rack.objects.filter(site=site, name=clean["rack"]).first()
    if rack_obj is None:
        raise ValueError(f"Rack {clean['rack']!r} not found in {site.name}. Seed racks first.")

    feed, created = PowerFeed.objects.update_or_create(
        power_panel=panel_obj,
        name=clean["name"],
        defaults={
            "rack": rack_obj,
            "status": "active",
            "type": clean["feed_type"],
            "supply": "ac",
            "phase": "three-phase",
            "voltage": clean["voltage"],
            "amperage": clean["amperage"],
            "max_utilization": 80,
            "tenant": tenant_core,
            "description": f"{clean['feed_type'].capitalize()} {clean['voltage']}V/{clean['amperage']}A feed for {rack_obj.name}",
        },
    )
    feed.tags.add(tag)
    return {
        "name": feed.name,
        "id": feed.pk,
        "url": feed.get_absolute_url(),
        "created": created,
    }


def quick_add_rack_device(
    *,
    name: str,
    role: str = "Server",
    rack: str = "RACK-A01",
    position: int | str = 12,
    u_height: int | str = 2,
    uplink_leaf: str = "demo-leaf-01",
    uplink_port: str = "Ethernet8",
) -> dict[str, Any]:
    """Rack a new server/firewall/storage node with auto-wired dual PSUs and leaf uplink."""
    from dcim.models import Device, DeviceType, Interface, Manufacturer, PowerOutlet, PowerPort, Rack
    from tenancy.models import Tenant

    from . import seeder

    clean = validate_rack_device_input(
        name=name,
        role=role,
        rack=rack,
        position=position,
        u_height=u_height,
        uplink_leaf=uplink_leaf,
        uplink_port=uplink_port,
    )
    site = seeder.ensure_site()
    tag = seeder._tag()
    tenant_core, _ = Tenant.objects.get_or_create(slug="core", defaults={"name": "CORE"})
    _, row = _ensure_location_hierarchy(site, tag)

    rack_obj = Rack.objects.filter(site=site, name=clean["rack"]).first()
    if rack_obj is None:
        raise ValueError(f"Rack {clean['rack']!r} not found in {site.name}. Seed racks first.")

    role_colors = {
        "Server": ("dell", "Dell", "PowerEdge R660", "2196f3"),
        "Firewall": ("palo-alto", "Palo Alto Networks", "PA-3420", "e91e63"),
        "Storage": ("netapp", "NetApp", "AFF A250", "009688"),
        "Router": ("arista", "Arista", "7280R3", "ff9800"),
    }
    m_slug, m_name, model_name, color = role_colors[clean["role"]]
    mfr, _ = Manufacturer.objects.get_or_create(slug=m_slug, defaults={"name": m_name})
    dt_slug = f"{m_slug}-{_slugify(model_name)}-{clean['u_height']}u"
    dt, _ = DeviceType.objects.get_or_create(
        slug=dt_slug,
        defaults={
            "manufacturer": mfr,
            "model": f"{model_name} ({clean['u_height']}U)",
            "u_height": clean["u_height"],
            "is_full_depth": True,
        },
    )
    role_obj = _ensure_role(clean["role"], _slugify(clean["role"]), color)

    dev, created = Device.objects.update_or_create(
        name=clean["name"],
        defaults={
            "site": site,
            "location": row,
            "rack": rack_obj,
            "position": clean["position"],
            "face": "front",
            "device_type": dt,
            "role": role_obj,
            "tenant": tenant_core,
            "status": "active",
            "description": f"Live-added {clean['role']} in {rack_obj.name} U{clean['position']}",
        },
    )
    dev.tags.add(tag)

    cabled_uplink = False
    if clean["uplink_leaf"] and clean["uplink_port"]:
        leaf = Device.objects.filter(name=clean["uplink_leaf"]).first()
        if leaf is not None:
            nic, _ = Interface.objects.get_or_create(
                device=dev,
                name="eth0",
                defaults={"type": seeder.FABRIC_IFACE_TYPE, "enabled": True},
            )
            nic.tags.add(tag)
            leaf_intf, _ = Interface.objects.get_or_create(
                device=leaf,
                name=clean["uplink_port"],
                defaults={"type": seeder.FABRIC_IFACE_TYPE, "enabled": True, "description": f"Uplink {dev.name}"},
            )
            leaf_intf.tags.add(tag)
            cabled_uplink = _connect_cable(
                nic, leaf_intf, label=f"10G {dev.name}", color="4caf50", cable_type="dac-passive", tag=tag
            )

    # Auto-wire PSU1 & PSU2 to any free outlets on this rack's A/B PDUs.
    power_wired = 0
    pdus = list(Device.objects.filter(rack=rack_obj, role__slug="pdu").order_by("name"))
    for idx, pdu in enumerate(pdus[:2], start=1):
        psu, _ = PowerPort.objects.update_or_create(
            device=dev,
            name=f"PSU{idx}",
            defaults={"type": "iec-60320-c14", "maximum_draw": 600, "allocated_draw": 320},
        )
        psu.tags.add(tag)
        free_outlet = PowerOutlet.objects.filter(device=pdu, cable__isnull=True).order_by("name").first()
        if free_outlet is not None and _connect_cable(
            psu, free_outlet, label=f"{dev.name}-PSU{idx}", color="f44336" if idx == 1 else "3f51b5", tag=tag
        ):
            power_wired += 1

    return {
        "name": dev.name,
        "id": dev.pk,
        "url": dev.get_absolute_url(),
        "rack_url": rack_obj.get_absolute_url(),
        "created": created,
        "cabled_uplink": cabled_uplink,
        "power_wired": power_wired,
    }


# --------------------------------------------------------------------------- #
# 6. Summary & surgical cleanup
# --------------------------------------------------------------------------- #


def showcase_summary() -> dict[str, Any]:
    """Read live counts and deep links for the Source-of-Record showcase page."""
    from circuits.models import Circuit, Provider
    from dcim.models import Cable, Device, Location, PowerFeed, PowerPanel, Rack
    from ipam.models import ASN, Prefix, VLAN

    from . import seeder

    site = seeder.get_site()
    if site is None:
        return {
            "site": None,
            "topology_available": topology_plugin_available(),
            "topology_url": "/plugins/netbox_topology_views/topology/",
            "topology_network_url": "/plugins/netbox_topology_views/topology/",
            "topology_power_url": "/plugins/netbox_topology_views/topology/",
            "counts": {
                "racks": 0,
                "devices": 0,
                "servers": 0,
                "pdus": 0,
                "power_panels": 0,
                "power_feeds": 0,
                "circuits": 0,
                "providers": 0,
                "cables": 0,
                "prefixes": 0,
                "vlans": 0,
                "asns": 0,
            },
            "racks": [],
            "power_feeds": [],
            "circuits": [],
        }

    racks = list(Rack.objects.filter(site=site).order_by("name"))
    devices = Device.objects.filter(site=site)
    servers = devices.exclude(name__in=design.ALL_DEVICES).exclude(role__slug="pdu")
    pdus = devices.filter(role__slug="pdu")
    panels = list(PowerPanel.objects.filter(site=site).order_by("name"))
    feeds = list(PowerFeed.objects.filter(power_panel__site=site).select_related("power_panel", "rack").order_by("name"))
    circuits = list(
        Circuit.objects.filter(tags__slug=seeder.TAG_SLUG)
        .select_related("provider", "type")
        .order_by("cid")
    )
    providers = Provider.objects.filter(tags__slug=seeder.TAG_SLUG)
    cables = Cable.objects.filter(tags__slug=seeder.TAG_SLUG)
    prefixes = Prefix.objects.filter(tags__slug=seeder.TAG_SLUG)
    vlans = VLAN.objects.filter(group__slug=design.SITE_SLUG)
    asns = ASN.objects.filter(sites=site)

    group_param = ""
    if topology_plugin_available():
        from netbox_topology_views.models import CoordinateGroup

        grp = CoordinateGroup.objects.filter(name=SHOWCASE_COORDINATE_GROUP).first()
        if grp is not None:
            group_param = f"&group={grp.pk}"

    base_topo = f"/plugins/netbox_topology_views/topology/?site_id={site.pk}&show_cables=true{group_param}"

    rack_rows = []
    for r in racks:
        r_devs = Device.objects.filter(rack=r)
        used_u = sum(int(getattr(d.device_type, "u_height", 1) or 1) for d in r_devs if d.position)
        rack_rows.append(
            {
                "name": r.name,
                "facility_id": r.facility_id or "",
                "location": r.location.name if r.location else "",
                "u_height": r.u_height,
                "used_u": used_u,
                "device_count": r_devs.count(),
                "url": r.get_absolute_url(),
                "description": r.description,
            }
        )

    feed_rows = [
        {
            "name": f.name,
            "panel": f.power_panel.name,
            "panel_url": f.power_panel.get_absolute_url(),
            "rack": f.rack.name if f.rack else "",
            "rack_url": f.rack.get_absolute_url() if f.rack else "",
            "type": f.type,
            "spec": f"{f.voltage}V / {f.amperage}A ({f.phase})",
            "available_power": f.available_power,
            "cabled": bool(f.cable_id),
            "url": f.get_absolute_url(),
        }
        for f in feeds
    ]

    circuit_rows = []
    for c in circuits:
        rate_gbps = f"{c.commit_rate // 1_000_000} Gbps" if (c.commit_rate and c.commit_rate >= 1_000_000) else (
            f"{c.commit_rate // 1_000} Mbps" if c.commit_rate else "—"
        )
        circuit_rows.append(
            {
                "cid": c.cid,
                "provider": c.provider.name,
                "type": c.type.name,
                "commit": rate_gbps,
                "description": c.description,
                "url": c.get_absolute_url(),
            }
        )

    return {
        "site": site,
        "topology_available": topology_plugin_available(),
        "topology_url": f"{base_topo}&show_circuit=true&show_power=true",
        "topology_network_url": f"{base_topo}&show_circuit=true&show_power=false",
        "topology_power_url": f"{base_topo}&show_circuit=false&show_power=true",
        "counts": {
            "racks": len(racks),
            "locations": Location.objects.filter(site=site).count(),
            "devices": devices.count(),
            "servers": servers.count(),
            "pdus": pdus.count(),
            "power_panels": len(panels),
            "power_feeds": len(feeds),
            "circuits": len(circuits),
            "providers": providers.count(),
            "cables": cables.count(),
            "prefixes": prefixes.count(),
            "vlans": vlans.count(),
            "asns": asns.count(),
        },
        "racks": rack_rows,
        "power_feeds": feed_rows,
        "circuits": circuit_rows,
    }


def clean_showcase_data() -> dict[str, int]:
    """Remove showcase-only extras (circuits, power, servers, racks) while keeping the 6 switches."""
    from circuits.models import Circuit, Provider, ProviderNetwork
    from dcim.models import Cable, Device, Location, PowerFeed, PowerPanel, PowerPort, Rack

    from . import seeder

    site = seeder.get_site()
    removed: dict[str, int] = {}

    # 1. Remove showcase cables (circuit, power, extra devices, MLAG Eth3/4) — keep spine↔leaf fabric links intact.
    from dcim.models import Interface

    fabric_core_ports = set(
        Interface.objects.filter(
            device__name__in=design.ALL_DEVICES,
            name__in=("Ethernet1", "Ethernet2", "Ethernet3", "Ethernet4"),
        )
        .exclude(
            device__name__in=design.LEAVES,
            name__in=("Ethernet3", "Ethernet4"),
        )
        .values_list("pk", flat=True)
    )
    showcase_cable_pks = []
    for cable in Cable.objects.filter(tags__slug=seeder.TAG_SLUG):
        a_terms = list(cable.a_terminations)
        b_terms = list(cable.b_terminations)
        if (
            len(a_terms) == 1
            and len(b_terms) == 1
            and isinstance(a_terms[0], Interface)
            and isinstance(b_terms[0], Interface)
            and a_terms[0].pk in fabric_core_ports
            and b_terms[0].pk in fabric_core_ports
        ):
            continue
        showcase_cable_pks.append(cable.pk)
    removed["cables"] = len(showcase_cable_pks)
    if showcase_cable_pks:
        Cable.objects.filter(pk__in=showcase_cable_pks).delete()

    # 2. Delete tagged circuits and provider networks.
    circuits = Circuit.objects.filter(tags__slug=seeder.TAG_SLUG)
    removed["circuits"] = circuits.count()
    circuits.delete()

    ProviderNetwork.objects.filter(tags__slug=seeder.TAG_SLUG).delete()

    # 3. Delete power feeds, power panels, and switch/server PSU ports.
    feeds = PowerFeed.objects.filter(tags__slug=seeder.TAG_SLUG)
    removed["power_feeds"] = feeds.count()
    feeds.delete()

    panels = PowerPanel.objects.filter(tags__slug=seeder.TAG_SLUG)
    removed["power_panels"] = panels.count()
    panels.delete()

    PowerPort.objects.filter(tags__slug=seeder.TAG_SLUG).delete()

    # 4. Delete extra showcase devices (servers & PDUs; keep the 6 fabric switches).
    extra_devices = (
        Device.objects.filter(site=site).exclude(name__in=design.ALL_DEVICES)
        if site
        else Device.objects.none()
    )
    removed["extra_devices"] = extra_devices.count()
    extra_devices.delete()

    # 5. Unmount the 6 fabric switches from racks before deleting racks & locations.
    if site is not None:
        Device.objects.filter(site=site, name__in=design.ALL_DEVICES).update(
            rack=None, position=None, face="", location=None
        )
        racks = Rack.objects.filter(site=site, tags__slug=seeder.TAG_SLUG)
        removed["racks"] = racks.count()
        racks.delete()

        locs = Location.objects.filter(site=site, tags__slug=seeder.TAG_SLUG).order_by("-level")
        removed["locations"] = locs.count()
        for loc in locs:
            loc.delete()

    providers = Provider.objects.filter(tags__slug=seeder.TAG_SLUG, circuits__isnull=True)
    removed["providers"] = providers.count()
    providers.delete()

    return removed
