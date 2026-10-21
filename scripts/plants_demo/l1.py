"""Level-1 / level-2 actions: the runbook items that should not need the network team.

The workshop named them: someone installs a time clock and needs a switch port in the
right VLAN; someone needs an IP address reserved; someone asks "what is this address?".
Today each of those lands on a senior engineer. Here each is one command with the
guardrails written down, and what each person may do is NetBox's own permission model:

``l1 find <ip|device>``                       where is it, what does it plug into, who owns it
``l1 reserve-ip <site> <vlan> --for ...``      reserve the next free address in the static range
``l1 port-vlan <switch> <port> <vlan>``        move an access port to another VLAN (L2)
``l1 dhcp <site>``                             how full each DHCP scope is, and what to do

``--as demo.helpdesk`` runs a command as that demo user, so the *same* command is
allowed for ``demo.netops`` and refused for ``demo.helpdesk`` by NetBox itself, not by
this script. Port changes go to the MOCK Meraki dashboard (see ``dashboard.py``).
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pynetbox
import requests
from rich.table import Table

from . import cards
from .common import REPO_ROOT, console, journal
from .dashboard import Dashboard
from .meraki_client import MerakiError
from .rbac import DEFAULT_PASSWORD

# Static reservations live in .100-.199 of each VLAN: clear of the gateway (.1) and of the
# addresses the sample gives firewalls, switches, access points and servers (.2-.99).
STATIC_RANGE = (100, 199)
INFRA_ROLES = {"firewall", "access-switch", "wireless-ap", "spine", "leaf", "border"}


LOG_FILE = REPO_ROOT / "output" / "changes" / "l1_log.json"


class L1Error(Exception):
    """Something the user can fix or escalate, explained plainly."""


def _log(entry: dict[str, Any]) -> None:
    """Remember what an action created or changed, so ``l1 reset`` can put it back."""
    entries = json.loads(LOG_FILE.read_text(encoding="utf-8")) if LOG_FILE.exists() else []
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text(json.dumps([*entries, entry], indent=2), encoding="utf-8")


def undo_all(nb: Any, apply: bool) -> int:
    """Delete the addresses ``reserve-ip`` reserved and restore the ports ``port-vlan`` changed.

    NetBox journal entries are kept: they are the record of what happened.
    """
    entries = json.loads(LOG_FILE.read_text(encoding="utf-8")) if LOG_FILE.exists() else []
    ips = [e for e in entries if e["kind"] == "ip"]
    ports = [e for e in entries if e["kind"] == "port"]
    console.print(f"{len(ips)} reserved address(es) to delete, {len(ports)} port(s) to restore.")
    if not apply:
        console.print("[dim]Dry run. Add --yes to do it.[/dim]")
        return 0
    for entry in reversed(entries):
        if entry["kind"] == "ip":
            found = nb.ipam.ip_addresses.get(entry["id"])
            if found is not None:
                found.delete()
        else:
            found = nb.dcim.interfaces.get(entry["interface_id"])
            if found is not None:
                found.update(
                    {
                        "mode": entry["mode"] or "",
                        "untagged_vlan": entry["untagged_vlan"],
                        "description": entry["description"],
                    }
                )
    LOG_FILE.unlink(missing_ok=True)
    console.print("[green]Level-1 changes undone.[/green]")
    return 0


@contextmanager
def acting_as(nb: Any, username: str | None) -> Iterator[Any]:
    """A NetBox connection that has only ``username``'s permissions (None = as the admin token).

    Signs in with the demo password to get a short-lived API token, and deletes it again.
    """
    if not username:
        yield nb
        return
    url = nb.base_url.removesuffix("/api")
    password = os.environ.get("DEMO_USER_PASSWORD") or DEFAULT_PASSWORD
    try:
        reply = requests.post(
            f"{url}/api/users/tokens/provision/",
            json={"username": username, "password": password},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise L1Error(f"Cannot reach NetBox to sign in as {username}: {exc}") from exc
    if reply.status_code >= 400:
        raise L1Error(
            f"Could not sign in as {username} (HTTP {reply.status_code}). "
            "Run 'plants' to create the demo users, or set DEMO_USER_PASSWORD."
        )
    data = reply.json()
    actor = pynetbox.api(url, token=data["key"])
    actor.http_session.verify = False
    try:
        yield actor
    finally:
        found = nb.users.tokens.get(data["id"])
        if found is not None:
            found.delete()


def _forbidden(exc: pynetbox.RequestError, who: str | None, what: str) -> L1Error:
    if getattr(exc.req, "status_code", None) == 403:
        return L1Error(
            f"{who or 'This user'} is not allowed to {what}. "
            "That is NetBox's permission model, not this script: escalate to level 2."
        )
    return L1Error(f"NetBox refused: {exc.error}")


# ------------------------------------------------------------------------ find
def do_find(nb: Any, plan: dict[str, Any], args: Any) -> int:
    what = args.what
    try:
        address = str(ipaddress.ip_address(what))
    except ValueError:
        address = ""

    device_name, port = what, ""
    if address:
        found = list(nb.ipam.ip_addresses.filter(address=address))
        assigned = getattr(found[0], "assigned_object", None) if found else None
        owner_device = getattr(assigned, "device", None)
        if not found:
            prefixes = list(nb.ipam.prefixes.filter(contains=address))
            where = (
                f"inside {prefixes[-1].prefix} ({prefixes[-1].description})"
                if prefixes
                else "outside every prefix NetBox knows"
            )
            console.print(
                f"[yellow]{address} is not recorded in NetBox[/yellow], {where}. "
                "If something answers on it, it is missing from the CMDB."
            )
            return 1
        if owner_device is None:
            record = found[0]
            console.print(
                f"{address} is recorded as [bold]{record.status.value}[/bold]"
                + (f": {record.description}" if record.description else "")
                + ". No device is attached to it."
            )
            return 0
        device_name, port = owner_device.name, assigned.name

    try:
        report = cards.build_triage(nb, plan, device_name)
    except LookupError as exc:
        raise L1Error(str(exc)) from exc
    dev, site, people = report["device"], report["site"], report["people"]
    console.print(f"\n[bold]{dev['name']}[/bold]  ({dev['role']}, {dev['model']}) at {site['name']}")
    if address:
        console.print(f"  {address} is on its {port} interface")
    console.print(f"  Status {dev['status']}   serial {dev['serial'] or '?'}   criticality {dev['criticality'] or '?'}")
    for hop in report["path"]:
        console.print(f"  plugs into {hop['to']} {hop['to_port']} (from its {hop['from_port']}), {hop['to_status']}")
    console.print(f"  Owner: {people['owner']}   Approver: {people['approver']}")
    console.print(f"  Change window: {report['change_window'] or 'none recorded'}")
    console.print(f"[dim]Steps if it is down: plants-triage {dev['name']}[/dim]")
    return 0


# ------------------------------------------------------------------ reserve-ip
def next_static(network: ipaddress.IPv4Network, used: set[str]) -> str | None:
    """The first free address in the static range of a /24-style prefix."""
    low, high = STATIC_RANGE
    for host in network.hosts():
        if low <= int(host) % 256 <= high and str(host) not in used:
            return str(host)
    return None


def do_reserve_ip(nb: Any, plan: dict[str, Any], args: Any) -> int:
    with acting_as(nb, args.as_user) as actor:
        site = actor.dcim.sites.get(slug=args.site)
        if site is None:
            raise L1Error(f"No site '{args.site}'. Try: {', '.join(sorted(s.slug for s in actor.dcim.sites.all())[:8])} ...")
        vlan = actor.ipam.vlans.get(site_id=site.id, vid=args.vlan)
        if vlan is None:
            have = sorted(v.vid for v in actor.ipam.vlans.filter(site_id=site.id))
            raise L1Error(f"{site.name} has no VLAN {args.vlan}. It has: {', '.join(map(str, have))}.")
        prefix = next(iter(actor.ipam.prefixes.filter(vlan_id=vlan.id)), None)
        if prefix is None:
            raise L1Error(f"VLAN {args.vlan} at {site.name} has no prefix recorded.")
        network = ipaddress.ip_network(str(prefix.prefix))
        used = {str(ipaddress.ip_interface(ip.address).ip) for ip in actor.ipam.ip_addresses.filter(parent=str(prefix.prefix))}
        choice = next_static(network, used)
        if choice is None:
            raise L1Error(f"{prefix.prefix} has no free address in .{STATIC_RANGE[0]}-.{STATIC_RANGE[1]}.")
        if args.dry_run:
            console.print(f"Would reserve {choice}/{network.prefixlen} on VLAN {args.vlan} ({vlan.name}) at {site.name} for: {args.label}")
            return 0
        tag = actor.extras.tags.get(slug=plan["addon"]["tag"])
        data: dict[str, Any] = {
            "address": f"{choice}/{network.prefixlen}",
            "status": "reserved",
            "description": args.label,
            "tags": [tag.id] if tag else [],
        }
        if prefix.tenant:
            data["tenant"] = prefix.tenant.id
        try:
            created = actor.ipam.ip_addresses.create(data)
        except pynetbox.RequestError as exc:
            raise _forbidden(exc, args.as_user, "reserve IP addresses") from exc
        _log({"kind": "ip", "id": created.id, "address": created.address})
        journal(actor, "ipam.ipaddress", created.id, f"Reserved for: {args.label} (VLAN {args.vlan}, {site.name}).")
    who = f" as {args.as_user}" if args.as_user else ""
    console.print(
        f"[green]Reserved[/green] {created.address}{who}: VLAN {args.vlan} ({vlan.name}) at {site.name}, for \"{args.label}\"."
    )
    console.print(f"[dim]/ipam/ip-addresses/{created.id}/  (status reserved, in the static range .{STATIC_RANGE[0]}-.{STATIC_RANGE[1]})[/dim]")
    return 0


# ------------------------------------------------------------------- port-vlan
def port_number(name: str) -> str:
    """Meraki's portId for a NetBox interface name ('Port 12' -> '12')."""
    match = re.fullmatch(r"(?:port\s*)?(\d+)", name.strip(), re.IGNORECASE)
    if not match:
        raise L1Error(f"'{name}' is not an access port (use a number such as 12, or 'Port 12').")
    return match.group(1)


