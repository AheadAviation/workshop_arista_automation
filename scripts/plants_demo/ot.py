"""OT segmentation readiness: what has to be true before firewalling the plant floor.

The team's next project is "going into the OT space ... segment in some OT
firewalling". Before a firewall goes between the plant floor and everything else,
four questions need answers, per plant:

1. What is on the OT network, and is anything there that nobody knows about?
2. Is all the OT equipment actually on the OT network? (Anything left on the
   corporate VLAN stays exposed, or gets cut off from its line.)
3. Who owns each machine, so someone can sign off the change and test it?
4. What does each machine need to talk to outside the OT network? Those are the
   firewall rules to write; NetBox's ``depends_on`` field records them.

Old operating systems are counted too: a Windows XP HMI cannot be patched, so the
firewall is the only protection it will get.

The unknown devices come from the MOCK network scan (see ``scan.py``).
"""

from __future__ import annotations

import ipaddress
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from rich.table import Table

from . import cards, scan
from .common import REPO_ROOT, console

REPORTS_DIR = REPO_ROOT / "output" / "reports"
OT_TAG = "ot-network"


def _eol_passed(value: str | None, today: date) -> bool:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date() < today  # noqa: DTZ007
    except (TypeError, ValueError):
        return False


def _ip(record: Any) -> str:
    address = getattr(record.primary_ip4, "address", None)
    return str(ipaddress.ip_interface(address).ip) if address else ""


