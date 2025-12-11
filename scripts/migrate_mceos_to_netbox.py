#!/usr/bin/env python3
"""
Migration Tool: mceos YAML to NetBox

This script converts mceos Ansible YAML inventory and host_vars
into NetBox API objects for migration to a NetBox-centric workflow.

Features:
- Parse mceos inventory.yml for device hierarchy
- Extract host_vars/*.yml for device-specific data
- Extract group_vars/*/ for fabric-wide settings
- Map to NetBox API objects:
  - Devices with custom fields
  - Interfaces with IPs
  - Config contexts for tenants/MLAG
  - Cables for connectivity

Usage:
    python scripts/migrate_mceos_to_netbox.py --mceos-path /path/to/mceos/ansible
    python scripts/migrate_mceos_to_netbox.py --mceos-path /path/to/mceos/ansible --dry-run
    python scripts/migrate_mceos_to_netbox.py --mceos-path /path/to/mceos/ansible --export-only

Examples:
    # Dry run to see what would be imported
    python scripts/migrate_mceos_to_netbox.py --mceos-path ../mceos/ansible --dry-run

    # Export to local YAML files (for review before import)
    python scripts/migrate_mceos_to_netbox.py --mceos-path ../mceos/ansible --export-only

    # Full migration to NetBox
    python scripts/migrate_mceos_to_netbox.py --mceos-path ../mceos/ansible
"""

import argparse
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    import yaml
    from dotenv import load_dotenv
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
except ImportError:
    print("Required packages not installed. Run: pip install pyyaml rich python-dotenv")
    sys.exit(1)

console = Console()


@dataclass
class MceosDevice:
    """Represents a device extracted from mceos."""

    name: str
    hostname: str = ""
    serial_number: str = ""
    device_model: str = ""
    role: str = ""
    site: str = "DC1"
    bgp_asn: int = 0
    router_id: str = ""
    vtep_ip: str = ""
    switch_id: int = 0
    mlag_index: int = 0
    mlag_name: str = ""
    mlag_peer: str = ""
    management_ip: str = ""
    interfaces: list[dict] = field(default_factory=list)
    local_interfaces: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


@dataclass
class MceosMLAG:
    """Represents MLAG configuration from mceos."""

    name: str
    peerlink_vlan: int = 4094
    peerlink_channel: int = 2000
    peerlink_network: str = ""
    underlay_peering_network: str = ""
    interfaces: list[str] = field(default_factory=list)
    members: list[str] = field(default_factory=list)


@dataclass
class MceosTenant:
    """Represents a tenant/VRF from mceos."""

    name: str
    id: int
    l2_segments: list[dict] = field(default_factory=list)


