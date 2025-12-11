#!/usr/bin/env python3
"""
Full NetBox Import Pipeline

Orchestrates the complete import process:
1. Bootstrap NetBox with data model
2. Import fabric topology

Usage:
    python scripts/full_import.py
"""

import subprocess
import sys
from pathlib import Path

try:
    from rich.console import Console
    from rich.panel import Panel
except ImportError:
    print("Required: pip install rich")
    sys.exit(1)

console = Console()

SCRIPTS_DIR = Path(__file__).parent


def run_script(script_name: str, description: str) -> bool:
    """Run a Python script and return success status."""
    console.print(f"\n[bold blue]▶[/bold blue] {description}")

    script_path = SCRIPTS_DIR / script_name
    result = subprocess.run([sys.executable, str(script_path)], capture_output=False)

    if result.returncode != 0:
        console.print(f"[red]✗ {script_name} failed![/red]")
        return False

    return True


def main():
    console.print(
        Panel.fit(
            "[bold blue]NetBox Full Import Pipeline[/bold blue]\n" "Bootstrap + Topology Import",
            border_style="blue",
        )
    )

    # Step 1: Bootstrap NetBox
    if not run_script("bootstrap_netbox.py", "Bootstrapping NetBox data model..."):
        sys.exit(1)

    # Step 2: Import topology
    if not run_script("import_topology.py", "Importing fabric topology..."):
        sys.exit(1)

    console.print("\n" + "=" * 60)
    console.print("[bold green]✓ Full import complete![/bold green]")
    console.print("=" * 60)
    console.print("\nNext steps:")
    console.print("  1. Verify data in NetBox: http://localhost:8000")
    console.print("  2. Generate configs: ansible-playbook playbooks/generate_configs.yml")
    console.print("  3. Deploy lab: ansible-playbook playbooks/deploy_lab.yml")


if __name__ == "__main__":
    main()
