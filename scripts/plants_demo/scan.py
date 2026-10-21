"""Scan a site's networks and compare what answers with what NetBox records (MOCK scan).

Three answers come out of it:

- answers, but NetBox has never heard of it: the PLC a line integrator put in, the
  Raspberry Pi behind the scoreboard, the consumer router under a desk;
- NetBox records it, but it does not answer: retired and never written down, or broken;
- both agree.

``--apply`` records the unknown ones as IP addresses tagged ``scan-found`` (no
device: a person still has to say what each one is). Nothing else is changed.

The scan is a MOCK: it answers for every address NetBox records at the site except
the devices under ``scan_mock.silent`` in the data file, plus the hosts under
``scan_mock.extra``. A real one would be nmap (or the RMM's discovery) run from a
box at the site, with its results fed into the same comparison.
"""

from __future__ import annotations

import hashlib
import ipaddress
import sys
from datetime import date
from typing import Any

from rich.table import Table

from .common import Stats, console, upsert
from .populate import expand_site

SCAN_TAG = "scan-found"


def site_cfg(plan: dict[str, Any], code: str) -> dict[str, Any]:
    for site in plan["sites"]:
        if site["code"] == code:
            return site
    raise LookupError(f"No site '{code}' in the sample data: {', '.join(s['code'] for s in plan['sites'])}")


def _mac(text: str) -> str:
    digits = hashlib.sha1(text.encode()).hexdigest()[:6]  # noqa: S324 - not security related
    return "02:00:00:" + ":".join(digits[i : i + 2] for i in (0, 2, 4))


def check_config(plan: dict[str, Any]) -> None:
    """Fail loudly on a typo in ``scan_mock`` instead of silently scanning nothing."""
    cfg = plan.get("scan_mock") or {}
    names = {d.name for s in plan["sites"] for d in expand_site(plan, s)}
    unknown = sorted(set(cfg.get("silent", [])) - names)
    if unknown:
        raise ValueError(f"scan_mock.silent names devices not in the sample data: {unknown}")
    codes = {s["code"] for s in plan["sites"]}
    unknown = sorted(set(cfg.get("extra", {})) - codes)
    if unknown:
        raise ValueError(f"scan_mock.extra names sites not in the sample data: {unknown}")