class MceosParser:
    """Parse mceos Ansible project structure."""

    def __init__(self, mceos_path: Path):
        self.mceos_path = mceos_path
        self.inventory = {}
        self.host_vars = {}
        self.group_vars = {}
        self.devices: list[MceosDevice] = []
        self.mlag_configs: list[MceosMLAG] = []
        self.tenants: list[MceosTenant] = []

    def parse_all(self):
        """Parse all mceos data."""
        console.print("\n[bold]Parsing mceos project...[/bold]")

        self._parse_inventory()
        self._parse_group_vars()
        self._parse_host_vars()
        self._extract_devices()
        self._extract_mlag_configs()
        self._extract_tenants()

        console.print(f"  [green]✓[/green] Found {len(self.devices)} devices")
        console.print(f"  [green]✓[/green] Found {len(self.mlag_configs)} MLAG pairs")
        console.print(f"  [green]✓[/green] Found {len(self.tenants)} tenants")

    def _parse_inventory(self):
        """Parse inventory.yml."""
        inventory_file = self.mceos_path / "inventory.yml"
        if inventory_file.exists():
            with open(inventory_file) as f:
                self.inventory = yaml.safe_load(f)
            console.print("  [green]✓[/green] Parsed inventory.yml")
        else:
            console.print("  [yellow]![/yellow] inventory.yml not found")

    def _parse_group_vars(self):
        """Parse all group_vars directories."""
        group_vars_path = self.mceos_path / "group_vars"
        if not group_vars_path.exists():
            console.print("  [yellow]![/yellow] group_vars/ not found")
            return

        for group_dir in group_vars_path.iterdir():
            if group_dir.is_dir():
                group_name = group_dir.name
                self.group_vars[group_name] = {}
                for var_file in group_dir.glob("*.yml"):
                    try:
                        with open(var_file) as f:
                            data = yaml.safe_load(f)
                            if data:
                                self.group_vars[group_name][var_file.stem] = data
                    except Exception as e:
                        console.print(f"  [yellow]![/yellow] Error parsing {var_file}: {e}")

        console.print(f"  [green]✓[/green] Parsed {len(self.group_vars)} group_vars directories")

    def _parse_host_vars(self):
        """Parse all host_vars files."""
        host_vars_path = self.mceos_path / "host_vars"
        if not host_vars_path.exists():
            console.print("  [yellow]![/yellow] host_vars/ not found")
            return

        for host_file in host_vars_path.glob("*.yml"):
            hostname = host_file.stem
            try:
                with open(host_file) as f:
                    self.host_vars[hostname] = yaml.safe_load(f)
            except Exception as e:
                console.print(f"  [yellow]![/yellow] Error parsing {host_file}: {e}")

        console.print(f"  [green]✓[/green] Parsed {len(self.host_vars)} host_vars files")

    def _extract_devices(self):
        """Extract device information from parsed data."""
        # Get global settings
        all_vars = self.group_vars.get("all", {})
        bgp_asn_data = all_vars.get("bgp_asn", {}).get("bgp_asn", {})

        # Get site-specific setup data
        for _site_name, site_vars in self.group_vars.items():
            if "setup" in site_vars:
                setup = site_vars["setup"]
                setup.get("switch_ids", {})
                setup.get("vteps", {})
                setup.get("mgmt_ips", {})
                setup.get("mgmt_mask", 24)

        # Process each host
        for hostname, host_data in self.host_vars.items():
            if not host_data:
                continue

            # Determine role from groups
            role = self._determine_role(hostname)

            # Get ASN from bgp_asn mapping
            dc_asn = bgp_asn_data.get("da11", {})
            asn = dc_asn.get(hostname, dc_asn.get("spine" if role == "spine" else "base", 0))

            # Get switch_id and vtep from setup
            switch_id = 0
            vtep_id = 0
            mgmt_ip = ""

            for _site_name, site_vars in self.group_vars.items():
                if "setup" in site_vars:
                    setup = site_vars["setup"]
                    switch_id = setup.get("switch_ids", {}).get(hostname, 0)
                    vtep_id = setup.get("vteps", {}).get(hostname, 0)
                    mgmt_ip_addr = setup.get("mgmt_ips", {}).get(hostname)
                    if mgmt_ip_addr:
                        mgmt_ip = f"{mgmt_ip_addr}/{setup.get('mgmt_mask', 24)}"

            # Extract MLAG info
            mlag_index = host_data.get("mlag_index", 0)

            # Find MLAG name from group membership
            mlag_name = ""
            for _group_name, group_data in self.group_vars.items():
                if "mlag" in group_data:
                    mlag_data = group_data["mlag"]
                    if "mlag_name" in mlag_data:
                        # Check if this host is in this mlag group
                        mlag_name = mlag_data.get("mlag_name", "")

            device = MceosDevice(
                name=hostname,
                hostname=host_data.get("devicename", hostname),
                serial_number=host_data.get("serial_number", ""),
                device_model=host_data.get("device_model", ""),
                role=role,
                bgp_asn=int(asn) if asn else 0,
                switch_id=switch_id,
                mlag_index=mlag_index,
                mlag_name=mlag_name,
                management_ip=mgmt_ip,
                local_interfaces=host_data.get("local_interfaces", {}),
                tags=["containerlab", "evpn-fabric", role],
            )

            # Calculate router_id and vtep_ip from vrf_net
            for _site_name, site_vars in self.group_vars.items():
                if "setup" in site_vars:
                    setup = site_vars["setup"]
                    vrf_net = setup.get("vrf_net", {})
                    if "default" in vrf_net and switch_id:
                        # Parse base network and add switch_id
                        base = vrf_net["default"]
                        device.router_id = self._calculate_ip(base, switch_id)
                    if vtep_id:
                        device.vtep_ip = self._calculate_ip(
                            vrf_net.get("default", "10.255.0.0"), vtep_id
                        )

            # Extract interfaces from local_interfaces
            device.interfaces = self._extract_interfaces(device.local_interfaces)

            self.devices.append(device)

    def _determine_role(self, hostname: str) -> str:
        """Determine device role from hostname or inventory groups."""
        hostname_lower = hostname.lower()
        if "ssw" in hostname_lower:
            return "spine"
        elif "bsw" in hostname_lower:
            return "border"
        elif "lsw" in hostname_lower or "dsw" in hostname_lower:
            return "leaf"
        elif "msw" in hostname_lower:
            return "mgmt"
        return "unknown"

    def _calculate_ip(self, base_network: str, offset: int) -> str:
        """Calculate IP address from base network and offset."""
        try:
            import ipaddress

            base = ipaddress.ip_address(base_network)
            return str(base + offset)
        except Exception:
            return ""

    def _extract_interfaces(self, local_interfaces: dict) -> list[dict]:
        """Extract interface list from local_interfaces structure."""
        interfaces = []
        for vrf, intf_list in local_interfaces.items():
            if isinstance(intf_list, list):
                for intf in intf_list:
                    if isinstance(intf, dict):
                        interfaces.append(
                            {
                                "vrf": vrf,
                                "name": intf.get("name"),
                                "description": intf.get("description"),
                                "ip": intf.get("ip"),
                                "speed": intf.get("speed"),
                                "bgp_peers": intf.get("bgp", []),
                            }
                        )
        return interfaces

    def _extract_mlag_configs(self):
        """Extract MLAG configurations from group_vars."""
        for group_name, group_data in self.group_vars.items():
            if "mlag" in group_data:
                mlag_data = group_data["mlag"]
                if "mlag_domain" in mlag_data:
                    domain = mlag_data["mlag_domain"]
                    mlag = MceosMLAG(
                        name=mlag_data.get("mlag_name", group_name),
                        peerlink_vlan=domain.get("peerlink_vlan", 4094),
                        peerlink_channel=domain.get("peerlink_channel", 2000),
                        peerlink_network=domain.get("peerlink_network", ""),
                        underlay_peering_network=domain.get("underlay_peering_network", ""),
                        interfaces=domain.get("interfaces", []),
                    )
                    self.mlag_configs.append(mlag)

    def _extract_tenants(self):
        """Extract tenant configurations from group_vars."""
        for _group_name, group_data in self.group_vars.items():
            if "tenants" in group_data:
                tenants_data = group_data["tenants"]
                fabric_tenants = tenants_data.get("fabric_tenants", [])
                for tenant_data in fabric_tenants:
                    tenant = MceosTenant(
                        name=tenant_data.get("name"),
                        id=tenant_data.get("id", 0),
                        l2_segments=tenant_data.get("l2_segments", []),
                    )
                    self.tenants.append(tenant)