def do_port_vlan(nb: Any, plan: dict[str, Any], args: Any) -> int:
    with acting_as(nb, args.as_user) as actor:
        record = cards.find_device(actor, args.switch)
        dev = cards._dev(record)
        if dev.role != "access-switch":
            raise L1Error(f"{dev.name} is a {dev.role or 'device'}, not an access switch.")
        if not any(tag.slug == "meraki" for tag in record.tags):
            vendor = record.device_type.manufacturer.name if record.device_type.manufacturer else "this vendor"
            raise L1Error(
                f"{dev.name} is a {vendor} switch, not in the Meraki dashboard. NetBox could "
                "record the VLAN, but nothing pushes it to Juniper or Ubiquiti switches yet: "
                "that is still a manual change."
            )
        port_id = port_number(args.port)
        iface = actor.dcim.interfaces.get(device_id=record.id, name=f"Port {port_id}")
        if iface is None:
            raise L1Error(f"{dev.name} has no 'Port {port_id}' in NetBox.")
        vlan = actor.ipam.vlans.get(site_id=record.site.id, vid=args.vlan)
        if vlan is None:
            have = sorted(v.vid for v in actor.ipam.vlans.filter(site_id=record.site.id))
            raise L1Error(f"{record.site.name} has no VLAN {args.vlan}. It has: {', '.join(map(str, have))}.")

        peers = [p.device for p in (iface.link_peers or []) if getattr(p, "device", None)]
        infra = [p.name for p in peers if p.role and p.role.slug in INFRA_ROLES]
        if infra:
            raise L1Error(
                f"Port {port_id} connects to network equipment ({', '.join(infra)}). "
                "Changing its VLAN could cut off everything behind it: this needs the network team."
            )

        with Dashboard(plan) as dash:
            assert dash.client is not None
            target = dash.snapshot().get(dev.name)
            if target is None:
                raise L1Error(
                    f"{dev.name} is not in the Meraki dashboard. NetBox could record the VLAN, but "
                    "nothing pushes it to Juniper or Ubiquiti switches yet."
                )
            try:
                before = dash.client.switch_port(target["serial"], port_id)
            except MerakiError as exc:
                raise L1Error(str(exc)) from exc
            if args.dry_run:
                console.print(f"Would move {dev.name} port {port_id} from VLAN {before['vlan']} to {args.vlan} ({vlan.name}).")
                return 0

            was_mode = iface.mode.value if iface.mode else None
            was_vlan = iface.untagged_vlan.id if iface.untagged_vlan else None
            was_description = iface.description or ""
            change = {"mode": "access", "untagged_vlan": vlan.id}
            if args.label:
                change["description"] = args.label
            try:  # NetBox first: it is the source of truth, and it decides who may do this
                iface.update(change)
            except pynetbox.RequestError as exc:
                raise _forbidden(exc, args.as_user, "change switch ports") from exc
            restore = {
                "mode": was_mode or "",
                "untagged_vlan": was_vlan,
                "description": was_description,
            }
            try:
                fields: dict[str, Any] = {"type": "access", "vlan": args.vlan}
                if args.label:
                    fields["name"] = args.label
                dash.client.update_switch_port(target["serial"], port_id, **fields)
                after = dash.client.switch_port(target["serial"], port_id)
            except MerakiError as exc:
                iface.update(restore)
                raise L1Error(f"The dashboard refused the change, so NetBox was put back: {exc}") from exc
            _log({"kind": "port", "interface_id": iface.id, "device": dev.name, "port": port_id, **restore})

        who = args.as_user or "admin"
        text = f"Port {port_id} VLAN {before['vlan']} -> {after['vlan']} ({vlan.name}) by {who}" + (f": {args.label}" if args.label else "")
        journal(actor, "dcim.device", record.id, text)

    table = Table(title=f"{dev.name} port {port_id}", show_header=True)
    for column in ("", "NetBox", "Dashboard (mock)"):
        table.add_column(column)
    table.add_row("VLAN before", str(was_vlan or "none recorded"), str(before["vlan"]))
    table.add_row("VLAN after", f"{vlan.vid} ({vlan.name})", str(after["vlan"]))
    console.print(table)
    if peers:
        console.print(f"[yellow]![/yellow] {', '.join(p.name for p in peers)} is recorded on this port: tell its owner.")
    console.print(f"[green]Done[/green] as {who}. Journal entry added to {dev.name}.")
    return 0


