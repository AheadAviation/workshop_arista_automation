#!/usr/bin/env python3
"""Generate ContainerLab topology from NetBox - Docker-friendly version.

This version generates paths that work when running ContainerLab
inside a Docker container on Windows.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import pynetbox
import yaml
from rich.console import Console
from rich.panel import Panel

console = Console()


def get_devices(nb) -> list[dict[str, Any]]:
    """Fetch containerlab-tagged devices from NetBox."""
    devices = nb.dcim.devices.filter(tag="containerlab")
    result = []

    for device in devices:
        # Get primary IP for management
        mgmt_ip = None
        if device.primary_ip4:
            mgmt_ip = str(device.primary_ip4.address).split("/")[0]

        result.append(
            {
                "name": device.name,
                "role": device.role.slug if device.role else "unknown",
                "mgmt_ip": mgmt_ip,
            }
        )

    return sorted(result, key=lambda x: x["name"])


def get_cables(nb, devices: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Fetch cables between containerlab devices."""
    device_names = {d["name"] for d in devices}
    cables_list = []
    seen_cables = set()

    console.print(f"  Total cables in NetBox: {len(list(nb.dcim.cables.all()))}")

    for cable in nb.dcim.cables.all():
        try:
            if cable.id in seen_cables:
                continue

            a_terms = cable.a_terminations
            b_terms = cable.b_terminations

            if not a_terms or not b_terms:
                continue

            a_term = a_terms[0]
            b_term = b_terms[0]

            a_device = a_term.device.name
            b_device = b_term.device.name
            a_intf_name = a_term.name
            b_intf_name = b_term.name

            if a_device not in device_names or b_device not in device_names:
                continue

            seen_cables.add(cable.id)

            cables_list.append(
                {
                    "a_device": a_device,
                    "a_interface": normalize_interface(a_intf_name),
                    "b_device": b_device,
                    "b_interface": normalize_interface(b_intf_name),
                }
            )

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

    # Add nodes - using Docker-friendly paths
    for device in devices:
        node_config = {
            "kind": "ceos",
            # Path relative to /clab in the container
            "startup-config": f"/clab/configs/{device['name']}.cfg",
        }
        if device.get("mgmt_ip"):
            node_config["mgmt-ipv4"] = device["mgmt_ip"]

        topology["topology"]["nodes"][device["name"]] = node_config

    # Add links
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
    parser = argparse.ArgumentParser(
        description="Generate ContainerLab topology from NetBox (Docker version)"
    )
    parser.add_argument(
        "--output",
        "-o",
        default="clab/topology-docker.clab.yml",
        help="Output topology file path",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("NETBOX_URL", "http://localhost:8888"),
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
            "Docker-friendly version for Windows",
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

    # Add header comment
    header = """# =============================================================================
# ContainerLab Topology - Docker-friendly version for Windows
# =============================================================================
# Generated by: scripts/generate_clab_topology_docker.py
# Run with: docker compose run --rm clab containerlab deploy --topo /clab/topology/topology-docker.clab.yml
# =============================================================================

"""

    with open(output_path, "w") as f:
        f.write(header)
        yaml.dump(topology, f, default_flow_style=False, sort_keys=False)
        f.write("\n")

    console.print(f"\n[green]✓[/green] Topology written to: {output_path}")

    # Print summary
    console.print(
        f"""
{'='*60}
Topology Generation Complete!
{'='*60}

Nodes: {len(devices)}
Links: {len(cables)}

To deploy on Windows:
  docker compose build clab
  docker compose run --rm clab containerlab deploy --topo /clab/topology/topology-docker.clab.yml
"""
    )


if __name__ == "__main__":
    main()
