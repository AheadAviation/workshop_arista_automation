"""Ownership & lifecycle audit - the "can we trust our CMDB?" report.

Answers the questions that came up in the customer workshop:
  * Who owns this and who approves a change to it?   (device owner, else site owner)
  * Which sites have no standing change window?
  * What is still running an end-of-life OS?
  * What looks unneeded but is still running?

Read-only. Works on the add-on's objects by default (``--scope plants``) or on
everything in NetBox (``--scope all``).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from rich.table import Table

from .common import console


def _contacts_by_object(nb: Any) -> dict[tuple[str, int], dict[str, list[str]]]:
    """(object_type, object_id) -> {role_slug: [contact names, primary first]}."""
    order = {"primary": 0, "secondary": 1, "tertiary": 2, "inactive": 3}
    raw: dict[tuple[str, int], dict[str, list[tuple[int, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for item in nb.tenancy.contact_assignments.all():
        priority = item.priority.value if item.priority else "primary"
        raw[(item.object_type, item.object_id)][item.role.slug].append(
            (order.get(priority, 9), item.contact.name)
        )
    return {
        key: {role: [name for _, name in sorted(vals)] for role, vals in roles.items()}
        for key, roles in raw.items()
    }


def _effective(
    contacts: dict[tuple[str, int], dict[str, list[str]]],
    dev: Any,
    role: str,
    inherit_roles: set[str],
) -> tuple[list[str], str]:
    """Return (names, where) for a role.

    Device-level contacts always count. The site's contacts only count for device
    roles in ``inherit_roles`` (network gear) - a server nobody claimed must show up.
    """
    own = contacts.get(("dcim.device", dev.id), {}).get(role)
    if own:
        return own, "device"
    if dev.role and dev.role.slug in inherit_roles:
        inherited = contacts.get(("dcim.site", dev.site.id), {}).get(role)
        if inherited:
            return inherited, "site"
    return [], "none"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()  # noqa: DTZ007
    except ValueError:
        return None


def collect(
    nb: Any,
    tag: str,
    scope: str = "plants",
    horizon_days: int = 180,
    today: date | None = None,
    inherit_roles: set[str] | None = None,
) -> dict[str, Any]:
    """Gather the audit findings into a plain dict (also the --json output)."""
    today = today or date.today()
    inherit_roles = inherit_roles or set()
    if scope == "all":
        sites = list(nb.dcim.sites.all())
        devices = list(nb.dcim.devices.all())
    else:
        sites = list(nb.dcim.sites.filter(tag=tag))
        devices = list(nb.dcim.devices.filter(tag=tag))

    contacts = _contacts_by_object(nb)
    per_site: dict[str, int] = Counter(d.site.slug for d in devices)

    site_rows = []
    for site in sites:
        cf = site.custom_fields or {}
        roles = contacts.get(("dcim.site", site.id), {})
        site_rows.append(
            {
                "site": site.slug,
                "name": site.name,
                "region": site.region.name if site.region else "",
                "devices": per_site.get(site.slug, 0),
                "criticality": cf.get("criticality") or "",
                "owner": ", ".join(roles.get("owner", [])),
                "approver": ", ".join(roles.get("approver", [])),
                "change_window": cf.get("change_window") or "",
                "firmware_canary": bool(cf.get("firmware_canary")),
            }
        )

    no_owner, eol_past, eol_soon, decommission = [], [], [], []
    vendors: Counter = Counter()
    for dev in devices:
        cf = dev.custom_fields or {}
        slugs = {t.slug for t in dev.tags}
        status = dev.status.value if dev.status else ""
        try:
            vendors[dev.device_type.manufacturer.name] += 1
        except AttributeError:
            vendors["unknown"] += 1

        owners, where = _effective(contacts, dev, "owner", inherit_roles)
        row = {
            "device": dev.name,
            "site": dev.site.slug,
            "role": dev.role.name if dev.role else "",
            "status": status,
            "application": cf.get("business_application") or "",
            "owner": ", ".join(owners),
            "owner_from": where,
        }
        if not owners:
            no_owner.append(row)

        eol = _parse_date(cf.get("os_eol_date"))
        if eol:
            delta = (eol - today).days
            entry = {**row, "platform": dev.platform.name if dev.platform else "", "eol": str(eol)}
            if delta < 0:
                eol_past.append({**entry, "days_past_eol": -delta})
            elif delta <= horizon_days:
                eol_soon.append({**entry, "days_until_eol": delta})

        # A device the Meraki dashboard saw go down is broken, not retired.
        observed_down = status == "offline" and cf.get("source_system") == "meraki-api"
        if (
            "decommission-candidate" in slugs
            or status == "decommissioning"
            or (status == "offline" and not observed_down)
        ):
            decommission.append(row)

    return {
        "generated": str(today),
        "scope": scope,
        "totals": {
            "sites": len(sites),
            "devices": len(devices),
            "devices_without_owner": len(no_owner),
            "owner_coverage_pct": round(100 * (1 - len(no_owner) / max(len(devices), 1)), 1),
            "sites_without_change_window": sum(1 for r in site_rows if not r["change_window"]),
            "eol_os_devices": len(eol_past),
            "eol_within_horizon": len(eol_soon),
            "decommission_candidates": len(decommission),
        },
        "vendors": dict(vendors),
        "sites": sorted(site_rows, key=lambda r: r["site"]),
        "devices_without_owner": sorted(no_owner, key=lambda r: (r["site"], r["device"])),
        "sites_without_change_window": [r["site"] for r in site_rows if not r["change_window"]],
        "eol_os": sorted(eol_past, key=lambda r: -r["days_past_eol"]),
        "eol_soon": sorted(eol_soon, key=lambda r: r["days_until_eol"]),
        "decommission_candidates": decommission,
    }


def _table(title: str, columns: list[str], rows: list[list[str]], style: str) -> None:
    table = Table(title=title, title_style=style, header_style="bold")
    for position, col in enumerate(columns):
        table.add_column(col, no_wrap=position == 0)  # never truncate device/site names
    for row in rows:
        table.add_row(*[str(c) for c in row])
    console.print(table)


def render(report: dict[str, Any], horizon_days: int) -> None:
    totals = report["totals"]
    console.rule(
        f"[bold]Ownership & lifecycle audit[/bold]  ({report['scope']}, {report['generated']})"
    )
    console.print(
        f"  {totals['sites']} sites, {totals['devices']} devices  |  vendors: "
        + ", ".join(f"{k} {v}" for k, v in sorted(report["vendors"].items()))
    )
    console.print(
        f"  Owner coverage: [bold]{totals['owner_coverage_pct']}%[/bold]  "
        f"({totals['devices_without_owner']} devices with no owner)"
    )

    _table(
        "Sites",
        ["Site", "Region", "Devices", "Crit", "Owner", "Approver", "Change window"],
        [
            [
                r["site"],
                r["region"],
                r["devices"],
                r["criticality"],
                r["owner"],
                r["approver"],
                r["change_window"] or "[red]none - negotiated each time[/red]",
            ]
            for r in report["sites"]
        ],
        "cyan",
    )
    if report["devices_without_owner"]:
        _table(
            "No owner (device or site level)",
            ["Device", "Site", "Role", "Status", "Application"],
            [
                [r["device"], r["site"], r["role"], r["status"], r["application"] or "unknown"]
                for r in report["devices_without_owner"]
            ],
            "red",
        )
    if report["eol_os"]:
        _table(
            "End-of-life OS still in service",
            ["Device", "Site", "Platform", "EOL", "Years past", "Owner"],
            [
                [
                    r["device"],
                    r["site"],
                    r["platform"],
                    r["eol"],
                    f"{r['days_past_eol'] / 365:.1f}",
                    r["owner"] or "[red]none[/red]",
                ]
                for r in report["eol_os"]
            ],
            "red",
        )
    if report["eol_soon"]:
        _table(
            f"OS reaching end of life within {horizon_days} days",
            ["Device", "Site", "Platform", "EOL", "Days left", "Owner"],
            [
                [r["device"], r["site"], r["platform"], r["eol"], r["days_until_eol"], r["owner"]]
                for r in report["eol_soon"]
            ],
            "yellow",
        )
    if report["decommission_candidates"]:
        _table(
            "Decommission candidates",
            ["Device", "Site", "Status", "Owner"],
            [
                [r["device"], r["site"], r["status"], r["owner"] or "[red]nobody to ask[/red]"]
                for r in report["decommission_candidates"]
            ],
            "yellow",
        )


def run(
    nb: Any,
    plan: dict[str, Any],
    scope: str = "plants",
    as_json: bool = False,
    horizon_days: int = 180,
    fail_on_gaps: bool = False,
) -> int:
    inherit = set(plan.get("audit", {}).get("site_inherit_roles", []))
    report = collect(nb, plan["addon"]["tag"], scope, horizon_days, inherit_roles=inherit)
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        render(report, horizon_days)
    if fail_on_gaps and report["totals"]["devices_without_owner"]:
        return 1
    return 0
