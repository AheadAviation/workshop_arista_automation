"""The weekly estate report: how the whole fleet is doing, on one page.

"I don't have a greater picture of like how the fleet is doing" (the workshop).
Everything below already exists in separate commands; this puts it side by side,
per site, and says what changed since the last report:

- devices, and how many the dashboard or the poller says are down
- devices with no owner, and machines on an operating system past end of life
- where the Meraki dashboard and NetBox disagree (drift), and devices NetBox lacks
- the fullest DHCP scope, and how full the primary internet circuit is
- incidents in the last 30 days, open ones, repeat offenders, and per provider

Written to ``output/reports/estate-<timestamp>.md`` with a JSON snapshot beside it
for the next week's comparison. The dashboard, the poller and the ticket history
are MOCKS; NetBox is real.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from rich.table import Table

from . import alerts, audit, circuits, monitor, scan
from .common import REPO_ROOT, console
from .dashboard import Dashboard
from .l1 import scope_level
from .meraki_sync import DRIFT_KINDS, MerakiSync

REPORTS_DIR = REPO_ROOT / "output" / "reports"
TOTAL_KEYS = ("devices", "down", "no_owner", "eol_os", "drift", "not_in_netbox", "incidents_30d", "open")
LABELS = {
    "devices": "Devices",
    "down": "Down",
    "no_owner": "No owner",
    "eol_os": "Old OS",
    "drift": "Dashboard drift",
    "not_in_netbox": "Not in NetBox",
    "incidents_30d": "Incidents (30d)",
    "open": "Open",
}


def collect(nb: Any, plan: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    tag = plan["addon"]["tag"]
    found = audit.collect(nb, tag, inherit_roles=set(plan["audit"]["site_inherit_roles"]))
    site_of = {d.name: d.site.slug for d in nb.dcim.devices.filter(tag=tag, exclude="config_context")}
    codes = [s["code"] for s in plan["sites"]]
    rows: dict[str, dict[str, Any]] = {
        code: {"site": code, **dict.fromkeys(TOTAL_KEYS, 0), "dhcp": "", "dhcp_pct": 0, "wan": "", "wan_pct": 0}
        for code in codes
    }
    names = {s["code"]: s["name"] for s in plan["sites"]}
    crit = {s["code"]: s["criticality"] for s in plan["sites"]}
    for item in found["sites"]:
        if item["site"] in rows:
            rows[item["site"]]["devices"] = item["devices"]
    for item in found["devices_without_owner"]:
        rows[item["site"]]["no_owner"] += 1
    for item in found["eol_os"]:
        rows[item["site"]]["eol_os"] += 1

    down_list: list[dict[str, str]] = []
    drift_kinds: Counter = Counter()
    hot: list[str] = []
    with Dashboard(plan) as dash:
        assert dash.client is not None
        sync = MerakiSync(nb, plan, dash.client, dash.org_id)
        sync.load()
        for finding in sync.outcome.findings:
            code = site_of.get(finding.subject)
            if code not in rows:
                continue
            if finding.kind == "device-offline":
                rows[code]["down"] += 1
                down_list.append({"device": finding.subject, "site": code, "why": f"dashboard: {finding.detail.replace('dashboard reports it ', '')}"})
            elif finding.kind == "device-alerting":
                hot.append(f"Alerting: {finding.subject} ({code}), the dashboard reports it alerting")
            elif finding.kind in DRIFT_KINDS:
                rows[code]["drift"] += 1
                drift_kinds[finding.kind] += 1
        for action in sync.outcome.actions:
            match = re.search(r" at (\S+)$", action.detail)
            if action.kind == "create-device" and match and match.group(1) in rows:
                rows[match.group(1)]["not_in_netbox"] += 1

        for code in codes:
            mx, scopes = dash.dhcp_scopes(code)
            if scopes:
                worst = max(scopes, key=lambda s: s["usedCount"] / max(1, s["usedCount"] + s["freeCount"]))
                percent, level = scope_level(worst["usedCount"], worst["freeCount"], 80, 95)
                rows[code]["dhcp"] = f"VLAN {worst['vlanId']} {percent}%"
                rows[code]["dhcp_pct"] = percent
                if level != "ok":
                    hot.append(f"DHCP: {code} VLAN {worst['vlanId']} is {percent}% used ({worst['freeCount']} left)")
            if mx is None:
                continue
            usage = dash.uplink_mbps(code)
            primary = next((c for c in circuits.site_circuits(nb, code) if c["role"] == "primary"), None)
            if primary and primary["commit_mbps"] and primary["port"].lower() in usage:
                percent = round(100 * usage[primary["port"].lower()] / primary["commit_mbps"])
                rows[code]["wan"] = f"{percent}% of {primary['commit_mbps']}"
                rows[code]["wan_pct"] = percent
                if percent >= int((plan.get("monitor_mock") or {}).get("uplink_alert_pct", 80)):
                    hot.append(f"Circuit: {code} {primary['cid']} ({primary['provider']}) at {percent}% of {primary['commit_mbps']} Mbps")

    for code in codes:
        rows[code]["not_in_netbox"] += len(scan.scan_site(nb, plan, code)["unknown"])

    marked_down = monitor.load_down(plan)
    for name in sorted(marked_down):
        code = site_of.get(name)
        if code in rows:
            rows[code]["down"] += 1
            down_list.append({"device": name, "site": code, "why": "poller: does not answer"})

    since = now - timedelta(days=alerts.REPEAT_DAYS)
    history = [h for h in alerts.all_history(plan, now) if (alerts._when(h["opened"]) or now) >= since]
    open_incidents = [i for i in alerts.load_incidents() if i.get("state", "open") == "open"]
    for h in history:
        code = site_of.get(h["device"])
        if code in rows:
            rows[code]["incidents_30d"] += 1
    for inc in open_incidents:
        code = inc["site"].get("slug")
        if code in rows:
            rows[code]["open"] += 1
    per_problem = Counter((h["device"], h["alert"]) for h in history)
    repeats = [
        {"device": d, "alert": a, "site": site_of.get(d, "?"), "count": n}
        for (d, a), n in per_problem.most_common()
        if n >= 2
    ]

    by_cid = circuits.all_circuits(nb, tag)
    providers: dict[str, dict[str, Any]] = defaultdict(lambda: {"circuits": 0, "sites": set(), "incidents_30d": 0})
    for item in by_cid.values():
        providers[item["provider"]]["circuits"] += 1
        providers[item["provider"]]["sites"].add(item["site"])
    for h in history:
        if h["circuit"] in by_cid:
            providers[by_cid[h["circuit"]]["provider"]]["incidents_30d"] += 1

    totals = {key: sum(r[key] for r in rows.values()) for key in TOTAL_KEYS}
    return {
        "generated": now.strftime("%Y-%m-%d %H:%M UTC"),
        "totals": totals,
        "sites": [{**rows[c], "name": names[c], "criticality": crit[c]} for c in codes],
        "down": down_list,
        "drift_kinds": dict(drift_kinds),
        "hot": hot,
        "open": [
            {"id": i["id"], "priority": i["priority"], "alert": i["alert"]["type"], "device": i["device"]["name"], "owner": i["assign_to"]}
            for i in sorted(open_incidents, key=lambda i: (i["priority"], i["opened"]))
        ],
        "repeats": repeats,
        "providers": [
            {"provider": p, "circuits": v["circuits"], "sites": len(v["sites"]), "incidents_30d": v["incidents_30d"]}
            for p, v in sorted(providers.items(), key=lambda kv: (-kv[1]["incidents_30d"], kv[0]))
        ],
        "eol_platforms": dict(Counter(item["platform"] for item in found["eol_os"]).most_common()),
        "unowned_important": [
            item["device"] for item in found["devices_without_owner"]
            if item["site"] in rows and (crit.get(item["site"]) in ("critical", "high"))
        ],
    }


def previous_snapshot(reports_dir: Path, current: Path | None = None) -> dict[str, Any] | None:
    paths = sorted(p for p in reports_dir.glob("estate-*.json") if p != current) if reports_dir.exists() else []
    return json.loads(paths[-1].read_text(encoding="utf-8")) if paths else None


def delta(now: int, before: int | None) -> str:
    if before is None or now == before:
        return ""
    return f" ({'+' if now > before else ''}{now - before})"


def attention(report: dict[str, Any]) -> list[str]:
    """What someone should do something about this week, most urgent first."""
    items = [f"Open {o['priority']}: {o['id']} {o['alert']} on {o['device']}, assigned to {o['owner']}" for o in report["open"]]
    items += [f"Repeat: {r['device']} ({r['site']}), {r['alert']}, {r['count']} times in 30 days" for r in report["repeats"]]
    items += report["hot"]
    items += [f"Down: {d['device']} ({d['site']}), {d['why']}" for d in report["down"]]
    if report["unowned_important"]:
        items.append(f"No owner at a critical or high site: {', '.join(report['unowned_important'])}")
    if report["eol_platforms"]:
        items.append("Old OS still running: " + ", ".join(f"{p} x{n}" for p, n in report["eol_platforms"].items()))
    return items


SITE_COLUMNS = ["Site", "Crit.", "Devices", "Down", "No owner", "Old OS", "Drift", "Not in NetBox", "Fullest DHCP", "Internet", "Incidents 30d", "Open"]


def _site_row(r: dict[str, Any]) -> list[str]:
    return [
        r["site"], r["criticality"], str(r["devices"]), str(r["down"]), str(r["no_owner"]), str(r["eol_os"]),
        str(r["drift"]), str(r["not_in_netbox"]), r["dhcp"] or "relay", r["wan"] or "?", str(r["incidents_30d"]), str(r["open"]),
    ]


def render(report: dict[str, Any], before: dict[str, Any] | None) -> None:
    was = (before or {}).get("totals", {})
    console.print(f"\n[bold]Estate report[/bold]  {report['generated']}" + (f"  [dim](compared with {before['generated']})[/dim]" if before else ""))
    console.print("  " + "   ".join(f"{LABELS[k]}: [bold]{v}[/bold]{delta(v, was.get(k))}" for k, v in report["totals"].items()))
    table = Table(header_style="bold underline", box=None, pad_edge=False)
    for column in ("Site", "Dev", "Down", "Unowned", "Old OS", "Drift", "Unknown", "DHCP", "WAN", "Inc", "Open"):
        table.add_column(column, no_wrap=True, justify="left" if column == "Site" else "right")
    for r in report["sites"]:
        dhcp = f"{r['dhcp_pct']}%" if r["dhcp"] else "relay"
        if r["dhcp_pct"] >= 95:
            dhcp = f"[red]{dhcp}[/red]"
        elif r["dhcp_pct"] >= 80:
            dhcp = f"[yellow]{dhcp}[/yellow]"
        wan = f"{r['wan_pct']}%" if r["wan"] else "?"
        if r["wan_pct"] >= 80:
            wan = f"[yellow]{wan}[/yellow]"
        table.add_row(
            r["site"], str(r["devices"]), str(r["down"]), str(r["no_owner"]), str(r["eol_os"]), str(r["drift"]),
            str(r["not_in_netbox"]), dhcp, wan, str(r["incidents_30d"]), str(r["open"]),
        )
    console.print(table)
    console.print(
        "[dim]  DHCP: fullest scope. WAN: primary circuit vs its commit rate. Unknown: on the dashboard or "
        "answering a scan, but not in NetBox. Inc: incidents in 30 days.[/dim]"
    )
    items = attention(report)
    console.print(f"[bold]Needs attention ({len(items)})[/bold]")
    for item in items:
        console.print(f"  - {item}")
    if report["providers"]:
        prov = Table(title="Internet providers (last 30 days)", header_style="bold")
        for column in ("Provider", "Circuits", "Sites", "Incidents"):
            prov.add_column(column)
        for p in report["providers"]:
            count = f"[yellow]{p['incidents_30d']}[/yellow]" if p["incidents_30d"] >= 2 else str(p["incidents_30d"])
            prov.add_row(p["provider"], str(p["circuits"]), str(p["sites"]), count)
        console.print(prov)


def to_markdown(report: dict[str, Any], before: dict[str, Any] | None) -> str:
    was = (before or {}).get("totals", {})
    lines = [
        f"# Estate report, {report['generated']}",
        "",
        (f"Compared with {before['generated']}." if before else "First report: nothing to compare with yet."),
        "",
        "| " + " | ".join(LABELS[k] for k in report["totals"]) + " |",
        "|" + "---|" * len(report["totals"]),
        "| " + " | ".join(f"{v}{delta(v, was.get(k))}" for k, v in report["totals"].items()) + " |",
        "",
        "## Needs attention",
        *[f"- {item}" for item in attention(report)],
        "",
        "## Sites",
        "| " + " | ".join(SITE_COLUMNS) + " |",
        "|" + "---|" * len(SITE_COLUMNS),
        *["| " + " | ".join(_site_row(r)) + " |" for r in report["sites"]],
        "",
    ]
    if report["providers"]:
        lines += [
            "## Internet providers (last 30 days)",
            "| Provider | Circuits | Sites | Incidents |",
            "|---|---|---|---|",
            *[f"| {p['provider']} | {p['circuits']} | {p['sites']} | {p['incidents_30d']} |" for p in report["providers"]],
            "",
        ]
    if report["drift_kinds"]:
        lines += ["Dashboard drift by kind: " + ", ".join(f"{k} {v}" for k, v in report["drift_kinds"].items()), ""]
    lines.append("_NetBox is real. The Meraki dashboard, the poller and the ticket history are MOCKS._")
    return "\n".join(lines) + "\n"


def run(nb: Any, plan: dict[str, Any], reports_dir: Path = REPORTS_DIR) -> int:
    try:
        report = collect(nb, plan)
    except (LookupError, ValueError) as exc:
        print(f"report: {exc}", file=sys.stderr)
        return 1
    before = previous_snapshot(reports_dir)
    render(report, before)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (reports_dir / f"estate-{stamp}.md").write_text(to_markdown(report, before), encoding="utf-8")
    (reports_dir / f"estate-{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    console.print(f"\n[dim]Written: output/reports/estate-{stamp}.md (and .json, for next week's comparison)[/dim]")
    return 0
