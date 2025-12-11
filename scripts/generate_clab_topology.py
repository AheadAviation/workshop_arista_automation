#!/usr/bin/env python3
"""Generate ContainerLab Topology from NetBox.

Dynamically generates a ContainerLab topology file by:
1. Querying all devices with 'containerlab' tag from NetBox
2. Extracting cable connections between devices
3. Building a complete topology with nodes and links

Usage:
    python scripts/generate_clab_topology.py
    python scripts/generate_clab_topology.py --output clab/topology.clab.yml
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

try:
    import pynetbox
    import yaml
    from dotenv import load_dotenv
    from rich.console import Console
    from rich.panel import Panel
except ImportError:
    print("Required packages not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

console = Console()


def get_devices(nb: pynetbox.api) -> list[dict[str, Any]]:
    """Get all devices with containerlab tag."""
    devices = list(nb.dcim.devices.filter(tag="containerlab", status="active"))

    device_list = []
    for device in devices:
        # Get primary IP for management
        mgmt_ip = None
        if device.primary_ip4:
            mgmt_ip = str(device.primary_ip4.address).split("/")[0]

        device_list.append({
            "name": device.name,
            "id": device.id,
            "role": device.role.slug if device.role else "unknown",
            "mgmt_ip": mgmt_ip,
        })

    return device_list


def get_cables(nb: pynetbox.api, devices: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Get all cables between containerlab devices."""
    device_names = {d["name"] for d in devices}

    cables_list = []
    seen_cables = set()

    all_cables = list(nb.dcim.cables.all())
    console.print(f"  Total cables in NetBox: {len(all_cables)}")

    for cable in all_cables:
        if cable.id in seen_cables:
            continue

        try:
            # Get terminations - they are already interface objects in newer pynetbox
            a_terms = cable.a_terminations
            b_terms = cable.b_terminations

            if not a_terms or not b_terms:
                continue

            a_intf = a_terms[0]
            b_intf = b_terms[0]

            # Get device and interface names
            a_device = str(a_intf.device)
            b_device = str(b_intf.device)
            a_intf_name = a_intf.name
            b_intf_name = b_intf.name

            # Only include cables between containerlab devices
            if a_device not in device_names or b_device not in device_names:
                continue

            seen_cables.add(cable.id)

            cables_list.append({
                "a_device": a_device,
                "a_interface": normalize_interface(a_intf_name),
                "b_device": b_device,
                "b_interface": normalize_interface(b_intf_name),
            })

        except Exception as e:
            console.print(f"  [yellow]Warning: Error processing cable {cable.id}: {e}[/yellow]")

    return cables_list


def normalize_interface(name: str) -> str:
    """Normalize interface name for cEOS."""
    name_lower = name.lower()
    if name_lower.startswith("ethernet"):
        num = name_lower.replace("ethernet", "")
        return f"eth{num}"
    elif name_lower.startswith("eth"):
        return name_lower
    return name


def generate_topology(
    devices: list[dict[str, Any]],
    cables: list[dict[str, str]],
    ceos_image: str = "ceos:4.32.0F",
    topology_name: str = "netbox-arista-lab",
    mgmt_subnet: str = "172.30.30.0/24",
    config_dir: str = "../output/configs",
) -> dict[str, Any]:
    """Generate ContainerLab topology dictionary."""
    topology = {
        "name": topology_name,
        "mgmt": {
            "network": f"{topology_name}_mgmt",
            "ipv4-subnet": mgmt_subnet,
        },
        "topology": {
            "kinds": {
                "ceos": {
                    "image": ceos_image,
                    "image-pull-policy": "never",
                }
            },
            "nodes": {},
            "links": [],
        },
    }

    # Add nodes
    for device in sorted(devices, key=lambda d: d["name"]):
        node_config = {
            "kind": "ceos",
            "startup-config": f"{config_dir}/{device['name']}.cfg",
        }
        if device["mgmt_ip"]:
            node_config["mgmt-ipv4"] = device["mgmt_ip"]

        topology["topology"]["nodes"][device["name"]] = node_config

    # Add links from cable data
    for cable in cables:
        link = {
            "endpoints": [
                f"{cable['a_device']}:{cable['a_interface']}",
                f"{cable['b_device']}:{cable['b_interface']}",
            ]
        }
        topology["topology"]["links"].append(link)

    return topology


def main():
    """Main entry point."""
    load_dotenv(Path(__file__).parent.parent / ".env")

    parser = argparse.ArgumentParser(description="Generate ContainerLab topology from NetBox")
    parser.add_argument(
        "--output",
        "-o",
        default="clab/topology.clab.yml",
        help="Output topology file path",
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
    parser.add_argument(
        "--image",
        default=os.environ.get("CEOS_IMAGE", "ceos:4.32.0F"),
        help="cEOS Docker image name",
    )

    args = parser.parse_args()

    if not args.url or not args.token:
        console.print("[red]Error: NETBOX_URL and NETBOX_TOKEN must be set[/red]")
        sys.exit(1)

    console.print(
        Panel.fit(
            "[bold blue]ContainerLab Topology Generator[/bold blue]\n"
            "Generating topology from NetBox",
            border_style="blue",
        )
    )

    # Connect to NetBox
    nb = pynetbox.api(args.url, token=args.token)
    nb.http_session.verify = False

    console.print(f"\n[cyan]Connected to NetBox:[/cyan] {args.url}")

    # Get devices
    console.print("\n[bold]Fetching devices...[/bold]")
    devices = get_devices(nb)
    console.print(f"  Found {len(devices)} containerlab devices:")
    for d in devices:
        console.print(f"    - {d['name']} ({d['role']}) - {d['mgmt_ip'] or 'no mgmt IP'}")

    # Get cables
    console.print("\n[bold]Fetching cables...[/bold]")
    cables = get_cables(nb, devices)

    if cables:
        console.print(f"  Found {len(cables)} fabric cables:")
        for c in cables:
            console.print(
                f"    - {c['a_device']}:{c['a_interface']} <-> {c['b_device']}:{c['b_interface']}"
            )
    else:
        console.print("  [yellow]Warning: No cables found between containerlab devices.[/yellow]")

    # Generate topology
    console.print("\n[bold]Generating topology...[/bold]")
    topology = generate_topology(
        devices=devices,
        cables=cables,
        ceos_image=args.image,
    )

    # Write output file
    output_path = Path(__file__).parent.parent / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Custom YAML formatting
    yaml_output = f"""# =============================================================================
# ContainerLab Topology - Dynamically Generated from NetBox
# =============================================================================
# Generated by: scripts/generate_clab_topology.py
# Source: {args.url}
# Devices: {len(devices)}
# Links: {len(cables)}
# =============================================================================

{yaml.dump(topology, default_flow_style=False, sort_keys=False)}
"""

    with open(output_path, "w") as f:
        f.write(yaml_output)

    console.print(f"\n[green]✓ Topology written to:[/green] {output_path}")

    # Print summary
    console.print("\n" + "=" * 60)
    console.print("[bold green]Topology Generation Complete![/bold green]")
    console.print("=" * 60)
    console.print(f"\nNodes: {len(devices)}")
    console.print(f"Links: {len(cables)}")
    console.print("\nTo deploy (in WSL2):")
    console.print(f"  sudo containerlab deploy --topo {args.output}")


if __name__ == "__main__":
    main()