def netbox_addresses(nb: Any, site: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every IP address NetBox records in the site's 10.<index>.0.0/16 block."""
    found = {}
    for record in nb.ipam.ip_addresses.filter(parent=f"10.{site['index']}.0.0/16"):
        obj = record.assigned_object
        device = getattr(getattr(obj, "device", None), "name", "") if obj else ""
        found[str(ipaddress.ip_interface(record.address).ip)] = {
            "id": record.id,
            "ip": str(ipaddress.ip_interface(record.address).ip),
            "device": device,
            "status": record.status.value if record.status else "",
            "tags": sorted(t.slug for t in record.tags),
            "description": record.description or "",
        }
    return found


def site_networks(nb: Any, code: str) -> list[dict[str, Any]]:
    """The site's prefixes with their VLAN and role, most specific first."""
    nets = []
    for prefix in nb.ipam.prefixes.filter(site=code):
        nets.append(
            {
                "network": ipaddress.ip_network(prefix.prefix),
                "vlan": prefix.vlan.vid if prefix.vlan else None,
                "vlan_name": prefix.vlan.name if prefix.vlan else "",
                "role": prefix.role.slug if prefix.role else "",
            }
        )
    return sorted(nets, key=lambda n: -n["network"].prefixlen)


def network_of(ip: str, nets: list[dict[str, Any]]) -> dict[str, Any] | None:
    address = ipaddress.ip_address(ip)
    return next((n for n in nets if address in n["network"]), None)


def mock_scan(plan: dict[str, Any], code: str, known: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """What a ping sweep plus a little port probing would find at the site."""
    cfg = plan.get("scan_mock") or {}
    silent = set(cfg.get("silent", []))
    answers = {
        ip: {"ip": ip, "mac": _mac(ip), "vendor": "", "hostname": item["device"], "ports": []}
        for ip, item in known.items()
        if item["device"] not in silent
    }
    for extra in cfg.get("extra", {}).get(code, []):
        answers[extra["ip"]] = {
            "ip": extra["ip"],
            "mac": extra.get("mac") or _mac(extra["ip"]),
            "vendor": extra.get("vendor", ""),
            "hostname": extra.get("hostname", ""),
            "ports": list(extra.get("ports", [])),
        }
    return sorted(answers.values(), key=lambda a: ipaddress.ip_address(a["ip"]))


def compare(
    answers: list[dict[str, Any]], known: dict[str, dict[str, Any]], nets: list[dict[str, Any]]
) -> dict[str, Any]:
    """Split into unknown / silent / recorded-but-unidentified / matched."""
    seen = {a["ip"] for a in answers}

    def where(ip: str) -> dict[str, Any]:
        net = network_of(ip, nets)
        return {
            "vlan": net["vlan"] if net else None,
            "vlan_name": net["vlan_name"] if net else "outside every recorded prefix",
            "role": net["role"] if net else "",
        }

    unknown = [{**a, **where(a["ip"])} for a in answers if a["ip"] not in known]
    unidentified = [
        {**known[a["ip"]], **a, **where(a["ip"])}
        for a in answers
        if a["ip"] in known and SCAN_TAG in known[a["ip"]]["tags"] and not known[a["ip"]]["device"]
    ]
    silent = [{**item, **where(ip)} for ip, item in sorted(known.items()) if ip not in seen]
    matched = sum(1 for a in answers if a["ip"] in known) - len(unidentified)
    return {"unknown": unknown, "unidentified": unidentified, "silent": silent, "matched": matched}


def scan_site(nb: Any, plan: dict[str, Any], code: str) -> dict[str, Any]:
    check_config(plan)
    site = site_cfg(plan, code)
    known = netbox_addresses(nb, site)
    nets = site_networks(nb, code)
    result = compare(mock_scan(plan, code, known), known, nets)
    return {"site": code, "name": site["name"], **result}


def record_unknown(nb: Any, plan: dict[str, Any], found: list[dict[str, Any]], when: date | None = None) -> Stats:
    """Write each unknown address into NetBox, tagged scan-found, with what the scan saw."""
    stats = Stats()
    tag_ids = [t.id for t in (nb.extras.tags.get(slug=plan["addon"]["tag"]), nb.extras.tags.get(slug=SCAN_TAG)) if t]
    stamp = (when or date.today()).isoformat()
    for item in found:
        seen = ", ".join(x for x in (item["vendor"], item["hostname"]) if x) or "unknown vendor"
        ports = f", ports {', '.join(map(str, item['ports']))}" if item["ports"] else ""
        upsert(
            nb.ipam.ip_addresses,
            {"address": f"{item['ip']}/24"},
            {
                "address": f"{item['ip']}/24",
                "status": "active",
                "dns_name": item["hostname"] or "",
                "description": f"Found by network scan {stamp}: {seen}, MAC {item['mac']}{ports}. Not identified yet."[:200],
                "tags": tag_ids,
            },
            stats,
            item["ip"],
        )
    return stats


def forget(nb: Any, plan: dict[str, Any], codes: list[str]) -> int:
    """Delete the addresses earlier scans recorded at these sites (undo ``--apply``)."""
    removed = 0
    for code in codes:
        site = site_cfg(plan, code)
        for record in nb.ipam.ip_addresses.filter(parent=f"10.{site['index']}.0.0/16", tag=SCAN_TAG):
            record.delete()
            removed += 1
    return removed


def render(result: dict[str, Any]) -> None:
    console.print(
        f"\n[bold]Network scan: {result['name']}[/bold] ({result['site']})  [dim]MOCK scan[/dim]\n"
        f"  {result['matched']} address(es) answer and match NetBox, "
        f"{len(result['unknown'])} answer but NetBox has never heard of them, "
        f"{len(result['silent'])} recorded but silent."
    )
    if result["unknown"]:
        table = Table(title="Answers, but not in NetBox", title_style="bold yellow")
        for column in ("IP", "VLAN", "Vendor (from MAC)", "Name", "Open ports"):
            table.add_column(column, no_wrap=column == "IP")
        for item in result["unknown"]:
            vlan = f"{item['vlan']} {item['vlan_name']}" if item["vlan"] else item["vlan_name"]
            style = "[bold red]" if item["role"] == "ot" else ""
            table.add_row(item["ip"], f"{style}{vlan}", item["vendor"] or "?", item["hostname"] or "", ", ".join(map(str, item["ports"])))
        console.print(table)
        if any(i["role"] == "ot" for i in result["unknown"]):
            console.print("  [red]Unknown devices on the OT network.[/red] Find out what they are before any OT firewalling: the segmentation would cut them off.")
    if result["unidentified"]:
        console.print(f"  [yellow]{len(result['unidentified'])} recorded by an earlier scan, still not identified:[/yellow] "
                      + ", ".join(i["ip"] for i in result["unidentified"]))
    if result["silent"]:
        table = Table(title="In NetBox, but does not answer", title_style="bold")
        for column in ("IP", "Device", "Status in NetBox", "VLAN"):
            table.add_column(column, no_wrap=column == "IP")
        for item in result["silent"]:
            table.add_row(item["ip"], item["device"] or "(no device)", item["status"], f"{item['vlan'] or ''} {item['vlan_name']}".strip())
        console.print(table)
        console.print("  Silent records: confirm with the owner, then retire them in NetBox (or fix them).")


def run(nb: Any, plan: dict[str, Any], site: str, apply: bool, forget_found: bool = False) -> int:
    codes = [s["code"] for s in plan["sites"]] if site == "all" else [site]
    try:
        if forget_found:
            console.print(f"Deleted {forget(nb, plan, codes)} address(es) tagged {SCAN_TAG}.")
            return 0
        results = [scan_site(nb, plan, code) for code in codes]
    except (LookupError, ValueError) as exc:
        print(f"scan: {exc}", file=sys.stderr)
        return 1
    if site == "all":
        table = Table(title="Network scan, every site (MOCK)")
        for column in ("Site", "Match NetBox", "Not in NetBox", "On OT VLAN", "Silent"):
            table.add_column(column)
        for r in results:
            ot = sum(1 for i in r["unknown"] if i["role"] == "ot")
            table.add_row(r["site"], str(r["matched"]), str(len(r["unknown"])), f"[red]{ot}[/red]" if ot else "0", str(len(r["silent"])))
        console.print(table)
        console.print("[dim]Detail for one site: plants-scan <site>[/dim]")
    else:
        render(results[0])
    unknown = [i for r in results for i in r["unknown"]]
    if apply and unknown:
        stats = record_unknown(nb, plan, unknown)
        console.print(f"[green]Recorded {stats.created} address(es) in NetBox, tagged {SCAN_TAG}.[/green] {stats.line()}")
    elif unknown:
        console.print("[dim]Nothing written. --apply records the unknown addresses in NetBox (tagged scan-found).[/dim]")
    return 0
