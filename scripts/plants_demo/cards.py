"""Two one-page answers the team said they do not have today.

``impact <device>``  - before a change: who owns it, who has to approve, what is
                       downstream, which owners to tell, when the site allows
                       changes, and which sites to roll it out to first.
``triage <device>``  - when something is down (level 1): where it plugs in, what is
                       upstream, the likely cause, who to call, and the steps.

Both are read-only. They use only what NetBox already knows (cabling, owners,
approvers, change windows, status), so they are only as good as that data. The
runbook steps are EXAMPLES from ``showcase.runbooks`` in the data file: replace
them with the team's real procedures.

Device status is whatever NetBox last recorded, for instance from the last
``plants-meraki --apply``; neither card queries a live dashboard.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Any

from rich.table import Table

from .common import console

CRITICALITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
ROLE_KIND = {
    "firewall": "firewall",
    "access-switch": "switch",
    "wireless-ap": "ap",
    "server": "server",
    "ot-workstation": "endpoint",
}
Adjacency = dict[int, dict[int, tuple[str, str]]]  # device -> peer -> (own port, peer port)
# What a runbook step may mention; a typo in the data file is caught by the tests.
PLACEHOLDERS = frozenset(
    {
        "name",
        "model",
        "serial",
        "ip",
        "status",
        "site",
        "platform",
        "owner",
        "window",
        "criticality",
        "application",
        "upstream",
        "upstream_port",
        "upstream_status",
        "siblings_note",
        "blast_count",
        "blast_summary",
        "affected_owners",
    }
)


# ------------------------------------------------------- the graph (pure)
def reachable(adj: Adjacency, start: set[int], removed: set[int] | None = None) -> set[int]:
    """Every device that can be reached from ``start`` without passing through ``removed``."""
    removed = removed or set()
    seen = {node for node in start if node not in removed}
    queue = deque(seen)
    while queue:
        for peer in adj.get(queue.popleft(), {}):
            if peer not in seen and peer not in removed:
                seen.add(peer)
                queue.append(peer)
    return seen


def blast_radius(adj: Adjacency, roots: set[int], node: int) -> set[int]:
    """Devices that lose every path to a root if ``node`` fails.

    Redundancy counts: a device with another way round is not lost, and if ``node``
    is one of several roots (an HA firewall pair) the others keep their devices up.
    """
    before = reachable(adj, roots)
    if node not in before:
        return set()
    return before - reachable(adj, roots, removed={node}) - {node}


def path_to_root(adj: Adjacency, roots: set[int], node: int) -> list[int]:
    """Shortest chain of devices from ``node`` to the nearest root (empty if none)."""
    if node in roots:
        return [node]
    parent: dict[int, int | None] = {node: None}
    queue = deque([node])
    while queue:
        current = queue.popleft()
        for peer in adj.get(current, {}):
            if peer in parent:
                continue
            parent[peer] = current
            if peer in roots:
                chain = [peer]
                while chain[-1] != node:
                    chain.append(parent[chain[-1]])  # type: ignore[arg-type]
                return chain[::-1]
            queue.append(peer)
    return []


# -------------------------------------------------------------- the data
@dataclass
class Dev:
    id: int
    name: str
    role: str
    model: str
    type_id: int
    status: str
    site: str
    criticality: str
    application: str
    firmware: str
    serial: str
    ip: str
    platform: str
    source: str
    updated: str

    @property
    def kind(self) -> str:
        return ROLE_KIND.get(self.role, "generic")


@dataclass(frozen=True)
class Person:
    id: int
    name: str
    title: str
    group: str
    email: str
    phone: str

    def label(self) -> str:
        return f"{self.name} ({self.title})" if self.title else self.name


@dataclass
class SiteInfo:
    id: int
    slug: str
    name: str
    address: str
    zone: str
    criticality: str
    window: str
    canary: bool


def _dev(record: Any) -> Dev:
    cf = record.custom_fields or {}
    address = getattr(record.primary_ip4, "address", None)
    return Dev(
        id=record.id,
        name=record.name,
        role=record.role.slug if record.role else "",
        model=record.device_type.model,
        type_id=record.device_type.id,
        status=record.status.value if record.status else "",
        site=record.site.slug,
        criticality=cf.get("criticality") or "",
        application=cf.get("business_application") or "",
        firmware=cf.get("firmware_version") or "",
        serial=record.serial or "",
        ip=address.split("/")[0] if address else "",
        platform=record.platform.name if record.platform else "",
        source=cf.get("source_system") or "",
        updated=str(record.last_updated or "")[:16].replace("T", " "),
    )


def _site(record: Any) -> SiteInfo:
    cf = record.custom_fields or {}
    return SiteInfo(
        id=record.id,
        slug=record.slug,
        name=record.name,
        address=record.physical_address or "",
        zone=str(record.time_zone or ""),
        criticality=cf.get("criticality") or "",
        window=cf.get("change_window") or "",
        canary=bool(cf.get("firmware_canary")),
    )


def find_device(nb: Any, name: str) -> Any:
    exact = list(nb.dcim.devices.filter(name=name)) or list(nb.dcim.devices.filter(name__ie=name))
    if len(exact) == 1:
        return exact[0]
    if exact:
        raise LookupError(f"'{name}' matches {len(exact)} devices; names must be unique.")
    near = sorted(d.name for d in nb.dcim.devices.filter(name__ic=name))[:8]
    hint = f" Did you mean: {', '.join(near)}?" if near else ""
    raise LookupError(f"No device named '{name}'.{hint}")


def load_site(nb: Any, slug: str) -> tuple[dict[int, Dev], Adjacency]:
    """Every device at a site and the cabling between them."""
    # exclude=config_context: NetBox renders the lab's fabric-* contexts onto every device,
    # which made this one query take ~20 s for a large site. The cards never read them.
    devices = {
        d.id: _dev(d) for d in nb.dcim.devices.filter(site=slug, exclude="config_context")
    }
    adj: Adjacency = defaultdict(dict)
    for cable in nb.dcim.cables.filter(site=slug):
        ends = []
        for side in (cable.a_terminations, cable.b_terminations):
            ends.append(
                [
                    (t.object.device.id, t.object.name)
                    for t in side
                    if getattr(t.object, "device", None) is not None
                ]
            )
        for dev_a, port_a in ends[0]:
            for dev_b, port_b in ends[1]:
                adj[dev_a].setdefault(dev_b, (port_a, port_b))
                adj[dev_b].setdefault(dev_a, (port_b, port_a))
    return devices, dict(adj)


def load_contacts(nb: Any) -> dict[tuple[str, int], dict[str, list[Person]]]:
    """(object type, id) -> role -> people, primary first."""
    people = {
        c.id: Person(
            c.id,
            c.name,
            c.title or "",
            c.group.name if c.group else "",
            c.email or "",
            c.phone or "",
        )
        for c in nb.tenancy.contacts.all()
    }
    order = {"primary": 0, "secondary": 1, "tertiary": 2, "inactive": 3}
    raw: dict[tuple[str, int], dict[str, list[tuple[int, Person]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for item in nb.tenancy.contact_assignments.all():
        person = people.get(item.contact.id)
        if person is None:
            continue
        priority = order.get(item.priority.value if item.priority else "primary", 9)
        raw[(item.object_type, item.object_id)][item.role.slug].append((priority, person))
    return {
        key: {
            role: [p for _, p in sorted(vals, key=lambda v: v[0])] for role, vals in roles.items()
        }
        for key, roles in raw.items()
    }


def effective(
    contacts: dict[tuple[str, int], dict[str, list[Person]]],
    dev: Dev,
    site_id: int,
    role: str,
    inherit_roles: set[str],
) -> tuple[list[Person], str]:
    """The device's own contacts; network gear may fall back to its site's."""
    own = contacts.get(("dcim.device", dev.id), {}).get(role)
    if own:
        return own, "device"
    if dev.role in inherit_roles:
        inherited = contacts.get(("dcim.site", site_id), {}).get(role)
        if inherited:
            return inherited, "site"
    return [], "none"


class _Site:
    """Looks sites up once each."""

    def __init__(self, nb: Any):
        self.nb = nb
        self.cache: dict[str, SiteInfo] = {}

    def __call__(self, slug: str) -> SiteInfo:
        if slug not in self.cache:
            self.cache[slug] = _site(self.nb.dcim.sites.get(slug=slug))
        return self.cache[slug]


def names(people: list[Person]) -> str:
    return ", ".join(p.label() for p in people) or "nobody recorded"


def _fill(step: str, values: dict[str, Any]) -> str:
    class Blank(dict):
        def __missing__(self, key: str) -> str:
            return "?"

    return step.format_map(Blank(values))


def _tally(devs: list[Dev]) -> str:
    counts = Counter(d.role or "device" for d in devs)
    return ", ".join(f"{n} {role}" for role, n in counts.most_common()) or "none"


# ---------------------------------------------------------------- impact
def rollout_order(nb: Any, dev: Dev, site: _Site) -> list[dict[str, Any]]:
    """Where else this model runs, safest place to start first."""
    per_site: dict[str, list[Any]] = defaultdict(list)
    for record in nb.dcim.devices.filter(device_type_id=dev.type_id, exclude="config_context"):
        per_site[record.site.slug].append(record)
    rows = []
    for slug, records in per_site.items():
        info = site(slug)
        versions = Counter(
            (r.custom_fields or {}).get("firmware_version") or "unknown" for r in records
        )
        rows.append(
            {
                "site": slug,
                "name": info.name,
                "devices": len(records),
                "criticality": info.criticality or "?",
                "window": info.window or "negotiate each time",
                "canary": info.canary,
                "firmware": ", ".join(f"{v} x{n}" for v, n in versions.most_common()),
            }
        )
    rows.sort(key=lambda r: (not r["canary"], CRITICALITY_RANK.get(r["criticality"], 1), r["site"]))
    return rows


def build_impact(nb: Any, plan: dict[str, Any], name: str) -> dict[str, Any]:
    record = find_device(nb, name)
    dev = _dev(record)
    site = _Site(nb)
    info = site(dev.site)
    inherit = set(plan["audit"]["site_inherit_roles"])
    devices, adj = load_site(nb, dev.site)
    roots = {d.id for d in devices.values() if d.role == "firewall"}
    contacts = load_contacts(nb)

    lost = [devices[i] for i in blast_radius(adj, roots, dev.id)] if roots and dev.id in adj else []
    lost.sort(key=lambda d: (-CRITICALITY_RANK.get(d.criticality, 1), d.name))

    def who(target: Dev, role: str) -> tuple[list[Person], str]:
        return effective(contacts, target, info.id, role, inherit)

    owners, owner_from = who(dev, "owner")
    approvers, approver_from = who(dev, "approver")
    affected = []
    to_tell: dict[int, dict[str, Any]] = {}

    def tell(people: list[Person], role: str, about: str) -> None:
        for person in people:
            entry = to_tell.setdefault(person.id, {"person": person, "roles": set(), "about": []})
            entry["roles"].add(role)
            if about not in entry["about"]:
                entry["about"].append(about)

    tell(owners, "owner", dev.name)
    tell(approvers, "approver", dev.name)
    for other in lost:
        other_owners, _ = who(other, "owner")
        other_approvers, _ = who(other, "approver")
        affected.append(
            {
                "device": other.name,
                "role": other.role,
                "status": other.status,
                "criticality": other.criticality or "?",
                "application": other.application,
                "owner": names(other_owners),
                "approver": names(other_approvers) if other_approvers else "",
            }
        )
        tell(other_owners, "owner", other.name)
        tell(other_approvers, "approver", other.name)

    uncabled = sorted(d.name for d in devices.values() if d.id not in adj)
    checks = list(plan["showcase"]["post_change_checks"].get(dev.kind) or [])
    checks = checks or list(plan["showcase"]["post_change_checks"]["generic"])
    if lost:
        checks.insert(0, f"Each of the {len(lost)} devices listed above still responds")

    return {
        "device": {
            "name": dev.name,
            "role": dev.role,
            "model": dev.model,
            "status": dev.status,
            "criticality": dev.criticality,
            "application": dev.application,
        },
        "site": {"slug": info.slug, "name": info.name, "criticality": info.criticality},
        "owner": {"people": names(owners), "from": owner_from},
        "approver": {"people": names(approvers), "from": approver_from},
        "change_window": info.window or "",
        "no_firewall_recorded": not roots,
        "cabled": dev.id in adj,
        "lost_connectivity": affected,
        "highest_criticality_affected": max(
            (a["criticality"] for a in affected),
            key=lambda c: CRITICALITY_RANK.get(c, 1),
            default="",
        ),
        "to_tell": [
            {
                "name": e["person"].label(),
                "as": sorted(e["roles"]),
                "about": e["about"],
                "email": e["person"].email,
                "phone": e["person"].phone,
            }
            for e in to_tell.values()
        ],
        "rollout": rollout_order(nb, dev, site),
        "checks": checks,
        "not_cabled_at_site": uncabled,
        "ha_peers": sorted(devices[i].name for i in roots - {dev.id}) if dev.id in roots else [],
    }


def _status(value: str) -> str:
    colour = "green" if value == "active" else "red" if value == "offline" else "yellow"
    return f"[{colour}]{value}[/{colour}]"


def render_impact(report: dict[str, Any]) -> None:
    dev, site = report["device"], report["site"]
    console.print(
        f"\n[bold]Change impact: {dev['name']}[/bold]  "
        f"({dev['role'] or 'device'}, {dev['model']}) at {site['name']}  "
        f"status {_status(dev['status'])}  criticality {dev['criticality'] or '?'}"
    )

    who = Table(title="Who to talk to", show_header=False)
    who.add_column(style="bold")
    who.add_column()
    who.add_row("Owner", f"{report['owner']['people']}  [dim]({report['owner']['from']})[/dim]")
    who.add_row(
        "Approver", f"{report['approver']['people']}  [dim]({report['approver']['from']})[/dim]"
    )
    who.add_row(
        "Change window",
        report["change_window"] or "[yellow]none recorded - negotiated each time[/yellow]",
    )
    console.print(who)

    lost = report["lost_connectivity"]
    if report["no_firewall_recorded"]:
        console.print(
            "\n[yellow]No firewall is recorded at this site, so what depends on it "
            "cannot be worked out.[/yellow]"
        )
    elif not report["cabled"]:
        console.print(
            "\n[yellow]This device has no cabling recorded in NetBox, so what depends on "
            "it cannot be worked out.[/yellow]"
        )
    elif not lost and report["ha_peers"]:
        console.print(
            f"\n[green]Nothing loses connectivity[/green]: {', '.join(report['ha_peers'])} "
            "still connects the site."
        )
    elif not lost:
        console.print(
            "\n[green]Nothing loses connectivity if it goes down[/green] "
            "(nothing is cabled behind it, or there is another path)."
        )
    else:
        table = Table(
            title=f"If it goes down, {len(lost)} device(s) lose their path to the firewall"
        )
        for column in ("Devices", "Criticality", "Owner"):
            table.add_column(column, overflow="fold")
        groups: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
        for row in lost:
            key = (row["role"], row["criticality"], row["application"], row["owner"])
            groups[key].append(row["device"])
        for (role, criticality, application, owner), members in groups.items():
            detail = f"\n[dim]{application}[/dim]" if application else ""
            table.add_row(
                f"{len(members)} x {role}: {', '.join(members)}{detail}", criticality, owner
            )
        console.print(table)

    tell = Table(title="People to tell or ask before changing it")
    for column in ("Person", "As", "About"):
        tell.add_column(column, overflow="fold")
    for entry in report["to_tell"]:
        tell.add_row(entry["name"], ", ".join(entry["as"]), ", ".join(entry["about"]))
    console.print(tell if report["to_tell"] else "[yellow]Nobody is recorded for it.[/yellow]")

    roll = Table(title=f"Rollout order for {dev['model']} (start at the top)")
    for column in ("Site", "Devices", "Criticality", "Change window", "Firmware", ""):
        roll.add_column(column, overflow="fold")
    for number, row in enumerate(report["rollout"]):
        flag = ", ".join(
            label
            for label, shown in (
                ("canary", row["canary"]),
                ("start here", number == 0 and not row["canary"]),
                ("this site", row["site"] == site["slug"]),
            )
            if shown
        )
        roll.add_row(
            row["name"],
            str(row["devices"]),
            row["criticality"],
            row["window"],
            row["firmware"],
            flag,
        )
    console.print(roll)
    if report["rollout"] and not any(row["canary"] for row in report["rollout"]):
        console.print(
            "[yellow]No canary site runs this model, so the order is simply "
            "lowest criticality first.[/yellow]"
        )

    console.print(
        "\n[bold]Check after the change[/bold]  [dim](examples; edit in the data file)[/dim]"
    )
    for item in report["checks"]:
        console.print(f"  \\[ ] {item}")
    if report["not_cabled_at_site"]:
        console.print(
            "\n[dim]Recorded at this site but not cabled in NetBox, so not counted above: "
            + ", ".join(report["not_cabled_at_site"])
            + "[/dim]"
        )


# ---------------------------------------------------------------- triage
def runbook_for(plan: dict[str, Any], kind: str, vendor: str) -> list[str]:
    """The vendor's own runbook for this kind of device, else the kind's, else the generic one."""
    books = plan["showcase"]["runbooks"]
    return books.get(f"{kind}-{vendor}") or books.get(kind) or books["generic"]


def build_triage(nb: Any, plan: dict[str, Any], name: str) -> dict[str, Any]:
    record = find_device(nb, name)
    dev = _dev(record)
    vendor = getattr(getattr(record.device_type, "manufacturer", None), "slug", "") or ""
    site = _Site(nb)(dev.site)
    inherit = set(plan["audit"]["site_inherit_roles"])
    devices, adj = load_site(nb, dev.site)
    roots = {d.id for d in devices.values() if d.role == "firewall"}
    contacts = load_contacts(nb)

    path = path_to_root(adj, roots, dev.id) if roots else []
    hops = []
    for here, there in zip(path, path[1:], strict=False):
        own_port, peer_port = adj[here][there]
        hops.append(
            {
                "from": devices[here].name,
                "from_port": own_port,
                "to": devices[there].name,
                "to_port": peer_port,
                "to_status": devices[there].status,
            }
        )

    upstream = devices[path[1]] if len(path) > 1 else None
    upstream_port = adj[dev.id][upstream.id][1] if upstream else "?"
    beyond = {path[2]} if len(path) > 2 else set()
    siblings = (
        [devices[i] for i in adj[upstream.id] if i != dev.id and i not in beyond]
        if upstream
        else []
    )
    siblings_down = [s for s in siblings if s.status != "active"]

    suspect = next((devices[i] for i in reversed(path[1:]) if devices[i].status != "active"), None)
    if suspect is not None:
        cause = (
            f"{suspect.name} is {suspect.status}: everything behind it would be down too. "
            "Deal with it first."
        )
    elif path and dev.status != "active":
        cause = (
            "Everything upstream looks healthy, so the fault is probably this device itself "
            "or its cable/port."
        )
    elif dev.status == "active":
        cause = "NetBox has this device as active. Check when it was last confirmed (see below)."
    else:
        cause = "No path to a firewall is recorded, so upstream cannot be checked."

    owners, owner_from = effective(contacts, dev, site.id, "owner", inherit)
    approvers, _ = effective(contacts, dev, site.id, "approver", inherit)
    escalation, _ = effective(contacts, dev, site.id, "escalation", inherit)
    lost = [devices[i] for i in blast_radius(adj, roots, dev.id)] if roots and dev.id in adj else []
    affected_owners: list[Person] = []
    for other in lost:
        for person in effective(contacts, other, site.id, "owner", inherit)[0]:
            if person not in affected_owners:
                affected_owners.append(person)

    values = {
        "name": dev.name,
        "model": dev.model,
        "serial": dev.serial or "?",
        "ip": dev.ip or "no IP recorded",
        "status": dev.status,
        "site": site.name,
        "platform": dev.platform or "its operating system",
        "owner": names(owners),
        "window": site.window or "none recorded (negotiate each time)",
        "criticality": dev.criticality or site.criticality or "unknown",
        "application": dev.application or "none recorded",
        "upstream": upstream.name if upstream else "its switch (cabling not recorded in NetBox)",
        "upstream_port": upstream_port if upstream else "(port unknown)",
        "upstream_status": upstream.status if upstream else "unknown",
        "siblings_note": (
            f"{len(siblings_down)} of {len(siblings)} other devices on it are down"
            if siblings
            else "No other devices are recorded on it"
        ),
        "blast_count": len(lost),
        "blast_summary": _tally(lost),
        "affected_owners": names(affected_owners),
    }
    assert set(values) == PLACEHOLDERS, "runbook values drifted from PLACEHOLDERS"
    steps = runbook_for(plan, dev.kind, vendor)

    return {
        "device": {
            "name": dev.name,
            "role": dev.role,
            "model": dev.model,
            "serial": dev.serial,
            "ip": dev.ip,
            "status": dev.status,
            "firmware": dev.firmware,
            "criticality": dev.criticality,
            "application": dev.application,
        },
        "site": {"name": site.name, "address": site.address, "time_zone": site.zone},
        "status_note": (
            "set by the Meraki sync from the dashboard"
            if dev.source == "meraki-api"
            else "recorded by hand or spreadsheet; not confirmed by the dashboard"
        ),
        "record_updated": dev.updated,
        "path": hops,
        "path_known": bool(path),
        "likely_cause": cause,
        "neighbours": [{"device": s.name, "status": s.status} for s in siblings],
        "people": {
            "owner": names(owners),
            "owner_from": owner_from,
            "approver": names(approvers),
            "escalation": names(escalation),
        },
        "change_window": site.window or "",
        "steps": [_fill(step, values) for step in steps],
    }


def render_triage(report: dict[str, Any]) -> None:
    dev, site = report["device"], report["site"]
    console.print(
        f"\n[bold]Level-1 card: {dev['name']}[/bold]  ({dev['role'] or 'device'}, {dev['model']})"
    )
    info = Table(show_header=False)
    info.add_column(style="bold")
    info.add_column()
    info.add_row("Status", f"{_status(dev['status'])}  [dim]{report['status_note']}[/dim]")
    info.add_row("NetBox record updated", report["record_updated"] or "unknown")
    info.add_row("Site", f"{site['name']}  {site['address']}  {site['time_zone']}".rstrip())
    info.add_row("Serial / IP", f"{dev['serial'] or '?'}  /  {dev['ip'] or 'no IP recorded'}")
    info.add_row("Firmware", dev["firmware"] or "unknown")
    info.add_row("Criticality", dev["criticality"] or "?")
    if dev["application"]:
        info.add_row("Application", dev["application"])
    console.print(info)

    if report["path_known"] and report["path"]:
        console.print("\n[bold]Where it plugs in[/bold]")
        for hop in report["path"]:
            console.print(
                f"  {hop['from']} [dim]{hop['from_port']}[/dim]  ->  {hop['to']} "
                f"[dim]{hop['to_port']}[/dim]  {_status(hop['to_status'])}"
            )
    elif report["path_known"]:
        console.print("\n[bold]It is the firewall[/bold]: nothing upstream is recorded.")
    else:
        console.print(
            "\n[yellow]No cabling path to a firewall is recorded for this device.[/yellow]"
        )
    console.print(f"\n[bold]Likely cause:[/bold] {report['likely_cause']}")
    if report["neighbours"]:
        down = sum(n["status"] != "active" for n in report["neighbours"])
        listing = ", ".join(f"{n['device']} ({n['status']})" for n in report["neighbours"])
        console.print(
            f"[bold]Others on the same switch:[/bold] {down} of {len(report['neighbours'])} down: {listing}"
        )

    people = report["people"]
    who = Table(title="Who to call", show_header=False)
    who.add_column(style="bold")
    who.add_column()
    who.add_row("Owner", f"{people['owner']}  [dim]({people['owner_from']})[/dim]")
    who.add_row("Approver", people["approver"])
    who.add_row("Escalation", people["escalation"])
    who.add_row("Change window", report["change_window"] or "none recorded")
    console.print(who)

    console.print("\n[bold]Steps[/bold]  [dim](example runbook; edit in the data file)[/dim]")
    for number, step in enumerate(report["steps"], 1):
        console.print(f"  {number}. {step}")


def _run(
    builder: Any, renderer: Any, nb: Any, plan: dict[str, Any], name: str, as_json: bool
) -> int:
    try:
        report = builder(nb, plan, name)
    except LookupError as exc:
        print(exc, file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(report, indent=2, default=str))
    else:
        renderer(report)
    return 0


def run_impact(nb: Any, plan: dict[str, Any], name: str, as_json: bool) -> int:
    return _run(build_impact, render_impact, nb, plan, name, as_json)


def run_triage(nb: Any, plan: dict[str, Any], name: str, as_json: bool) -> int:
    return _run(build_triage, render_triage, nb, plan, name, as_json)
