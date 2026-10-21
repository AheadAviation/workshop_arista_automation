"""Saved filters: the questions the team asked, one click away in the NetBox UI.

Each filter appears in the "Saved filters" menu on the matching list page (and as a
link: ``/dcim/devices/?filter=<slug>``). They are defined in the ``showcase`` block of
``netbox_data/plants_demo.yml``.

Trap this guards against: NetBox silently ignores a filter parameter it does not
understand and then shows *everything*. So every filter is tried against NetBox
before it is saved, and one that would not narrow the list is refused.
"""

from __future__ import annotations

from typing import Any

import pynetbox
from rich.table import Table

from .common import Stats, console, upsert

ENDPOINTS = {
    "dcim.device": ("dcim", "devices", "/dcim/devices/"),
    "dcim.site": ("dcim", "sites", "/dcim/sites/"),
    "ipam.prefix": ("ipam", "prefixes", "/ipam/prefixes/"),
}


def verdict(matches: int, total: int) -> str:
    """Judge a trial run of a filter: 'ok', 'empty' (matches nothing) or 'ignored'.

    'ignored' means it matched everything, which is what NetBox does with a
    parameter it does not recognise.
    """
    if total > 1 and matches == total:
        return "ignored"
    return "empty" if matches == 0 else "ok"


def run(nb: Any, plan: dict[str, Any]) -> Stats:
    console.print("\n[bold]Saved filters[/bold]")
    stats = Stats()
    table = Table(show_lines=False)
    table.add_column("Saved filter")
    table.add_column("Matches", justify="right")
    table.add_column("Open in NetBox", no_wrap=True)

    for position, item in enumerate(plan["showcase"]["saved_filters"]):
        app, name, path = ENDPOINTS[item["object_type"]]
        endpoint = getattr(getattr(nb, app), name)
        try:
            matches, total = endpoint.count(**item["parameters"]), endpoint.count()
        except pynetbox.RequestError as exc:
            stats.errors += 1
            console.print(
                f"  [red]✗[/red] {item['slug']}: NetBox rejected the filter ({exc.error})"
            )
            continue
        outcome = verdict(matches, total)
        if outcome == "ignored":
            stats.errors += 1
            console.print(
                f"  [red]✗[/red] {item['slug']}: it would show all {total} records, so a "
                "parameter is not understood by NetBox - not saved"
            )
            continue
        saved = upsert(
            nb.extras.saved_filters,
            {"slug": item["slug"]},
            {
                "name": item["name"],
                "slug": item["slug"],
                "object_types": [item["object_type"]],
                "description": item["description"],
                "weight": 100 + position,
                "enabled": True,
                "shared": True,
                "parameters": item["parameters"],
            },
            stats,
            item["slug"],
            quiet=True,
        )
        if saved is not None:
            note = "[dim]0[/dim]" if outcome == "empty" else str(matches)
            table.add_row(item["name"], note, f"{path}?filter={item['slug']}")
    console.print(table)
    console.print(f"  [dim]{stats.line()}[/dim]")
    return stats
