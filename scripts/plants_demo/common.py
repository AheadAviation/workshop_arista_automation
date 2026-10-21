"""Shared helpers for the Plants Demo add-on.

Everything in this package is optional and additive: it never modifies objects
created by the core Arista fabric lab, and everything it creates carries the
``plants-demo`` tag (or a name listed in ``netbox_data/plants_demo.yml``) so it
can be located and removed again.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import pynetbox
    import yaml
    from dotenv import load_dotenv
    from rich.console import Console
except ImportError:
    print("Required packages not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLAN = REPO_ROOT / "netbox_data" / "plants_demo.yml"

console = Console()


@dataclass
class Stats:
    """Counters so each stage can report what it actually did."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0

    def line(self) -> str:
        return (
            f"{self.created} created, {self.updated} updated, "
            f"{self.unchanged} unchanged, {self.errors} errors"
        )


def load_plan(path: Path | None = None) -> dict[str, Any]:
    """Load the add-on data file."""
    plan_path = Path(path) if path else DEFAULT_PLAN
    if not plan_path.exists():
        console.print(f"[red]Plan file not found: {plan_path}[/red]")
        sys.exit(1)
    with open(plan_path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def connect(url: str | None = None, token: str | None = None) -> pynetbox.api:
    """Connect to NetBox using args, then NETBOX_URL / NETBOX_TOKEN."""
    load_dotenv(REPO_ROOT / ".env")
    url = url or os.environ.get("NETBOX_URL")
    token = token or os.environ.get("NETBOX_TOKEN")
    if not url or not token:
        console.print("[red]NETBOX_URL and NETBOX_TOKEN must be set (or pass --url/--token)[/red]")
        sys.exit(1)

    nb = pynetbox.api(url, token=token)
    nb.http_session.verify = False
    try:
        status = nb.status()
    except Exception as exc:  # noqa: BLE001 - surface any connection problem plainly
        console.print(f"[red]Cannot reach NetBox at {url}: {exc}[/red]")
        sys.exit(1)
    console.print(
        f"[green]✓[/green] Connected to NetBox {status.get('netbox-version', '?')} at {url}"
    )
    return nb


def netbox_version(nb: pynetbox.api) -> tuple[int, int]:
    """Return (major, minor) so version-dependent fields can be handled."""
    parts = str(nb.version).split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 4, 1


def _same(current: Any, wanted: Any) -> bool:
    """Compare a serialized NetBox value with the value we want to set."""
    if current in (None, "") and wanted in (None, ""):
        return True
    if isinstance(wanted, list) and isinstance(current, list):
        # an object custom field reads back as [{"id": 7, ...}] but is written as [7]
        current = [c["id"] if isinstance(c, dict) and "id" in c else c for c in current]
        return sorted(map(str, current)) == sorted(map(str, wanted))
    if isinstance(wanted, dict) and isinstance(current, dict):
        return all(_same(current.get(key), value) for key, value in wanted.items())
    if isinstance(wanted, (int, float)) and not isinstance(wanted, bool):
        try:  # NetBox returns decimals such as u_height 1.0 for an int we sent as 1
            return float(current) == float(wanted)
        except (TypeError, ValueError):
            return False
    return current == wanted


def upsert(
    endpoint: Any,
    lookup: dict[str, Any],
    data: dict[str, Any],
    stats: Stats,
    label: str | None = None,
    quiet: bool = False,
) -> Any | None:
    """Create or update one object, touching NetBox only when something differs.

    ``lookup`` identifies the object; ``data`` is the full desired state
    (including the lookup keys). Returns the pynetbox record, or None on error.
    """
    label = label or str(next(iter(lookup.values())))
    try:
        try:
            existing = endpoint.get(**lookup)
        except ValueError:  # more than one match - take the first, never create a duplicate
            existing = next(iter(endpoint.filter(**lookup)), None)

        if existing is None:
            try:
                created = endpoint.create(data)
            except pynetbox.RequestError as exc:
                # get() can miss a row NetBox still considers taken. Use that row.
                if "already exists" not in str(exc.error):
                    raise
                existing = next(iter(endpoint.filter(**lookup)), None)
                if existing is None:
                    raise
                stats.unchanged += 1
                return existing
            stats.created += 1
            if not quiet:
                console.print(f"  [green]+[/green] {label}")
            return created

        current = existing.serialize()
        changes = {key: val for key, val in data.items() if not _same(current.get(key), val)}
        if changes:
            existing.update(changes)
            stats.updated += 1
            if not quiet:
                console.print(f"  [blue]~[/blue] {label} ({', '.join(sorted(changes))})")
        else:
            stats.unchanged += 1
        return existing
    except pynetbox.RequestError as exc:
        stats.errors += 1
        console.print(f"  [red]✗[/red] {label}: {exc.error}")
    except Exception as exc:  # noqa: BLE001
        stats.errors += 1
        console.print(f"  [red]✗[/red] {label}: {exc}")
    return None


def expand_interfaces(device_type: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand a device type's interface definitions (names and ``pattern`` ranges)."""
    expanded: list[dict[str, Any]] = []
    for entry in device_type.get("interfaces", []):
        if "pattern" in entry:
            start = entry.get("start", 1)
            for number in range(start, start + entry["count"]):
                expanded.append(
                    {
                        "name": entry["pattern"].format(n=number),
                        "type": entry["type"],
                        "group": entry.get("group", "access"),
                        "mgmt_only": entry.get("mgmt_only", False),
                    }
                )
        else:
            expanded.append(
                {
                    "name": entry["name"],
                    "type": entry["type"],
                    "group": entry.get("group", "access"),
                    "mgmt_only": entry.get("mgmt_only", False),
                }
            )
    return expanded


def interface_groups(device_type: dict[str, Any]) -> dict[str, list[str]]:
    """Map group name -> ordered interface names for a device type."""
    groups: dict[str, list[str]] = {}
    for intf in expand_interfaces(device_type):
        groups.setdefault(intf["group"], []).append(intf["name"])
    return groups


def journal(nb: Any, object_type: str, object_id: int, text: str, kind: str = "info") -> bool:
    """Leave a note on an object's journal (what the UI calls a journal entry).

    Returns False instead of raising when the user may not write one, so a
    read-only role can still be told why.
    """
    try:
        nb.extras.journal_entries.create(
            assigned_object_type=object_type,
            assigned_object_id=object_id,
            kind=kind,
            comments=text,
        )
    except pynetbox.RequestError as exc:
        console.print(f"  [yellow]![/yellow] could not write the journal entry: {exc.error}")
        return False
    return True