# ------------------------------------------------------------------------ dhcp
def scope_level(used: int, free: int, warn: int, full: int) -> tuple[int, str]:
    """Percent in use, and 'ok' / 'filling' / 'full' against the two thresholds."""
    total = used + free
    percent = round(100 * used / total) if total else 100
    return percent, "full" if percent >= full else ("filling" if percent >= warn else "ok")


def do_dhcp(nb: Any, plan: dict[str, Any], args: Any) -> int:
    with acting_as(nb, args.as_user) as actor:
        site = actor.dcim.sites.get(slug=args.site)
        if site is None:
            raise L1Error(f"No site '{args.site}'. Try: {', '.join(sorted(s.slug for s in actor.dcim.sites.all())[:8])} ...")
        names = {v.vid: v.name for v in actor.ipam.vlans.filter(site_id=site.id)}
        with Dashboard(plan) as dash:
            mx, scopes = dash.dhcp_scopes(site.slug)
        if mx is None:
            raise L1Error(f"{site.name} has no Meraki MX in the dashboard, so its DHCP scopes are not visible here.")
        card = cards.build_triage(actor, plan, mx["name"])
        if not scopes:
            servers = [
                d
                for d in actor.dcim.devices.filter(site_id=site.id, exclude="config_context")
                if "dhcp" in ((d.custom_fields or {}).get("business_application") or "").lower()
            ]
            if not servers:
                console.print(f"{mx['name']} serves no DHCP scope at {site.name}, and NetBox records no DHCP server there.")
                return 1
            server = cards.build_triage(actor, plan, servers[0].name)
            console.print(
                f"{mx['name']} does not hand out addresses at {site.name}: NetBox records "
                f"[bold]{servers[0].name}[/bold] ({server['device']['application']}) as the DHCP server.\n"
                f"  Owner: {server['people']['owner']}   Escalation: {server['people']['escalation']}\n"
                "  Check the scopes on that server; the Meraki dashboard cannot see them."
            )
            return 0

    table = Table(title=f"DHCP scopes on {mx['name']} at {site.name} (MOCK dashboard)")
    for column in ("VLAN", "Subnet", "In use", "Free", "Used", ""):
        table.add_column(column, justify="right" if column in ("In use", "Free", "Used") else "left")
    style = {"ok": "green", "filling": "yellow", "full": "red"}
    worst: list[tuple[dict[str, Any], int]] = []
    for scope in sorted(scopes, key=lambda s: s["vlanId"]):
        percent, level = scope_level(scope["usedCount"], scope["freeCount"], args.warn, args.full)
        if level == "full":
            worst.append((scope, percent))
        table.add_row(
            f"{scope['vlanId']} {names.get(scope['vlanId'], '')}".strip(),
            scope["subnet"],
            str(scope["usedCount"]),
            str(scope["freeCount"]),
            f"{percent}%",
            f"[{style[level]}]{level}[/{style[level]}]",
        )
    console.print(table)
    if not worst:
        console.print(f"[green]Every scope is below {args.full}%.[/green] Nothing to do.")
        return 0
    for scope, percent in worst:
        vid = scope["vlanId"]
        console.print(
            f"\n[bold red]VLAN {vid} ({names.get(vid, '?')}) is {percent}% full[/bold red]: "
            f"{scope['freeCount']} address(es) left. New devices on it will not get an address."
        )
        console.print(
            f"  1. Look for one device taking many addresses: dashboard Network-wide > Clients, VLAN {vid}.\n"
            f"  2. A device that needs an address now can get a static one: "
            f"plants-l1 reserve-ip {site.slug} {vid} --for \"...\"\n"
            "  3. This tool cannot clear leases. A shorter lease time or a wider scope is a change for "
            f"the network team: escalate to {card['people']['escalation']} with this output.\n"
            f"  Site owner: {card['people']['owner']}"
        )
    return 2


def do_reset(nb: Any, plan: dict[str, Any], args: Any) -> int:  # noqa: ARG001
    return undo_all(nb, args.yes)


ACTIONS = {
    "find": do_find,
    "reserve-ip": do_reserve_ip,
    "port-vlan": do_port_vlan,
    "dhcp": do_dhcp,
    "reset": do_reset,
}


def run(nb: Any, plan: dict[str, Any], args: Any) -> int:
    try:
        return ACTIONS[args.action](nb, plan, args)
    except (L1Error, LookupError) as exc:
        print(f"l1: {exc}", file=sys.stderr)
        return 1
