#!/usr/bin/env python3
"""
NetBox to AVD Data Transformation Script

Extracts network data from NetBox and transforms it into AVD-compatible
group_vars YAML files for use with the arista.avd Ansible collection.

This script bridges NetBox (Source of Truth) with AVD (Config Generation),
enabling a workflow where:
1. Network data lives in NetBox
2. AVD generates production-grade configurations
3. Configurations are pushed to devices

Usage:
    python scripts/netbox_to_avd.py
    python scripts/netbox_to_avd.py --dry-run
    python scripts/netbox_to_avd.py --output-dir avd/group_vars
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

try:
    import pynetbox
    import yaml
    from dotenv import load_dotenv
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:
    print("Required packages not installed. Run: pip install pynetbox pyyaml python-dotenv rich")
    sys.exit(1)

console = Console()


class NetBoxToAVD:
    """Transform NetBox data to AVD group_vars format."""

    def __init__(self, url: str, token: str, output_dir: str = "avd/group_vars"):
        self.url = url
        self.token = token
        self.output_dir = Path(output_dir)
        self.nb = None
        self.devices = []
        self.cables = []

    def connect(self) -> bool:
        """Connect to NetBox API."""
        try:
            self.nb = pynetbox.api(self.url, token=self.token)
            self.nb.http_session.verify = False
            self.nb.status()
            console.print(f"[green]✓[/green] Connected to NetBox: {self.url}")
            return True
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to connect: {e}")
            return False

    def fetch_data(self):
        """Fetch devices and cables from NetBox."""
        console.print("\n[bold]Fetching data from NetBox...[/bold]")

        # Get devices with containerlab tag
        self.devices = list(self.nb.dcim.devices.filter(tag="containerlab"))
        console.print(f"  Found {len(self.devices)} devices")

        # Get all cables
        self.cables = list(self.nb.dcim.cables.all())
        console.print(f"  Found {len(self.cables)} cables")

    def _get_device_custom_fields(self, device) -> Dict[str, Any]:
        """Extract custom fields from device."""
        cf = device.custom_fields or {}
        return {
            "bgp_asn": cf.get("bgp_asn"),
            "router_id": cf.get("router_id"),
            "vtep_ip": cf.get("vtep_ip"),
            "switch_id": cf.get("switch_id"),
            "mlag_index": cf.get("mlag_index"),
            "mlag_name": cf.get("mlag_name"),
        }

    def _get_device_role(self, device) -> str:
        """Get device role slug."""
        if device.role:
            return device.role.slug
        return "unknown"

    def _get_mgmt_ip(self, device) -> str:
        """Get management IP address."""
        if device.primary_ip:
            return str(device.primary_ip.address)
        return ""

    def _group_devices_by_role(self) -> Dict[str, List[Dict]]:
        """Group devices by their role."""
        groups = {"spine": [], "leaf": [], "border": []}

        for device in self.devices:
            role = self._get_device_role(device)
            cf = self._get_device_custom_fields(device)
            mgmt_ip = self._get_mgmt_ip(device)

            device_info = {
                "name": device.name,
                "id": cf.get("switch_id") or 1,
                "mgmt_ip": mgmt_ip,
                "bgp_asn": cf.get("bgp_asn"),
                "router_id": cf.get("router_id"),
                "vtep_ip": cf.get("vtep_ip"),
                "mlag_index": cf.get("mlag_index"),
                "mlag_name": cf.get("mlag_name"),
            }

            if role in groups:
                groups[role].append(device_info)
            else:
                groups["leaf"].append(device_info)  # Default to leaf

        return groups

    def _group_leaves_by_mlag(self, leaves: List[Dict]) -> List[Dict]:
        """Group leaf switches by MLAG pair."""
        mlag_groups = {}

        for leaf in leaves:
            mlag_name = leaf.get("mlag_name") or "STANDALONE"
            if mlag_name not in mlag_groups:
                mlag_groups[mlag_name] = {
                    "group": mlag_name.replace("-", "_").upper(),
                    "bgp_as": leaf.get("bgp_asn") or 65001,
                    "nodes": [],
                }
            mlag_groups[mlag_name]["nodes"].append(leaf)

        return list(mlag_groups.values())

    def generate_fabric_vars(self) -> Dict[str, Any]:
        """Generate FABRIC.yml content."""
        groups = self._group_devices_by_role()
        mlag_pairs = self._group_leaves_by_mlag(groups["leaf"])

        fabric = {
            "fabric_name": "NETBOX_FABRIC",
            "design": {"type": "l3ls-evpn"},
            # Spine configuration
            "spine": {
                "defaults": {
                    "platform": "cEOS-lab",
                    "bgp_as": groups["spine"][0]["bgp_asn"] if groups["spine"] else 65000,
                    "loopback_ipv4_pool": "10.255.0.0/24",
                    "bgp_defaults": [
                        "no bgp default ipv4-unicast",
                        "update wait-install",
                        "distance bgp 20 200 200",
                    ],
                },
                "nodes": [
                    {"name": s["name"], "id": s["id"], "mgmt_ip": s["mgmt_ip"]}
                    for s in groups["spine"]
                ],
            },
            # L3 Leaf configuration
            "l3leaf": {
                "defaults": {
                    "platform": "cEOS-lab",
                    "loopback_ipv4_pool": "10.255.0.0/24",
                    "vtep_loopback_ipv4_pool": "10.255.1.0/24",
                    "uplink_switches": [s["name"] for s in groups["spine"]],
                    "uplink_interfaces": ["Ethernet1", "Ethernet2"],
                    "mlag_interfaces": ["Ethernet3", "Ethernet4"],
                    "mlag_peer_ipv4_pool": "169.254.1.0/24",
                    "mlag_peer_l3_ipv4_pool": "100.64.0.0/24",
                    "virtual_router_mac_address": "00:1c:73:00:00:99",
                    "spanning_tree_mode": "mstp",
                    "spanning_tree_priority": 16384,
                },
                "node_groups": [
                    {
                        "group": pair["group"],
                        "bgp_as": pair["bgp_as"],
                        "nodes": [
                            {
                                "name": node["name"],
                                "id": node["id"],
                                "mgmt_ip": node["mgmt_ip"],
                            }
                            for node in pair["nodes"]
                        ],
                    }
                    for pair in mlag_pairs
                ],
            },
            # Underlay/Overlay settings
            "underlay_routing_protocol": "ebgp",
            "overlay_routing_protocol": "ebgp",
            "underlay_p2p_network_summary": "10.0.0.0/24",
            # Management
            "mgmt_gateway": "172.30.30.1",
            "mgmt_interface": "Management0",
            "mgmt_interface_vrf": "MGMT",
            "name_servers": ["172.30.30.1"],
            "ntp": {
                "servers": [{"name": "172.30.30.1", "vrf": "MGMT", "preferred": True}]
            },
            # Local users
            "local_users": [
                {"name": "admin", "privilege": 15, "role": "network-admin", "no_password": True}
            ],
        }

        return fabric

    def generate_network_services_vars(self) -> Dict[str, Any]:
        """Generate NETWORK_SERVICES.yml from NetBox config contexts."""
        # Try to get tenant data from config contexts
        tenants_data = []

        # Look for tenant config context
        try:
            contexts = self.nb.extras.config_contexts.filter(name="tenant-core")
            for ctx in contexts:
                if ctx.data and "tenants" in ctx.data:
                    tenants_data = ctx.data["tenants"]
                    break
        except Exception:
            pass

        if not tenants_data:
            # Default tenant structure
            tenants_data = [
                {
                    "name": "CORE",
                    "id": 1,
                    "l2_segments": [
                        {"vlan_number": 100, "vlan_name": "SERVERS", "vlan_ip": "10.100.100.1/24"},
                        {"vlan_number": 110, "vlan_name": "MGMT_SERVERS", "vlan_ip": "10.100.110.1/24"},
                        {"vlan_number": 120, "vlan_name": "STORAGE", "vlan_ip": "10.100.120.1/24", "mtu": 9000},
                        {"vlan_number": 130, "vlan_name": "BACKUP", "vlan_ip": "10.100.130.1/24", "mtu": 9000},
                        {"vlan_number": 140, "vlan_name": "VOIP", "vlan_ip": "10.100.140.1/24"},
                    ],
                }
            ]

        # Transform to AVD format
        avd_tenants = []
        for tenant in tenants_data:
            avd_tenant = {
                "name": tenant["name"],
                "mac_vrf_vni_base": 10000,
                "vrfs": [
                    {
                        "name": tenant["name"],
                        "vrf_vni": tenant.get("id", 1),
                        "svis": [],
                    }
                ],
            }

            # Add SVIs from l2_segments
            for segment in tenant.get("l2_segments", []):
                svi = {
                    "id": segment["vlan_number"],
                    "name": segment["vlan_name"],
                    "enabled": True,
                    "ip_virtual_router_addresses": [segment["vlan_ip"].split("/")[0]],
                }
                if segment.get("mtu"):
                    svi["mtu"] = segment["mtu"]

                avd_tenant["vrfs"][0]["svis"].append(svi)

            avd_tenants.append(avd_tenant)

        return {"tenants": avd_tenants}

    def write_files(self, dry_run: bool = False):
        """Write AVD group_vars files."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        files_to_write = [
            ("FABRIC.yml", self.generate_fabric_vars()),
            ("NETWORK_SERVICES.yml", self.generate_network_services_vars()),
        ]

        console.print("\n[bold]Writing AVD group_vars files...[/bold]")

        for filename, content in files_to_write:
            filepath = self.output_dir / filename
            if dry_run:
                console.print(f"  [yellow]○[/yellow] Would write: {filepath}")
            else:
                with open(filepath, "w") as f:
                    yaml.dump(content, f, default_flow_style=False, sort_keys=False)
                console.print(f"  [green]✓[/green] Wrote: {filepath}")

    def display_summary(self):
        """Display transformation summary."""
        groups = self._group_devices_by_role()

        table = Table(title="NetBox to AVD Transformation Summary")
        table.add_column("Category", style="cyan")
        table.add_column("Count", justify="right")

        table.add_row("Spines", str(len(groups["spine"])))
        table.add_row("Leaves", str(len(groups["leaf"])))
        table.add_row("Borders", str(len(groups["border"])))
        table.add_row("Total Devices", str(len(self.devices)))
        table.add_row("Cables", str(len(self.cables)))

        console.print("\n")
        console.print(table)

    def run(self, dry_run: bool = False):
        """Execute the transformation."""
        console.print(
            Panel.fit(
                "[bold blue]NetBox to AVD Transformation[/bold blue]\n"
                "Converting NetBox data to AVD group_vars",
                border_style="blue",
            )
        )

        if dry_run:
            console.print("[yellow]Running in DRY-RUN mode[/yellow]\n")

        if not self.connect():
            return False

        self.fetch_data()
        self.display_summary()
        self.write_files(dry_run)

        console.print("\n[green]✓ Transformation complete![/green]")
        console.print("\nNext steps:")
        console.print("  1. Review generated files in avd/group_vars/")
        console.print("  2. Run: ansible-playbook playbooks/generate_configs_avd.yml")

        return True


def main():
    load_dotenv(Path(__file__).parent.parent / ".env")

    parser = argparse.ArgumentParser(
        description="Transform NetBox data to AVD group_vars format"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without writing files",
    )
    parser.add_argument(
        "--output-dir",
        default="avd/group_vars",
        help="Output directory for AVD group_vars",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("NETBOX_URL"),
        help="NetBox URL",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("NETBOX_TOKEN"),
        help="NetBox API token",
    )

    args = parser.parse_args()

    if not args.url or not args.token:
        console.print("[red]Error: NETBOX_URL and NETBOX_TOKEN must be set[/red]")
        sys.exit(1)

    transformer = NetBoxToAVD(
        url=args.url,
        token=args.token,
        output_dir=args.output_dir,
    )

    success = transformer.run(dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