class NetBoxExporter:
    """Export parsed mceos data to NetBox format."""

    def __init__(self, parser: MceosParser):
        self.parser = parser

    def export_to_yaml(self, output_path: Path):
        """Export to local YAML files for review."""
        console.print(f"\n[bold]Exporting to {output_path}...[/bold]")
        output_path.mkdir(parents=True, exist_ok=True)

        # Export devices
        devices_data = [asdict(d) for d in self.parser.devices]
        with open(output_path / "devices.yml", "w") as f:
            yaml.dump(devices_data, f, default_flow_style=False)
        console.print(f"  [green]✓[/green] Exported {len(devices_data)} devices to devices.yml")

        # Export MLAG configs
        mlag_data = [asdict(m) for m in self.parser.mlag_configs]
        with open(output_path / "mlag_configs.yml", "w") as f:
            yaml.dump(mlag_data, f, default_flow_style=False)
        console.print(
            f"  [green]✓[/green] Exported {len(mlag_data)} MLAG configs to mlag_configs.yml"
        )

        # Export tenants
        tenant_data = [asdict(t) for t in self.parser.tenants]
        with open(output_path / "tenants.yml", "w") as f:
            yaml.dump(tenant_data, f, default_flow_style=False)
        console.print(f"  [green]✓[/green] Exported {len(tenant_data)} tenants to tenants.yml")

        # Generate NetBox-compatible fabric_topology.yml
        self._generate_fabric_topology(output_path)

    def _generate_fabric_topology(self, output_path: Path):
        """Generate NetBox-compatible fabric_topology.yml."""
        spines = []
        leaves = []

        for device in self.parser.devices:
            device_dict = {
                "name": device.name.replace("_", "-").lower(),
                "role": device.role,
                "asn": device.bgp_asn,
                "router_id": device.router_id,
                "vtep_ip": device.vtep_ip if device.role == "leaf" else None,
                "management_ip": device.management_ip,
                "switch_id": device.switch_id,
                "custom_fields": {
                    "bgp_asn": device.bgp_asn,
                    "router_id": device.router_id,
                    "vtep_ip": device.vtep_ip,
                    "switch_id": device.switch_id,
                    "mlag_index": device.mlag_index,
                    "mlag_name": device.mlag_name,
                },
                "tags": device.tags,
                "interfaces": {},
            }

            # Add interface IPs
            for intf in device.interfaces:
                if intf.get("ip"):
                    intf_name = intf["name"].lower()
                    device_dict["interfaces"][intf_name] = {
                        "ip": intf["ip"],
                        "description": intf.get("description", ""),
                    }

            if device.role == "spine":
                spines.append(device_dict)
            else:
                leaves.append(device_dict)

        topology = {
            "fabric": {
                "name": "migrated-fabric",
                "site": "DC1",
                "description": "Migrated from mceos",
            },
            "spines": spines,
            "leaves": leaves,
            "tenants": [asdict(t) for t in self.parser.tenants],
        }

        with open(output_path / "fabric_topology_migrated.yml", "w") as f:
            yaml.dump(topology, f, default_flow_style=False)
        console.print("  [green]✓[/green] Generated fabric_topology_migrated.yml")


