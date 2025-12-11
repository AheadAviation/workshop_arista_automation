#!/usr/bin/env python3
"""
NetBox Bootstrap Script for Arista ContainerLab Demo

Initializes NetBox with the required data model:
- Custom fields for EVPN/VXLAN parameters (including mceos-style fields)
- Device roles (spine, leaf)
- Device types (Arista cEOS)
- Platforms
- Tags
- Sites
- Config contexts for tenants, MLAG, and fabric settings

Usage:
    python scripts/bootstrap_netbox.py [--dry-run]
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

try:
    import pynetbox
    from dotenv import load_dotenv
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:
    print("Required packages not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

console = Console()

# =============================================================================
# Configuration Constants
# =============================================================================

CUSTOM_FIELDS = [
    # Core BGP/Routing fields
    {
        "name": "bgp_asn",
        "type": "integer",
        "label": "BGP ASN",
        "description": "BGP Autonomous System Number for this device",
        "object_types": ["dcim.device"],
        "required": False,
        "weight": 100,
    },
    {
        "name": "router_id",
        "type": "text",
        "label": "Router ID",
        "description": "BGP/OSPF Router ID (Loopback0 IP)",
        "object_types": ["dcim.device"],
        "required": False,
        "weight": 105,
    },
    {
        "name": "vtep_ip",
        "type": "text",
        "label": "VTEP IP",
        "description": "VXLAN Tunnel Endpoint IP (Loopback1)",
        "object_types": ["dcim.device"],
        "required": False,
        "weight": 110,
    },
    # mceos-style fields for advanced config generation
    {
        "name": "switch_id",
        "type": "integer",
        "label": "Switch ID",
        "description": "Unique switch ID for loopback IP generation (from mceos switch_ids)",
        "object_types": ["dcim.device"],
        "required": False,
        "weight": 115,
    },
    {
        "name": "mlag_index",
        "type": "integer",
        "label": "MLAG Index",
        "description": "0 or 1 to identify MLAG primary/secondary switch",
        "object_types": ["dcim.device"],
        "required": False,
        "weight": 120,
    },
    {
        "name": "mlag_name",
        "type": "text",
        "label": "MLAG Name",
        "description": "MLAG domain name (e.g., MLAG-PAIR-1)",
        "object_types": ["dcim.device"],
        "required": False,
        "weight": 125,
    },
    {
        "name": "mlag_domain_id",
        "type": "integer",
        "label": "MLAG Domain ID",
        "description": "MLAG Domain ID for leaf pair",
        "object_types": ["dcim.device"],
        "required": False,
        "weight": 130,
    },
    {
        "name": "mlag_peer",
        "type": "text",
        "label": "MLAG Peer",
        "description": "Name of MLAG peer device",
        "object_types": ["dcim.device"],
        "required": False,
        "weight": 140,
    },
    {
        "name": "fabric_name",
        "type": "text",
        "label": "Fabric Name",
        "description": "Name of the EVPN fabric this device belongs to",
        "object_types": ["dcim.device"],
        "required": False,
        "weight": 90,
    },
    {
        "name": "device_type_model",
        "type": "text",
        "label": "Device Type Model",
        "description": "Device model for platform-specific logic (from mceos platform lookup)",
        "object_types": ["dcim.device"],
        "required": False,
        "weight": 150,
    },
]

DEVICE_ROLES = [
    {
        "name": "Spine",
        "slug": "spine",
        "color": "3f51b5",
        "description": "Fabric spine switch - BGP route server, no VTEP",
    },
    {
        "name": "Leaf",
        "slug": "leaf",
        "color": "4caf50",
        "description": "Standard leaf switch - VTEP, VLANs, server connectivity",
    },
    {
        "name": "Border",
        "slug": "border",
        "color": "ff9800",
        "description": "Border leaf switch - External connectivity, DCI",
    },
]

MANUFACTURERS = [
    {"name": "Arista", "slug": "arista"},
]

DEVICE_TYPES = [
    {
        "manufacturer": "arista",
        "model": "cEOS-lab",
        "slug": "arista-ceos-lab",
        "part_number": "cEOS",
        "u_height": 0,
        "is_full_depth": False,
        "comments": "Containerized EOS for ContainerLab",
        "interfaces": [
            {"name": f"eth{i}", "type": "10gbase-x-sfpp"} for i in range(1, 9)
        ] + [
            {"name": "Management0", "type": "1000base-t", "mgmt_only": True},
        ],
    },
]

PLATFORMS = [
    {"name": "Arista cEOS", "slug": "arista-ceos", "manufacturer": "arista", "napalm_driver": "eos"},
]

SITES = [
    {
        "name": "DC1",
        "slug": "dc1",
        "status": "active",
        "description": "Primary datacenter for ContainerLab demo",
    },
]

TAGS = [
    {"name": "containerlab", "slug": "containerlab", "color": "2196f3", "description": "ContainerLab managed device"},
    {"name": "evpn-fabric", "slug": "evpn-fabric", "color": "9c27b0", "description": "Part of EVPN/VXLAN fabric"},
    {"name": "spine", "slug": "spine", "color": "3f51b5", "description": "Spine switch"},
    {"name": "leaf", "slug": "leaf", "color": "4caf50", "description": "Leaf switch"},
    {"name": "border", "slug": "border", "color": "ff9800", "description": "Border leaf switch"},
    {"name": "mlag-pair-1", "slug": "mlag-pair-1", "color": "00bcd4", "description": "MLAG Pair 1"},
    {"name": "mlag-pair-2", "slug": "mlag-pair-2", "color": "009688", "description": "MLAG Pair 2"},
]

# Config contexts for fabric-wide settings
CONFIG_CONTEXTS = [
    # Fabric global settings
    {
        "name": "fabric-global",
        "description": "Global EVPN fabric defaults for all devices",
        "weight": 1000,
        "is_active": True,
        "data": {
            "evpn": {
                "model": "symmetric",
                "control_plane": "bgp",
                "data_plane": "vxlan",
            },
            "underlay": {
                "mtu": 9214,
                "routing_protocol": "ebgp",
            },
            "overlay": {
                "anycast_gateway_mac": "00:1c:73:00:00:99",
            },
            "management": {
                "vrf": "MGMT",
                "gateway": "172.30.30.1",
                "ntp_servers": ["172.30.30.1"],
                "dns_servers": ["172.30.30.1"],
            },
            "base_l2_vni": 10000,
        },
    },
    # Fabric underlay BGP settings
    {
        "name": "fabric-underlay",
        "description": "Underlay eBGP peer groups and ASN allocation",
        "weight": 1500,
        "is_active": True,
        "data": {
            "bgp_peer_groups": {
                "SPINE_IPV4": {
                    "remote_as_type": "spine",
                    "max_routes": 12000,
                    "send_community": True,
                },
                "LEAF_IPV4": {
                    "remote_as_type": "leaf",
                    "max_routes": 12000,
                    "send_community": True,
                },
            },
            "spine_asn": 65000,
            "leaf_asn_base": 65001,
        },
    },
    # Fabric overlay EVPN settings
    {
        "name": "fabric-overlay",
        "description": "EVPN overlay peer groups and VNI settings",
        "weight": 1500,
        "is_active": True,
        "data": {
            "evpn_peer_groups": {
                "SPINE_EVPN": {
                    "remote_as_type": "spine",
                    "update_source": "Loopback0",
                    "ebgp_multihop": 3,
                    "send_community": True,
                },
                "LEAF_EVPN": {
                    "remote_as_type": "leaf",
                    "update_source": "Loopback0",
                    "ebgp_multihop": 3,
                    "next_hop_unchanged": True,
                    "send_community": True,
                },
            },
        },
    },
    # Spine-specific configuration
    {
        "name": "role-spine",
        "description": "Spine-specific configuration",
        "weight": 2000,
        "is_active": True,
        "roles": ["spine"],
        "data": {
            "bgp": {
                "route_server": True,
                "evpn_overlay": True,
            },
            "evpn_peers": [],  # Will be populated with leaf loopbacks
        },
    },
    # Leaf-specific configuration
    {
        "name": "role-leaf",
        "description": "Leaf-specific configuration",
        "weight": 2000,
        "is_active": True,
        "roles": ["leaf"],
        "data": {
            "vxlan": {
                "enabled": True,
                "source_interface": "Loopback1",
            },
            "mlag": {
                "peer_link_vlan": 4094,
                "reload_delay_mlag": 300,
                "reload_delay_non_mlag": 330,
            },
            "evpn_peers": [],  # Will be populated with spine loopbacks
        },
    },
    # Tenant: CORE with 5 VLANs
    {
        "name": "tenant-core",
        "description": "CORE tenant with server VLANs",
        "weight": 3000,
        "is_active": True,
        "roles": ["leaf"],
        "data": {
            "tenants": [
                {
                    "name": "CORE",
                    "id": 1,
                    "loopback_network": "172.30.1.0/24",
                    "ibgp_network": "100.64.1.0/31",
                    "l2_segments": [
                        {
                            "vlan_number": 100,
                            "vlan_name": "SERVERS",
                            "vlan_ip": "10.100.100.1/24",
                            "mtu": 9000,
                            "description": "Server VLAN",
                        },
                        {
                            "vlan_number": 110,
                            "vlan_name": "MGMT_SERVERS",
                            "vlan_ip": "10.100.110.1/24",
                            "mtu": 1500,
                            "description": "Management VLAN",
                        },
                        {
                            "vlan_number": 120,
                            "vlan_name": "STORAGE",
                            "vlan_ip": "10.100.120.1/24",
                            "mtu": 9000,
                            "description": "Storage VLAN",
                        },
                        {
                            "vlan_number": 130,
                            "vlan_name": "BACKUP",
                            "vlan_ip": "10.100.130.1/24",
                            "mtu": 9000,
                            "description": "Backup VLAN",
                        },
                        {
                            "vlan_number": 140,
                            "vlan_name": "VOIP",
                            "vlan_ip": "10.100.140.1/24",
                            "mtu": 1500,
                            "description": "VoIP VLAN",
                        },
                    ],
                },
            ],
        },
    },
    # MLAG Pair 1 configuration (leaf-01, leaf-02)
    {
        "name": "mlag-pair-1",
        "description": "MLAG configuration for leaf-01/leaf-02 pair",
        "weight": 4000,
        "is_active": True,
        "tags": ["mlag-pair-1"],
        "data": {
            "mlag_domain": {
                "description": "MLAG PeerLink",
                "peerlink_vlan": 4094,
                "peerlink_channel": 2000,
                "peerlink_network": "169.254.1.0/31",
                "underlay_peering_network": "100.64.0.0/31",
                "ibgp_vlan": 4080,
                "interfaces": ["eth3", "eth4"],
            },
        },
    },
    # MLAG Pair 2 configuration (leaf-03, leaf-04)
    {
        "name": "mlag-pair-2",
        "description": "MLAG configuration for leaf-03/leaf-04 pair",
        "weight": 4000,
        "is_active": True,
        "tags": ["mlag-pair-2"],
        "data": {
            "mlag_domain": {
                "description": "MLAG PeerLink",
                "peerlink_vlan": 4094,
                "peerlink_channel": 2000,
                "peerlink_network": "169.254.1.2/31",
                "underlay_peering_network": "100.64.0.2/31",
                "ibgp_vlan": 4080,
                "interfaces": ["eth3", "eth4"],
            },
        },
    },
]


class NetBoxBootstrap:
    """Bootstrap NetBox with Arista ContainerLab data model."""

    def __init__(self, url: str, token: str, dry_run: bool = False):
        self.url = url
        self.token = token
        self.dry_run = dry_run
        self.nb = None
        self.stats = {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
        }

    def connect(self) -> bool:
        """Establish connection to NetBox."""
        try:
            self.nb = pynetbox.api(self.url, token=self.token)
            self.nb.http_session.verify = False
            # Test connection
            self.nb.status()
            console.print(f"[green]✓[/green] Connected to NetBox: {self.url}")
            return True
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to connect to NetBox: {e}")
            return False

    def _create_or_update(self, endpoint, data: dict, lookup_field: str = "name") -> Any:
        """Create or update a NetBox object."""
        lookup_value = data.get(lookup_field)

        try:
            existing = endpoint.get(**{lookup_field: lookup_value})

            if existing:
                if self.dry_run:
                    console.print(f"  [yellow]○[/yellow] Would update: {lookup_value}")
                    self.stats["skipped"] += 1
                else:
                    existing.update(data)
                    console.print(f"  [blue]↻[/blue] Updated: {lookup_value}")
                    self.stats["updated"] += 1
                return existing
            else:
                if self.dry_run:
                    console.print(f"  [yellow]○[/yellow] Would create: {lookup_value}")
                    self.stats["skipped"] += 1
                else:
                    result = endpoint.create(data)
                    console.print(f"  [green]✓[/green] Created: {lookup_value}")
                    self.stats["created"] += 1
                return result

        except pynetbox.RequestError as e:
            console.print(f"  [red]✗[/red] Error with {lookup_value}: {e.error}")
            self.stats["errors"] += 1
            return None
        except Exception as e:
            console.print(f"  [red]✗[/red] Unexpected error with {lookup_value}: {e}")
            self.stats["errors"] += 1
            return None

    def create_sites(self):
        """Create sites."""
        console.print("\n[bold]Creating Sites...[/bold]")
        for site in SITES:
            self._create_or_update(self.nb.dcim.sites, site, lookup_field="slug")

    def create_custom_fields(self):
        """Create custom fields for EVPN/VXLAN parameters."""
        console.print("\n[bold]Creating Custom Fields...[/bold]")
        for cf in CUSTOM_FIELDS:
            self._create_or_update(self.nb.extras.custom_fields, cf)

    def create_device_roles(self):
        """Create device roles for fabric topology."""
        console.print("\n[bold]Creating Device Roles...[/bold]")
        for role in DEVICE_ROLES:
            self._create_or_update(self.nb.dcim.device_roles, role, lookup_field="slug")

    def create_manufacturers(self):
        """Create manufacturers."""
        console.print("\n[bold]Creating Manufacturers...[/bold]")
        for mfr in MANUFACTURERS:
            self._create_or_update(self.nb.dcim.manufacturers, mfr, lookup_field="slug")

    def create_device_types(self):
        """Create device types with interface templates."""
        console.print("\n[bold]Creating Device Types...[/bold]")
        for dt in DEVICE_TYPES:
            dt_data = dt.copy()
            interfaces = dt_data.pop("interfaces", [])

            mfr = self.nb.dcim.manufacturers.get(slug=dt_data["manufacturer"])
            if mfr:
                dt_data["manufacturer"] = mfr.id

            result = self._create_or_update(self.nb.dcim.device_types, dt_data, lookup_field="slug")

            if result and interfaces and not self.dry_run:
                self._create_interface_templates(result, interfaces)

    def _create_interface_templates(self, device_type, interfaces: list):
        """Create interface templates for a device type."""
        for intf in interfaces:
            intf_data = {
                "device_type": device_type.id,
                "name": intf["name"],
                "type": intf["type"],
                "mgmt_only": intf.get("mgmt_only", False),
            }

            try:
                existing = self.nb.dcim.interface_templates.get(
                    device_type_id=device_type.id,
                    name=intf["name"]
                )
                if not existing:
                    self.nb.dcim.interface_templates.create(intf_data)
            except Exception:
                pass

    def create_platforms(self):
        """Create platforms for Arista devices."""
        console.print("\n[bold]Creating Platforms...[/bold]")
        for platform in PLATFORMS:
            platform_data = platform.copy()
            mfr = self.nb.dcim.manufacturers.get(slug=platform_data.pop("manufacturer"))
            if mfr:
                platform_data["manufacturer"] = mfr.id
            self._create_or_update(self.nb.dcim.platforms, platform_data, lookup_field="slug")

    def create_tags(self):
        """Create tags for fabric classification."""
        console.print("\n[bold]Creating Tags...[/bold]")
        for tag in TAGS:
            self._create_or_update(self.nb.extras.tags, tag, lookup_field="slug")

    def create_config_contexts(self):
        """Create config contexts for fabric settings."""
        console.print("\n[bold]Creating Config Contexts...[/bold]")
        for ctx in CONFIG_CONTEXTS:
            ctx_data = {
                "name": ctx["name"],
                "description": ctx.get("description", ""),
                "weight": ctx.get("weight", 1000),
                "is_active": ctx.get("is_active", True),
                "data": ctx["data"],
            }

            # Link to roles if specified
            if "roles" in ctx:
                role_ids = []
                for role_slug in ctx["roles"]:
                    role = self.nb.dcim.device_roles.get(slug=role_slug)
                    if role:
                        role_ids.append(role.id)
                if role_ids:
                    ctx_data["roles"] = role_ids

            # Link to tags if specified
            if "tags" in ctx:
                tag_ids = []
                for tag_slug in ctx["tags"]:
                    tag = self.nb.extras.tags.get(slug=tag_slug)
                    if tag:
                        tag_ids.append(tag.id)
                if tag_ids:
                    ctx_data["tags"] = tag_ids

            self._create_or_update(self.nb.extras.config_contexts, ctx_data)

    def print_summary(self):
        """Print bootstrap summary."""
        table = Table(title="Bootstrap Summary")
        table.add_column("Action", style="cyan")
        table.add_column("Count", justify="right")

        table.add_row("Created", str(self.stats["created"]))
        table.add_row("Updated", str(self.stats["updated"]))
        table.add_row("Skipped (dry-run)", str(self.stats["skipped"]))
        table.add_row("Errors", str(self.stats["errors"]))

        console.print("\n")
        console.print(table)

    def run(self):
        """Execute the bootstrap process."""
        console.print(Panel.fit(
            "[bold blue]NetBox Arista ContainerLab Demo[/bold blue]\n"
            "Bootstrap Script (Enhanced with mceos-style fields)",
            border_style="blue"
        ))

        if self.dry_run:
            console.print("[yellow]Running in DRY-RUN mode - no changes will be made[/yellow]\n")

        if not self.connect():
            return False

        # Execute bootstrap steps in order
        self.create_sites()
        self.create_manufacturers()
        self.create_device_roles()
        self.create_platforms()
        self.create_device_types()
        self.create_custom_fields()
        self.create_tags()
        self.create_config_contexts()

        self.print_summary()

        if self.stats["errors"] > 0:
            console.print("\n[red]Bootstrap completed with errors![/red]")
            return False

        console.print("\n[green]Bootstrap completed successfully![/green]")
        return True


def main():
    # Load environment variables
    load_dotenv(Path(__file__).parent.parent / ".env")

    parser = argparse.ArgumentParser(
        description="Bootstrap NetBox with Arista ContainerLab data model"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("NETBOX_URL"),
        help="NetBox URL (or set NETBOX_URL env var)"
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("NETBOX_TOKEN"),
        help="NetBox API token (or set NETBOX_TOKEN env var)"
    )

    args = parser.parse_args()

    if not args.url:
        console.print("[red]Error: NETBOX_URL not set. Use --url or set NETBOX_URL environment variable.[/red]")
        sys.exit(1)

    if not args.token:
        console.print("[red]Error: NETBOX_TOKEN not set. Use --token or set NETBOX_TOKEN environment variable.[/red]")
        sys.exit(1)

    bootstrap = NetBoxBootstrap(
        url=args.url,
        token=args.token,
        dry_run=args.dry_run
    )

    success = bootstrap.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
