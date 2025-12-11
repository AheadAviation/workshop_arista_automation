#!/usr/bin/env python3
"""
Import Fabric Topology to NetBox

Imports a complete fabric topology from YAML:
- Devices (spines, leaves)
- Interfaces
- IP addresses
- Cables (interconnections)

Usage:
    python scripts/import_topology.py
    python scripts/import_topology.py --file netbox_data/fabric_topology.yml
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Optional

try:
    import pynetbox
    import yaml
    from dotenv import load_dotenv
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
except ImportError:
    print("Required packages not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

console = Console()


class TopologyImporter:
    """Import fabric topology from YAML into NetBox."""

    def __init__(self, nb: pynetbox.api, topology_data: dict):
        self.nb = nb
        self.data = topology_data
        self.created_devices = {}
        self.created_interfaces = {}

    def import_all(self):
        """Run the full import process."""
        console.print(
            Panel.fit(
                "[bold blue]NetBox Topology Import[/bold blue]\n" "Importing fabric topology",
                border_style="blue",
            )
        )

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
        ) as progress:
            # Import in dependency order
            task = progress.add_task("Importing devices...", total=None)
            self._import_devices()
            progress.update(task, description="[green]✓[/green] Devices imported")

            task = progress.add_task("Importing interfaces...", total=None)
            self._import_interfaces()
            progress.update(task, description="[green]✓[/green] Interfaces imported")

            task = progress.add_task("Importing IP addresses...", total=None)
            self._import_ip_addresses()
            progress.update(task, description="[green]✓[/green] IP addresses imported")

            task = progress.add_task("Importing cables...", total=None)
            self._import_cables()
            progress.update(task, description="[green]✓[/green] Cables imported")

        console.print("\n[green]✓ Topology import complete![/green]")

    def _get_or_create_device(self, device_data: dict) -> Optional[Any]:
        """Get or create a device."""
        name = device_data["name"]

        # Build comprehensive custom fields (including mceos-style fields)
        mlag_data = device_data.get("mlag", {})
        cf_source = device_data.get("custom_fields", {})

        cf = {
            "bgp_asn": cf_source.get("bgp_asn") or device_data.get("asn"),
            "router_id": cf_source.get("router_id") or device_data.get("router_id"),
            "vtep_ip": cf_source.get("vtep_ip") or device_data.get("vtep_ip"),
            "switch_id": cf_source.get("switch_id") or device_data.get("switch_id"),
            "mlag_index": cf_source.get("mlag_index") or mlag_data.get("index"),
            "mlag_name": cf_source.get("mlag_name")
            or mlag_data.get("name", f"MLAG-{mlag_data.get('domain_id', '')}"),
            "mlag_domain_id": cf_source.get("mlag_domain_id") or mlag_data.get("domain_id"),
            "mlag_peer": cf_source.get("mlag_peer") or mlag_data.get("peer"),
            "fabric_name": cf_source.get("fabric_name")
            or self.data.get("fabric", {}).get("name", "dc1-fabric"),
            "device_type_model": cf_source.get("device_type_model", "cEOS-lab"),
        }

        # Build tags list including MLAG pair tags
        base_tags = [
            {"slug": "containerlab"},
            {"slug": "evpn-fabric"},
            {"slug": device_data["role"]},
        ]
        for tag in device_data.get("tags", []):
            if isinstance(tag, str) and tag not in [t.get("slug") for t in base_tags]:
                base_tags.append({"slug": tag})

        # Check if device exists
        existing = self.nb.dcim.devices.get(name=name)
        if existing:
            console.print(f"  [blue]↻[/blue] Device exists: {name}")
            self.created_devices[name] = existing
            # Update custom fields
            existing.custom_fields = cf
            existing.save()
            return existing

        # Get references
        device_type = self.nb.dcim.device_types.get(slug="arista-ceos-lab")
        role = self.nb.dcim.device_roles.get(slug=device_data["role"])
        site = self.nb.dcim.sites.get(slug="dc1")
        platform = self.nb.dcim.platforms.get(slug="arista-ceos")

        if not all([device_type, role, site]):
            console.print(f"  [red]✗[/red] Missing prerequisites for {name}")
            return None

        try:
            device = self.nb.dcim.devices.create(
                name=name,
                device_type=device_type.id,
                role=role.id,
                site=site.id,
                platform=platform.id if platform else None,
                status="active",
                custom_fields=cf,
                tags=base_tags,
            )
            console.print(f"  [green]✓[/green] Created device: {name}")
            self.created_devices[name] = device
            return device
        except Exception as e:
            console.print(f"  [red]✗[/red] Failed to create {name}: {e}")
            return None

    def _import_devices(self):
        """Import all devices from topology."""
        console.print("\n[bold]Importing Devices...[/bold]")

        # Import spines
        for spine in self.data.get("spines", []):
            self._get_or_create_device(spine)

        # Import leaves
        for leaf in self.data.get("leaves", []):
            self._get_or_create_device(leaf)

    def _import_interfaces(self):
        """Import interfaces for all devices."""
        console.print("\n[bold]Importing Interfaces...[/bold]")

        all_devices = self.data.get("spines", []) + self.data.get("leaves", [])

        for device_data in all_devices:
            device_name = device_data["name"]
            device = self.created_devices.get(device_name)
            if not device:
                continue

            for intf_name, intf_data in device_data.get("interfaces", {}).items():
                self._get_or_create_interface(device, intf_name, intf_data)

    def _get_or_create_interface(self, device, intf_name: str, intf_data) -> Optional[Any]:
        """Get or create an interface."""
        # Normalize interface name for cEOS (eth1, eth2, etc.)
        normalized_name = self._normalize_interface_name(intf_name)

        # Check if interface exists
        existing = self.nb.dcim.interfaces.get(device_id=device.id, name=normalized_name)
        if existing:
            self.created_interfaces[(device.name, normalized_name)] = existing
            return existing

        # Determine interface type
        if "loopback" in intf_name.lower():
            intf_type = "virtual"
        elif "management" in intf_name.lower():
            intf_type = "1000base-t"
        else:
            intf_type = "10gbase-x-sfpp"

        # Get description
        description = ""
        if isinstance(intf_data, dict):
            if intf_data.get("peer"):
                description = f"to {intf_data['peer']} {intf_data.get('peer_intf', '')}"
            elif intf_data.get("description"):
                description = intf_data["description"]

        try:
            interface = self.nb.dcim.interfaces.create(
                device=device.id,
                name=normalized_name,
                type=intf_type,
                enabled=True,
                description=description,
            )
            self.created_interfaces[(device.name, normalized_name)] = interface
            return interface
        except Exception as e:
            # Interface might already exist from device type template
            existing = self.nb.dcim.interfaces.get(device_id=device.id, name=normalized_name)
            if existing:
                if description:
                    existing.description = description
                    existing.save()
                self.created_interfaces[(device.name, normalized_name)] = existing
                return existing
            console.print(f"  [yellow]![/yellow] Interface {normalized_name} on {device.name}: {e}")
            return None

    def _normalize_interface_name(self, name: str) -> str:
        """Normalize interface name for cEOS."""
        name_lower = name.lower()
        if name_lower.startswith("ethernet"):
            num = name_lower.replace("ethernet", "")
            return f"eth{num}"
        elif name_lower.startswith("loopback"):
            num = name_lower.replace("loopback", "")
            return f"Loopback{num}"
        elif name_lower.startswith("management"):
            return "Management0"
        return name

    def _import_ip_addresses(self):
        """Import IP addresses for all interfaces."""
        console.print("\n[bold]Importing IP Addresses...[/bold]")

        all_devices = self.data.get("spines", []) + self.data.get("leaves", [])

        for device_data in all_devices:
            device_name = device_data["name"]
            device = self.created_devices.get(device_name)
            if not device:
                continue

            # Import management IP
            mgmt_ip = device_data.get("management_ip")
            if mgmt_ip:
                self._assign_ip(device, "Management0", mgmt_ip, primary=True)

            # Import interface IPs
            for intf_name, intf_data in device_data.get("interfaces", {}).items():
                if isinstance(intf_data, str):
                    # Simple format: "10.255.0.1/32"
                    self._assign_ip(device, intf_name, intf_data)
                elif isinstance(intf_data, dict) and intf_data.get("ip"):
                    # Dict format: {ip: "10.0.0.0/31", ...}
                    self._assign_ip(device, intf_name, intf_data["ip"])

    def _assign_ip(self, device, intf_name: str, ip_address: str, primary: bool = False):
        """Assign an IP address to an interface."""
        normalized_name = self._normalize_interface_name(intf_name)
        interface = self.created_interfaces.get((device.name, normalized_name))

        if not interface:
            # Try to get interface directly
            interface = self.nb.dcim.interfaces.get(device_id=device.id, name=normalized_name)
            if interface:
                self.created_interfaces[(device.name, normalized_name)] = interface

        if not interface:
            console.print(
                f"  [yellow]![/yellow] Interface {normalized_name} not found on {device.name}"
            )
            return

        # Check if IP already exists
        existing_ip = self.nb.ipam.ip_addresses.get(address=ip_address)
        if existing_ip:
            # Update assignment if needed
            if existing_ip.assigned_object_id != interface.id:
                existing_ip.assigned_object_type = "dcim.interface"
                existing_ip.assigned_object_id = interface.id
                existing_ip.save()
            return

        try:
            ip = self.nb.ipam.ip_addresses.create(
                address=ip_address,
                assigned_object_type="dcim.interface",
                assigned_object_id=interface.id,
                status="active",
            )

            # Set as primary if specified
            if primary:
                device.primary_ip4 = ip.id
                device.save()

        except Exception as e:
            if "already exists" not in str(e).lower():
                console.print(f"  [yellow]![/yellow] IP {ip_address}: {e}")

    def _import_cables(self):
        """Import cables between devices."""
        console.print("\n[bold]Importing Cables...[/bold]")

        all_devices = self.data.get("spines", []) + self.data.get("leaves", [])
        processed_links = set()

        for device_data in all_devices:
            device_name = device_data["name"]

            for intf_name, intf_data in device_data.get("interfaces", {}).items():
                if not isinstance(intf_data, dict) or not intf_data.get("peer"):
                    continue

                peer_device = intf_data["peer"]
                peer_intf = intf_data.get("peer_intf", "")

                # Create a unique key for this link (sorted to avoid duplicates)
                link_key = tuple(sorted([(device_name, intf_name), (peer_device, peer_intf)]))

                if link_key in processed_links:
                    continue
                processed_links.add(link_key)

                self._create_cable(device_name, intf_name, peer_device, peer_intf)

    def _create_cable(self, device_a: str, intf_a: str, device_b: str, intf_b: str):
        """Create a cable between two interfaces."""
        norm_intf_a = self._normalize_interface_name(intf_a)
        norm_intf_b = self._normalize_interface_name(intf_b)

        interface_a = self.created_interfaces.get((device_a, norm_intf_a))
        interface_b = self.created_interfaces.get((device_b, norm_intf_b))

        if not interface_a:
            interface_a = self.nb.dcim.interfaces.get(device=device_a, name=norm_intf_a)
        if not interface_b:
            interface_b = self.nb.dcim.interfaces.get(device=device_b, name=norm_intf_b)

        if not interface_a or not interface_b:
            console.print(
                f"  [yellow]![/yellow] Cannot cable {device_a}:{intf_a} to {device_b}:{intf_b} - interface not found"
            )
            return

        # Check if cable already exists
        if interface_a.cable or interface_b.cable:
            return

        try:
            self.nb.dcim.cables.create(
                a_terminations=[{"object_type": "dcim.interface", "object_id": interface_a.id}],
                b_terminations=[{"object_type": "dcim.interface", "object_id": interface_b.id}],
                status="connected",
            )
            console.print(
                f"  [green]✓[/green] Cable: {device_a}:{norm_intf_a} <-> {device_b}:{norm_intf_b}"
            )
        except Exception as e:
            if "already" not in str(e).lower():
                console.print(
                    f"  [yellow]![/yellow] Cable {device_a}:{norm_intf_a} <-> {device_b}:{norm_intf_b}: {e}"
                )


def main():
    load_dotenv(Path(__file__).parent.parent / ".env")

    parser = argparse.ArgumentParser(description="Import fabric topology to NetBox")
    parser.add_argument(
        "--file", "-f", default="netbox_data/fabric_topology.yml", help="Path to topology YAML file"
    )
    parser.add_argument("--url", default=os.environ.get("NETBOX_URL"), help="NetBox URL")
    parser.add_argument("--token", default=os.environ.get("NETBOX_TOKEN"), help="NetBox API token")

    args = parser.parse_args()

    if not args.url or not args.token:
        console.print("[red]Error: NETBOX_URL and NETBOX_TOKEN must be set[/red]")
        sys.exit(1)

    # Load topology file
    topology_file = Path(__file__).parent.parent / args.file
    if not topology_file.exists():
        console.print(f"[red]Error: Topology file not found: {topology_file}[/red]")
        sys.exit(1)

    with open(topology_file) as f:
        topology_data = yaml.safe_load(f)

    # Connect to NetBox
    nb = pynetbox.api(args.url, token=args.token)
    nb.http_session.verify = False

    # Import topology
    importer = TopologyImporter(nb, topology_data)
    importer.import_all()


if __name__ == "__main__":
    main()