class NetBoxImporter:
    """Import parsed mceos data into NetBox."""

    def __init__(self, parser: MceosParser, url: str, token: str, dry_run: bool = False):
        self.parser = parser
        self.url = url
        self.token = token
        self.dry_run = dry_run
        self.nb = None

    def connect(self) -> bool:
        """Connect to NetBox."""
        try:
            import pynetbox

            self.nb = pynetbox.api(self.url, token=self.token)
            self.nb.http_session.verify = False
            self.nb.status()
            console.print(f"[green]✓[/green] Connected to NetBox: {self.url}")
            return True
        except ImportError:
            console.print("[red]✗[/red] pynetbox not installed. Run: pip install pynetbox")
            return False
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to connect to NetBox: {e}")
            return False

    def import_all(self):
        """Import all parsed data to NetBox."""
        if not self.connect():
            return False

        console.print("\n[bold]Importing to NetBox...[/bold]")

        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
        ) as progress:
            task = progress.add_task("Importing devices...", total=None)
            self._import_devices()
            progress.update(task, description="[green]✓[/green] Devices imported")

            task = progress.add_task("Importing config contexts...", total=None)
            self._import_config_contexts()
            progress.update(task, description="[green]✓[/green] Config contexts imported")

        console.print("\n[green]✓ Migration complete![/green]")
        return True

    def _import_devices(self):
        """Import devices to NetBox."""
        for device in self.parser.devices:
            if self.dry_run:
                console.print(f"  [yellow]○[/yellow] Would create device: {device.name}")
                continue

            # Check if device exists
            existing = self.nb.dcim.devices.get(name=device.name)
            if existing:
                console.print(f"  [blue]↻[/blue] Device exists: {device.name}")
                continue

            # Get references
            device_type = self.nb.dcim.device_types.get(slug="arista-ceos-lab")
            role = self.nb.dcim.device_roles.get(slug=device.role)
            site = self.nb.dcim.sites.get(slug="dc1")

            if not all([device_type, role, site]):
                console.print(f"  [red]✗[/red] Missing prerequisites for {device.name}")
                continue

            try:
                cf = {
                    "bgp_asn": device.bgp_asn,
                    "router_id": device.router_id,
                    "vtep_ip": device.vtep_ip,
                    "switch_id": device.switch_id,
                    "mlag_index": device.mlag_index,
                    "mlag_name": device.mlag_name,
                    "mlag_peer": device.mlag_peer,
                }

                self.nb.dcim.devices.create(
                    name=device.name,
                    device_type=device_type.id,
                    role=role.id,
                    site=site.id,
                    status="active",
                    custom_fields=cf,
                    tags=[{"slug": t} for t in device.tags],
                )
                console.print(f"  [green]✓[/green] Created device: {device.name}")
            except Exception as e:
                console.print(f"  [red]✗[/red] Failed to create {device.name}: {e}")

    def _import_config_contexts(self):
        """Import MLAG and tenant config contexts."""
        # Import MLAG configs as config contexts
        for mlag in self.parser.mlag_configs:
            if self.dry_run:
                console.print(f"  [yellow]○[/yellow] Would create config context: {mlag.name}")
                continue

            ctx_name = f"mlag-{mlag.name.lower()}"
            ctx_data = {
                "name": ctx_name,
                "description": f"MLAG configuration for {mlag.name}",
                "weight": 4000,
                "is_active": True,
                "data": {
                    "mlag_domain": {
                        "peerlink_vlan": mlag.peerlink_vlan,
                        "peerlink_channel": mlag.peerlink_channel,
                        "peerlink_network": mlag.peerlink_network,
                        "underlay_peering_network": mlag.underlay_peering_network,
                        "interfaces": mlag.interfaces,
                    }
                },
            }

            try:
                existing = self.nb.extras.config_contexts.get(name=ctx_name)
                if existing:
                    existing.update(ctx_data)
                    console.print(f"  [blue]↻[/blue] Updated config context: {ctx_name}")
                else:
                    self.nb.extras.config_contexts.create(ctx_data)
                    console.print(f"  [green]✓[/green] Created config context: {ctx_name}")
            except Exception as e:
                console.print(f"  [red]✗[/red] Failed to create config context {ctx_name}: {e}")


