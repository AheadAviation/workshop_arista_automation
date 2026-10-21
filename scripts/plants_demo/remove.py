"""Remove the Plants Demo add-on, leaving the core Arista lab untouched.

Default: deletes the sample DATA (cables, IPs, devices, prefixes, VLANs, sites,
contacts) - everything carrying the ``plants-demo`` tag. Fast to re-create.

``--purge-model`` also deletes the reference data the add-on introduced (device
types, roles, custom fields, regions, tenants, contact roles, RBAC users...).
Anything still referenced by something else is skipped and reported, never forced.
"""

from __future__ import annotations

from typing import Any

import pynetbox

from .common import Stats, console

DATA_ORDER = [  # children before parents
    ("cable", "dcim", "cables"),
    ("IP address", "ipam", "ip_addresses"),
    ("device", "dcim", "devices"),
    ("circuit", "circuits", "circuits"),  # takes its terminations with it
    ("provider", "circuits", "providers"),
    ("prefix", "ipam", "prefixes"),
    ("VLAN", "ipam", "vlans"),
    ("site", "dcim", "sites"),
    ("contact", "tenancy", "contacts"),
]


def _delete(endpoint: Any, records: list[Any], label: str, stats: Stats) -> None:
    if not records:
        return
    try:
        endpoint.delete(records)  # one bulk request
        stats.updated += len(records)
        console.print(f"  [red]-[/red] {len(records)} {label}")
        return
    except Exception:  # noqa: BLE001 - fall back to one-by-one so one bad record can't block the rest
        pass
    removed = 0
    for record in records:
        try:
            record.delete()
            removed += 1
        except Exception as exc:  # noqa: BLE001
            stats.errors += 1
            console.print(f"  [yellow]![/yellow] kept {label} '{record}': in use ({exc})")
    stats.updated += removed
    console.print(f"  [red]-[/red] {removed}/{len(records)} {label}")


def _drop_contact_assignments(nb: Any, tag: str, stats: Stats) -> None:
    """NetBox refuses to delete a contact that is still assigned to something.

    Sites/devices we delete take their assignments with them, but assignments on
    objects we do NOT delete (the core lab's DC1 via --link-fabric) must go first.
    """
    contact_ids = [c.id for c in _tagged(nb.tenancy.contacts, tag)]
    if not contact_ids:
        return
    leftovers = list(nb.tenancy.contact_assignments.filter(contact_id=contact_ids))
    _delete(nb.tenancy.contact_assignments, leftovers, "contact assignment", stats)


def _saved_filter_slugs(plan: dict[str, Any]) -> list[str]:
    return [item["slug"] for item in plan.get("showcase", {}).get("saved_filters", [])]


def _tagged(endpoint: Any, tag: str) -> list[Any]:
    """Objects carrying the tag. An unknown tag is an empty set.

    NetBox 4.1 returns 400 when a filter names a tag slug that is not defined,
    which is what is left after the tag itself has been removed.
    """
    try:
        return list(endpoint.filter(tag=tag))
    except pynetbox.RequestError as exc:
        if "not one of the available choices" in str(exc):
            return []
        raise


def _count_data(nb: Any, tag: str) -> dict[str, int]:
    return {
        label: len(_tagged(getattr(getattr(nb, app), endpoint), tag))
        for label, app, endpoint in DATA_ORDER
    }


def run(nb: Any, plan: dict[str, Any], confirm: bool, purge_model: bool) -> Stats:
    tag = plan["addon"]["tag"]
    stats = Stats()
    console.print("\n[bold]Remove Plants Demo add-on[/bold]")

    counts = _count_data(nb, tag)
    for label, number in counts.items():
        console.print(f"  {number:>4} {label}(s) tagged '{tag}'")
    filters = [
        f for slug in _saved_filter_slugs(plan) if (f := nb.extras.saved_filters.get(slug=slug))
    ]
    console.print(f"  {len(filters):>4} saved filter(s) from the add-on")
    if not confirm:
        console.print(
            "\n[yellow]Dry run - nothing deleted. Re-run with --yes to remove "
            "(add --purge-model to also remove the reference data).[/yellow]"
        )
        return stats

    for label, app, endpoint in DATA_ORDER:
        ep = getattr(getattr(nb, app), endpoint)
        if endpoint == "contacts":
            _drop_contact_assignments(nb, tag, stats)
        _delete(ep, _tagged(ep, tag), label, stats)
    _delete(nb.extras.saved_filters, filters, "saved filter", stats)

    if purge_model:
        _purge_model(nb, plan, stats)

    console.print(f"  [dim]{stats.line()}[/dim]")
    return stats


def _purge_model(nb: Any, plan: dict[str, Any], stats: Stats) -> None:
    console.print("[cyan]Reference data[/cyan]")

    def purge(endpoint: Any, field: str, values: list[str], label: str) -> None:
        records = [r for r in (endpoint.get(**{field: v}) for v in values) if r is not None]
        _delete(endpoint, records, label, stats)

    # users/groups/permissions first so nothing references the tag or objects
    purge(nb.users.users, "username", list(plan["rbac"]["users"]), "demo users")
    permission_names = [f"{g}-{kind}" for g in plan["rbac"]["groups"] for kind in ("view", "edit")]
    purge(nb.users.permissions, "name", permission_names, "permissions")
    purge(nb.users.groups, "name", list(plan["rbac"]["groups"]), "groups")

    purge(
        nb.extras.custom_fields, "name", [f["name"] for f in plan["custom_fields"]], "custom fields"
    )
    purge(
        nb.extras.custom_field_choice_sets,
        "name",
        [c["name"] for c in plan["custom_field_choice_sets"]],
        "choice sets",
    )
    purge(
        nb.dcim.device_types,
        "slug",
        [d["slug"] for d in plan["device_types"].values()],
        "device types",
    )
    purge(nb.dcim.platforms, "slug", [p["slug"] for p in plan["platforms"]], "platforms")
    purge(nb.dcim.device_roles, "slug", [r["slug"] for r in plan["device_roles"]], "device roles")
    purge(
        nb.dcim.manufacturers, "slug", [m["slug"] for m in plan["manufacturers"]], "manufacturers"
    )
    purge(nb.ipam.roles, "slug", [r["slug"] for r in plan["prefix_roles"]], "prefix roles")
    purge(
        nb.circuits.circuit_types,
        "slug",
        [t["slug"] for t in plan.get("circuit_types", [])],
        "circuit types",
    )
    purge(nb.tenancy.tenants, "slug", [t["slug"] for t in plan["tenants"]], "tenants")
    purge(
        nb.tenancy.tenant_groups,
        "slug",
        [g["slug"] for g in plan["tenant_groups"]],
        "tenant groups",
    )
    purge(
        nb.dcim.regions, "slug", [r["slug"] for r in reversed(plan["regions"])], "regions"
    )  # children first
    purge(
        nb.tenancy.contact_roles,
        "slug",
        [r["slug"] for r in plan["contact_roles"]],
        "contact roles",
    )
    purge(
        nb.tenancy.contact_groups,
        "slug",
        [g["slug"] for g in plan["contact_groups"]],
        "contact groups",
    )
    purge(nb.extras.tags, "slug", [t["slug"] for t in plan["tags"]], "tags")