def assess_site(nb: Any, plan: dict[str, Any], code: str, contacts: Any, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    site = cards._Site(nb)(code)
    inherit = set(plan["audit"]["site_inherit_roles"])
    nets = scan.site_networks(nb, code)
    ot_nets = [n for n in nets if n["role"] == "ot"]
    site_owner = contacts.get(("dcim.site", site.id), {}).get("owner", [])

    def where(ip: str) -> dict[str, Any] | None:
        return scan.network_of(ip, nets) if ip else None

    cache: dict[int, Any] = {}

    def device(dev_id: int) -> Any:
        if dev_id not in cache:
            cache[dev_id] = next(iter(nb.dcim.devices.filter(id=dev_id, exclude="config_context")), None)
        return cache[dev_id]

    machines, flows, retire_conflicts = [], [], []
    for record in nb.dcim.devices.filter(site=code, tag=OT_TAG, exclude="config_context"):
        dev = cards._dev(record)
        ip = _ip(record)
        net = where(ip)
        owners, _ = cards.effective(contacts, dev, site.id, "owner", inherit)
        cf = record.custom_fields or {}
        machine = {
            "device": dev.name,
            "application": dev.application,
            "criticality": dev.criticality,
            "ip": ip,
            "vlan": f"{net['vlan']} {net['vlan_name']}" if net else "",
            "on_ot": bool(net and net["role"] == "ot"),
            "platform": dev.platform,
            "unsupported_os": _eol_passed(cf.get("os_eol_date"), today),
            "owner": cards.names(owners) if owners else "",
        }
        machines.append(machine)
        for ref in cf.get("depends_on") or []:
            other = device(ref["id"] if isinstance(ref, dict) else int(ref))
            if other is None:
                continue
            other_ip = _ip(other)
            other_net = scan.network_of(other_ip, nets) if other_ip and other.site.slug == code else None
            if other_net and other_net["role"] == "ot":
                continue  # stays inside the OT network: nothing to open
            other_owners, _ = cards.effective(contacts, cards._dev(other), other.site.id, "owner", inherit)
            flows.append(
                {
                    "from": dev.name,
                    "from_ip": ip or "?",
                    "to": other.name,
                    "to_ip": other_ip or "?",
                    "to_where": (f"VLAN {other_net['vlan']} {other_net['vlan_name']}" if other_net else f"site {other.site.slug}"),
                    "to_application": (other.custom_fields or {}).get("business_application") or "",
                    "ask": ", ".join(p.name for p in other_owners) or "nobody recorded",
                    "ask_label": cards.names(other_owners),
                    "cross_site": other.site.slug != code,
                }
            )
            if "decommission-candidate" in {t.slug for t in other.tags}:
                retire_conflicts.append({"device": other.name, "needed_by": dev.name})

    found = scan.scan_site(nb, plan, code)
    unknown_ot = [u for u in found["unknown"] + found["unidentified"] if u["role"] == "ot"]

    wrong_vlan = [m for m in machines if m["ip"] and not m["on_ot"]]
    no_ip = [m for m in machines if not m["ip"]]
    no_owner = [m for m in machines if not m["owner"]]
    blockers = len(wrong_vlan) + len(no_ip) + len(no_owner) + len(unknown_ot) + (0 if ot_nets else 1)
    talk_to = sorted(
        {m["owner"] for m in machines if m["owner"]}
        | {f["ask_label"] for f in flows if f["ask"] != "nobody recorded"}
        | {p.label() for p in site_owner}
    )
    return {
        "site": code,
        "name": site.name,
        "ot_networks": [str(n["network"]) for n in ot_nets],
        "machines": sorted(machines, key=lambda m: m["device"]),
        "unknown": unknown_ot,
        "wrong_vlan": wrong_vlan,
        "no_ip": no_ip,
        "no_owner": no_owner,
        "unsupported_os": [m for m in machines if m["unsupported_os"]],
        "flows": flows,
        "retire_conflicts": retire_conflicts,
        "blockers": blockers,
        "ready": blockers == 0,
        "talk_to": talk_to,
    }


def collect(nb: Any, plan: dict[str, Any], site: str | None = None) -> list[dict[str, Any]]:
    plants = [s["code"] for s in plan["sites"] if s["kind"] == "plant"]
    if site and site not in plants:
        raise LookupError(f"'{site}' is not a plant. Plants: {', '.join(plants)}")
    contacts = cards.load_contacts(nb)
    return [assess_site(nb, plan, code, contacts) for code in ([site] if site else plants)]


def _summary_row(r: dict[str, Any]) -> list[str]:
    return [
        r["site"],
        str(len(r["machines"])),
        str(len(r["unknown"])),
        str(len(r["wrong_vlan"]) + len(r["no_ip"])),
        str(len(r["no_owner"])),
        str(len(r["unsupported_os"])),
        str(len(r["flows"])),
        "ready" if r["ready"] else f"{r['blockers']} to fix",
    ]


SUMMARY_COLUMNS = ["Plant", "OT gear", "Unknown", "Not on OT", "No owner", "Old OS", "Rules", "Readiness"]


def render(results: list[dict[str, Any]]) -> None:
    table = Table(title="OT segmentation readiness", header_style="bold underline", box=None, pad_edge=False)
    for column in SUMMARY_COLUMNS:
        table.add_column(column, no_wrap=True, justify="left" if column in ("Plant", "Readiness") else "right")
    for r in results:
        row = _summary_row(r)
        row[-1] = "[green]ready[/green]" if r["ready"] else f"[yellow]{row[-1]}[/yellow]"
        table.add_row(*row)
    console.print(table)
    for r in results:
        if r["ready"] and not r["flows"]:
            continue
        console.print(f"\n[bold]{r['name']}[/bold] ({r['site']}), OT network {', '.join(r['ot_networks']) or '[red]none recorded[/red]'}")
        for u in r["unknown"]:
            console.print(f"  [red]Unknown on OT:[/red] {u['ip']} {u.get('vendor') or '?'} (ports {', '.join(map(str, u.get('ports', []))) or '?'}): find out what it is and who owns it")
        for m in r["wrong_vlan"]:
            console.print(f"  [yellow]Wrong VLAN:[/yellow] {m['device']} ({m['application'] or 'no application'}) is on {m['vlan']}: move it to OT first")
        for m in r["no_ip"]:
            console.print(f"  [yellow]No IP in NetBox:[/yellow] {m['device']} ({m['application'] or 'no application'}): find out where it is plugged in")
        for m in r["no_owner"]:
            console.print(f"  [yellow]No owner:[/yellow] {m['device']}: nobody can sign off its change or test it afterwards")
        for c in r["retire_conflicts"]:
            console.print(f"  [yellow]Do not retire[/yellow] {c['device']}: {c['needed_by']} depends on it")
        if r["unsupported_os"]:
            console.print(f"  {len(r['unsupported_os'])} machine(s) on an unsupported OS ({', '.join(sorted({m['platform'] for m in r['unsupported_os']}))}): the firewall is their only protection")
        if r["flows"]:
            table = Table(title="Flows the OT firewall must allow (draft rules)", title_style="bold", header_style="bold")
            for column in ("From", "To", "Ports: ask"):
                table.add_column(column)
            for f in r["flows"]:
                table.add_row(f"{f['from']}\n{f['from_ip']}", f"{f['to']}\n{f['to_ip']}, {f['to_where']}", f["ask"])
            console.print(table)
        console.print(f"  Talk to: {'; '.join(r['talk_to']) or 'nobody recorded'}")


def to_markdown(results: list[dict[str, Any]], today: date) -> str:
    lines = [f"# OT segmentation readiness, {today.isoformat()}", "", "| " + " | ".join(SUMMARY_COLUMNS) + " |", "|" + "---|" * len(SUMMARY_COLUMNS)]
    lines += ["| " + " | ".join(_summary_row(r)) + " |" for r in results]
    for r in results:
        lines += ["", f"## {r['name']} ({r['site']})", f"OT network: {', '.join(r['ot_networks']) or 'none recorded'}", ""]
        lines += [f"- **Unknown on OT:** {u['ip']} {u.get('vendor') or '?'}" for u in r["unknown"]]
        lines += [f"- **Wrong VLAN:** {m['device']} is on {m['vlan']}" for m in r["wrong_vlan"]]
        lines += [f"- **No IP in NetBox:** {m['device']}" for m in r["no_ip"]]
        lines += [f"- **No owner:** {m['device']}" for m in r["no_owner"]]
        lines += [f"- **Do not retire** {c['device']}: {c['needed_by']} depends on it" for c in r["retire_conflicts"]]
        if r["unsupported_os"]:
            lines.append(f"- {len(r['unsupported_os'])} machine(s) on an unsupported OS")
        if r["flows"]:
            lines += ["", "| From | To | Where | Application | Ports: ask |", "|---|---|---|---|---|"]
            lines += [
                f"| {f['from']} {f['from_ip']} | {f['to']} {f['to_ip']} | {f['to_where']} | {f['to_application'] or '?'} | {f['ask']} |"
                for f in r["flows"]
            ]
        lines += ["", f"Talk to: {'; '.join(r['talk_to']) or 'nobody recorded'}"]
    lines += ["", "_From NetBox plus a MOCK network scan. Ports are not recorded anywhere yet: the people listed have to say._"]
    return "\n".join(lines) + "\n"


def run(nb: Any, plan: dict[str, Any], site: str | None, reports_dir: Path = REPORTS_DIR) -> int:
    try:
        results = collect(nb, plan, site)
    except (LookupError, ValueError) as exc:
        print(f"ot: {exc}", file=sys.stderr)
        return 1
    render(results)
    today = date.today()
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"ot-readiness-{today.isoformat()}.md"
    path.write_text(to_markdown(results, today), encoding="utf-8")
    console.print(f"\n[dim]Written: output/reports/{path.name}[/dim]")
    return 0