def main():
    load_dotenv(Path(__file__).parent.parent / ".env")

    parser = argparse.ArgumentParser(
        description="Migrate mceos YAML to NetBox",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run to see what would be imported
  python scripts/migrate_mceos_to_netbox.py --mceos-path ../mceos/ansible --dry-run

  # Export to local YAML files (for review)
  python scripts/migrate_mceos_to_netbox.py --mceos-path ../mceos/ansible --export-only

  # Full migration to NetBox
  python scripts/migrate_mceos_to_netbox.py --mceos-path ../mceos/ansible
        """,
    )
    parser.add_argument("--mceos-path", required=True, help="Path to mceos ansible directory")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Export to local YAML files only (no NetBox import)",
    )
    parser.add_argument(
        "--output", default="output/migration", help="Output directory for exported files"
    )
    parser.add_argument("--url", default=os.environ.get("NETBOX_URL"), help="NetBox URL")
    parser.add_argument("--token", default=os.environ.get("NETBOX_TOKEN"), help="NetBox API token")

    args = parser.parse_args()

    console.print(
        Panel.fit(
            "[bold blue]mceos to NetBox Migration Tool[/bold blue]\n"
            "Convert mceos YAML to NetBox Source of Truth",
            border_style="blue",
        )
    )

    # Parse mceos data
    mceos_path = Path(args.mceos_path)
    if not mceos_path.exists():
        console.print(f"[red]Error: mceos path not found: {mceos_path}[/red]")
        sys.exit(1)

    mceos_parser = MceosParser(mceos_path)
    mceos_parser.parse_all()

    # Export to YAML
    exporter = NetBoxExporter(mceos_parser)
    exporter.export_to_yaml(Path(args.output))

    if args.export_only:
        console.print("\n[green]Export complete! Review files in output/migration/[/green]")
        sys.exit(0)

    # Import to NetBox
    if not args.url or not args.token:
        console.print("\n[yellow]NetBox URL/token not set - skipping import[/yellow]")
        console.print("Set NETBOX_URL and NETBOX_TOKEN environment variables or use --url/--token")
        sys.exit(0)

    importer = NetBoxImporter(mceos_parser, args.url, args.token, args.dry_run)
    importer.import_all()


if __name__ == "__main__":
    main()
