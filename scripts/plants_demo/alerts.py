"""Alerts in, tickets out: a monitoring alert, already enriched with what NetBox knows.

The workshop asked for this on the next agenda: "monitoring integration with
automation, if there's a way to trigger something". Today an alert says "AP went
down" and a person works out the rest. Here the alert arrives as a webhook body and,
before anyone looks at it, the ticket already says:

- what the device is, where, how critical, and which application it carries
- who owns it, who to escalate to, and what else lost connectivity (and their owners)
- whether a change touched it recently, whether this keeps happening, and what
  fixed it last time
- for an internet circuit: the provider, circuit id, account and phone number
- the level-1 steps for it

``alert simulate <device>``              the mock dashboard raises "device went down"
``alert simulate <mx> --type dhcp``      ...or "DHCP scope nearly exhausted"
``alert simulate <mx> --type uplink``    ...or "the internet circuit on WAN1 went down"
``alert poll [--simulate-down DEV]``     one monitoring pass: the Juniper/Ubiquiti gear
                                         (mock ping), circuit usage and DHCP scopes
``alert handle <file.json>``             handle a webhook body saved to a file
``alert resolve <id> --fix "..."``       close it, saying what fixed it
``alert show <id>`` / ``alert list``     one incident / all of them
``alert reset [--yes]``                  delete them, and bring simulated outages back

The body is shaped like a Meraki webhook (alertType, deviceSerial, networkId, ...)
but has not been checked against a real one. The "ticket" is a Markdown file plus a
NetBox journal entry; handing it to their ITSM (the team mentioned Zendesk) would be
one more HTTP call at the end of ``handle``. Past incidents from ``incident_history``
in the data file stand in for the ticket system's history.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from rich.table import Table

from . import cards, circuits, monitor
from .common import REPO_ROOT, console, journal
from .dashboard import Dashboard
from .l1 import scope_level

ALERTS_DIR = REPO_ROOT / "output" / "alerts"
CHANGES_DIR = REPO_ROOT / "output" / "changes"
PRIORITY = {"critical": "P1", "high": "P2", "medium": "P3", "low": "P4"}
DOWN_TYPE = {"wireless": "APs went down", "switch": "Switches went down", "appliance": "Appliances went down"}
DHCP_FULL = "DHCP scope nearly exhausted"
UPLINK_DOWN = "Uplink status changed"
UPLINK_FULL = "Uplink near capacity"
UNREACHABLE = "Device unreachable"
REPEAT_DAYS = 30
POLLER = "poller (mock)"


class AlertError(Exception):
    """Something the user can fix, explained plainly."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _when(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _stamp(when: datetime) -> str:
    return when.isoformat(timespec="seconds").replace("+00:00", "Z")


def alert_kind(alert_type: str) -> str:
    """dhcp | uplink-down | uplink-full | device"""
    text = (alert_type or "").lower()
    if text.startswith("dhcp"):
        return "dhcp"
    if text == UPLINK_DOWN.lower():
        return "uplink-down"
    if text == UPLINK_FULL.lower():
        return "uplink-full"
    return "device"


# ------------------------------------------------------------------- payloads
def webhook(world: dict[str, Any], device: dict[str, Any], alert_type: str, level: str, data: dict[str, Any]) -> dict[str, Any]:
    """A body shaped like a Meraki webhook, for ``device`` in the mock ``world``."""
    network = next((n for n in world["networks"] if n["id"] == device["networkId"]), {})
    stamp = _stamp(_now())
    return {
        "version": "0.1",
        "sentAt": stamp,
        "organizationId": world["org"]["id"],
        "organizationName": world["org"]["name"],
        "networkId": device["networkId"],
        "networkName": network.get("name", ""),
        "deviceSerial": device["serial"],
        "deviceMac": device["mac"],
        "deviceName": device["name"],
        "deviceModel": device["model"],
        "alertType": alert_type,
        "alertLevel": level,
        "occurredAt": stamp,
        "alertData": {**data, "simulated": True},
    }


def poller_body(record: Any, detail: str) -> dict[str, Any]:
    """The same shape, from the poller, for a device the dashboard cannot see."""
    stamp = _stamp(_now())
    return {
        "version": "0.1",
        "sentAt": stamp,
        "networkName": record.site.name if record.site else "",
        "deviceSerial": record.serial or "",
        "deviceName": record.name,
        "deviceModel": record.device_type.model,
        "alertType": UNREACHABLE,
        "alertLevel": "critical",
        "occurredAt": stamp,
        "alertData": {"source": POLLER, "check": detail, "simulated": True},
    }


def dhcp_body(dash: Dashboard, device: dict[str, Any], scope: dict[str, Any], source: str = "") -> dict[str, Any]:
    percent, _ = scope_level(scope["usedCount"], scope["freeCount"], 80, 95)
    data = {"vlan": scope["vlanId"], "subnet": scope["subnet"], "inUse": f"{percent}%", "free": scope["freeCount"]}
    if source:
        data["source"] = source
    return webhook(dash.world, device, DHCP_FULL, "warning", data)


# --------------------------------------------------------------- what we know
def recent_changes(device: str, site: str, since: datetime, changes_dir: Path = CHANGES_DIR) -> list[dict[str, str]]:
    """Changes applied since ``since`` that touched the device, sit in front of it, or its site."""
    found = []
    for path in sorted(changes_dir.glob("CHG-*.json")) if changes_dir.exists() else []:
        if path.stem.endswith("-evidence"):
            continue
        rec = json.loads(path.read_text(encoding="utf-8"))
        applied = _when((rec.get("applied") or {}).get("at", ""))
        if applied is None or applied < since:
            continue
        if device in {d["name"] for d in rec.get("scope", [])}:
            how = "changed this device"
        elif device in {d["name"] for d in rec.get("downstream", [])}:
            how = "changed what this device sits behind"
        elif rec.get("site", {}).get("slug") == site:
            how = "changed something at the same site"
        else:
            continue
        found.append(
            {
                "id": rec["id"],
                "summary": rec["summary"],
                "applied": applied.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "state": rec["state"],
                "how": how,
            }
        )
    return found


def seeded_history(plan: dict[str, Any], now: datetime) -> list[dict[str, str]]:
    """The sample ticket history from the data file, dated relative to ``now``."""
    return [
        {
            "id": item["id"],
            "opened": (now - timedelta(days=int(item["days_ago"]))).isoformat(timespec="seconds"),
            "device": item["device"],
            "alert": item["alert"],
            "fix": item.get("fix", ""),
            "by": item.get("by", ""),
            "circuit": item.get("circuit", ""),
            "state": "resolved",
            "source": "ticket history (sample)",
        }
        for item in plan.get("incident_history", [])
    ]


def all_history(plan: dict[str, Any], now: datetime, alerts_dir: Path = ALERTS_DIR) -> list[dict[str, str]]:
    """Every incident this tool raised plus the ticket history, newest first."""
    found = []
    for rec in load_incidents(alerts_dir):
        fixed = rec.get("resolution") or {}
        found.append(
            {
                "id": rec["id"],
                "opened": rec["opened"],
                "device": rec["device"]["name"],
                "alert": rec["alert"]["type"],
                "fix": fixed.get("fix", ""),
                "by": fixed.get("by", ""),
                "circuit": (rec.get("circuit") or {}).get("cid", ""),
                "state": rec.get("state", "open"),
                "source": "this tool",
            }
        )
    found += seeded_history(plan, now)
    return sorted(found, key=lambda h: _when(h["opened"]) or now, reverse=True)


def earlier_incidents(
    device: str,
    since: datetime,
    alerts_dir: Path = ALERTS_DIR,
    plan: dict[str, Any] | None = None,
    alert_type: str | None = None,
) -> list[dict[str, str]]:
    """Incidents on ``device`` (of ``alert_type``, if given) opened since ``since``, newest first."""
    return [
        h
        for h in all_history(plan or {}, _now(), alerts_dir)
        if h["device"] == device
        and (alert_type is None or h["alert"] == alert_type)
        and (_when(h["opened"]) or since) >= since
    ]


def known_fixes(
    device: str, alert_type: str, plan: dict[str, Any], alerts_dir: Path = ALERTS_DIR, limit: int = 3
) -> list[dict[str, str]]:
    """What fixed this problem on this device before, newest first, however long ago."""
    return [
        h for h in all_history(plan, _now(), alerts_dir)
        if h["device"] == device and h["alert"] == alert_type and h["fix"]
    ][:limit]


def circuit_incidents(cid: str, since: datetime, plan: dict[str, Any], alerts_dir: Path = ALERTS_DIR) -> list[dict[str, str]]:
    return [h for h in all_history(plan, _now(), alerts_dir) if h["circuit"] == cid and (_when(h["opened"]) or since) >= since]


def priority_of(criticality: str, affected: list[dict[str, Any]]) -> str:
    """The higher of the device's criticality and the most critical thing it took down."""
    levels = [criticality or "medium", *(a["criticality"] for a in affected)]
    worst = max(levels, key=lambda c: cards.CRITICALITY_RANK.get(c, 1))
    return PRIORITY.get(worst, "P3")


def _circuit_story(
    kind: str, data: dict[str, Any], circuit: dict[str, Any] | None, backup: dict[str, Any] | None,
    repeats: int, owner: str, site: str,
) -> tuple[str, list[str]]:
    """Likely cause and steps for an internet circuit alert."""
    port = (data.get("uplink") or "wan1").upper()
    if circuit is None:
        return (
            f"No circuit is recorded on {port} in NetBox, so nobody can say who to call.",
            [f"Find out who provides the circuit on {port} at {site} and record it in NetBox (Circuits).", f"Tell {owner}."],
        )
    who = f"{circuit['provider']} on {circuit['noc'] or '(no number recorded)'}"
    quote = f"account {circuit['account'] or '?'}, circuit {circuit['cid']}"
    if kind == "uplink-full":
        cause = f"{port} ({circuit['cid']}, {circuit['provider']}) is at {data.get('inUse', '?')} of its {circuit['commit_mbps']} Mbps."
        steps = [
            f"See who is using it: Meraki dashboard > Network-wide > Clients for {site}, sorted by usage.",
            f"If it stays this full at busy times, ask {circuit['provider']} ({quote}) to quote for more than {circuit['commit_mbps']} Mbps.",
            f"Tell {owner}: {site} may feel slow until then.",
        ]
        return cause, steps
    cause = f"{port} is down: circuit {circuit['cid']} from {circuit['provider']}."
    if repeats:
        times = "once" if repeats == 1 else f"{repeats} times"
        cause += f" It has already gone down {times} in the last {REPEAT_DAYS} days."
    if backup:
        cause += f" The site should now be running on its backup, {backup['cid']} ({backup['provider']}, {backup['commit_mbps']} Mbps)."
    else:
        cause += " There is no backup circuit, so the whole site is offline."
    steps = [f"Call {who}: {quote}. Their SLA: {circuit['sla'] or 'not recorded'}."]
    if backup:
        steps.append(
            f"In the Meraki dashboard (Security & SD-WAN > Uplink status), check {backup['port']} is carrying traffic. "
            f"It is {backup['commit_mbps']} Mbps instead of {circuit['commit_mbps']}, so expect it to be slow."
        )
    else:
        steps.append(f"Nothing at {site} reaches the internet until {circuit['provider']} restores it.")
    steps.append(f"Tell {owner} what is happening and when {circuit['provider']} expects it back.")
    if repeats >= 2:
        steps.append(
            f"That is {repeats + 1} outages in {REPEAT_DAYS} days: send {circuit['provider']} the dates "
            f"(this ticket's history) and ask for a root cause and the service credits the contract allows."
        )
    return cause, steps


def build_incident(nb: Any, plan: dict[str, Any], body: dict[str, Any], lookback_hours: int) -> dict[str, Any]:
    serial, name = body.get("deviceSerial") or "", body.get("deviceName") or ""
    matches = list(nb.dcim.devices.filter(serial=serial)) if serial else []
    record = matches[0] if len(matches) == 1 else None
    if record is None and name:
        try:
            record = cards.find_device(nb, name)
        except LookupError:
            record = None
    alert = {
        "type": body.get("alertType") or "unknown alert",
        "level": body.get("alertLevel") or "",
        "occurred": body.get("occurredAt") or "",
        "data": body.get("alertData") or {},
        "network": body.get("networkName") or "",
    }
    opened = _now()
    if record is None:
        return {
            "opened": opened.isoformat(timespec="seconds"),
            "state": "open",
            "alert": alert,
            "device": {"name": name or serial or "?", "serial": serial, "model": body.get("deviceModel") or ""},
            "in_netbox": False,
            "priority": "P3",
            "assign_to": "network team (nobody can be looked up: the device is not in NetBox)",
            "escalate_to": "",
            "notify": [],
            "affected": [],
            "changes": [],
            "earlier": [],
            "fixes": [],
            "circuit": None,
            "likely_cause": "",
            "steps": [f"Add {name or serial} to NetBox: the dashboard knows it, the CMDB does not (meraki-sync finds these)."],
            "site": {"name": alert["network"], "slug": ""},
        }

    triage = cards.build_triage(nb, plan, record.name)
    impact = cards.build_impact(nb, plan, record.name)
    dev = triage["device"]
    since = opened - timedelta(hours=lookback_hours)
    changes = recent_changes(dev["name"], record.site.slug, since)
    earlier = earlier_incidents(dev["name"], opened - timedelta(days=REPEAT_DAYS), plan=plan, alert_type=alert["type"])
    fixes = known_fixes(dev["name"], alert["type"], plan)
    kind = alert_kind(alert["type"])
    data = alert["data"]
    owner = triage["people"]["owner"]
    site_name = triage["site"]["name"]

    circuit = backup = None
    if kind in ("uplink-down", "uplink-full"):
        at_site = circuits.site_circuits(nb, record.site.slug)
        circuit = circuits.on_port(at_site, data.get("uplink") or "wan1")
        backup = next((c for c in at_site if c is not circuit and c["role"] == "backup"), None)
        if circuit is not None:
            circuit["incidents_30d"] = len(circuit_incidents(circuit["cid"], opened - timedelta(days=REPEAT_DAYS), plan))

    if kind in ("dhcp", "uplink-full") or (kind == "uplink-down" and backup):
        affected = []
    else:
        affected = impact["lost_connectivity"]
    notify = sorted({a["owner"] for a in affected if a["owner"] != "nobody recorded"})

    steps = list(triage["steps"])
    hops = triage["path"]
    if kind == "dhcp":
        cause = (
            f"VLAN {data.get('vlan', '?')} ({data.get('subnet', '?')}) is {data.get('inUse', '?')} in use, "
            f"{data.get('free', '?')} address(es) left."
        )
        steps = [
            f"plants-l1 dhcp {record.site.slug}: which scope, how full, and the runbook for a full one.",
            *steps[-1:],
        ]
    elif kind in ("uplink-down", "uplink-full"):
        repeats = circuit["incidents_30d"] if circuit else 0
        cause, steps = _circuit_story(kind, data, circuit, backup, repeats, owner, site_name)
    elif any(hop["to_status"] != "active" for hop in hops):
        cause = triage["likely_cause"]
    elif triage["path_known"] and not hops:
        cause = "It is the site's firewall: check its power and the internet circuit first."
    elif hops:
        cause = (
            f"Everything between it and the firewall is up in NetBox, so start with {dev['name']} itself "
            f"and its uplink ({hops[0]['to']} {hops[0]['to_port']})."
        )
    else:
        cause = "No cabling path is recorded, so upstream cannot be ruled out."
    if changes:
        first = changes[-1]
        cause = f"{first['id']} ({first['summary']}) {first['how']} at {first['applied']}: rule that out first. If not: " + cause[0].lower() + cause[1:]
    if fixes:
        last = fixes[0]
        cause += f" Last time ({last['id']}, {last['opened'][:10]}): {last['fix']}"

    if kind == "uplink-full":
        priority = "P3" if cards.CRITICALITY_RANK.get(dev["criticality"], 1) >= cards.CRITICALITY_RANK["high"] else "P4"
    else:
        priority = priority_of(dev["criticality"], affected)
    return {
        "opened": opened.isoformat(timespec="seconds"),
        "state": "open",
        "alert": alert,
        "device": {**dev, "id": record.id},
        "in_netbox": True,
        "site": {"name": site_name, "slug": record.site.slug, "address": triage["site"]["address"]},
        "priority": priority,
        "assign_to": owner,
        "escalate_to": triage["people"]["escalation"],
        "change_window": triage["change_window"],
        "notify": notify,
        "affected": affected,
        "changes": changes,
        "earlier": earlier,
        "fixes": fixes,
        "circuit": circuit,
        "backup_circuit": backup,
        "likely_cause": cause,
        "path": triage["path"],
        "steps": steps,
    }


# ------------------------------------------------------------------- storage
def load_incidents(alerts_dir: Path = ALERTS_DIR) -> list[dict[str, Any]]:
    paths = sorted(alerts_dir.glob("INC-*.json")) if alerts_dir.exists() else []
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


def load_incident(inc_id: str, alerts_dir: Path = ALERTS_DIR) -> dict[str, Any]:
    path = alerts_dir / f"{inc_id.upper()}.json"
    if not path.exists():
        raise AlertError(f"No incident {inc_id}. See: plants-alert list")
    return json.loads(path.read_text(encoding="utf-8"))


def new_id(when: datetime, alerts_dir: Path = ALERTS_DIR) -> str:
    stamp = when.strftime("%Y%m%d")
    taken = {p.stem for p in alerts_dir.glob(f"INC-{stamp}-*.json")} if alerts_dir.exists() else set()
    number = 1
    while f"INC-{stamp}-{number:03d}" in taken:
        number += 1
    return f"INC-{stamp}-{number:03d}"


def _source(inc: dict[str, Any]) -> str:
    return inc["alert"]["data"].get("source") or "Meraki"


def render_ticket(inc: dict[str, Any]) -> str:
    dev, alert = inc["device"], inc["alert"]
    state = "RESOLVED" if inc.get("state") == "resolved" else "open"
    lines = [
        f"# {inc['id']} [{inc['priority']}]: {alert['type']} - {dev['name']} ({state})",
        "",
        f"- **Opened:** {inc['opened']} (alert occurred {alert['occurred'] or '?'})",
        f"- **Device:** {dev['name']} ({dev.get('role') or 'device'}, {dev.get('model') or '?'}), serial {dev.get('serial') or '?'}",
        f"- **Site:** {inc['site']['name']}",
    ]
    if not inc["in_netbox"]:
        lines += ["", "**This device is not in NetBox.** Nothing else about it can be looked up.", ""]
    else:
        lines += [
            f"- **Criticality:** {dev['criticality'] or '?'}; application: {dev['application'] or 'none recorded'}",
            f"- **Assign to (owner):** {inc['assign_to']}",
            f"- **Escalate to:** {inc['escalate_to']}",
            f"- **Change window:** {inc['change_window'] or 'none recorded'}",
            "",
            f"## Likely cause\n{inc['likely_cause']}",
            "",
        ]
    fixed = inc.get("resolution")
    if fixed:
        lines += ["## Resolution", f"{fixed['at']} by {fixed['by']}: {fixed['fix']}", ""]
    circuit = inc.get("circuit")
    if circuit:
        lines += [
            "## Circuit",
            f"- {circuit['cid']}: {circuit['provider']}, {circuit['type']}, {circuit['commit_mbps']} Mbps, {circuit['lands_on']}",
            f"- Provider: call {circuit['noc'] or '?'}, portal {circuit['portal'] or '?'}, account {circuit['account'] or '?'}, SLA {circuit['sla'] or '?'}",
            f"- This circuit: {circuit.get('incidents_30d', 0)} earlier incident(s) in {REPEAT_DAYS} days",
        ]
        backup = inc.get("backup_circuit")
        if backup:
            lines.append(f"- Backup: {backup['cid']} ({backup['provider']}, {backup['commit_mbps']} Mbps, {backup['lands_on']})")
        lines.append("")
    if alert["data"]:
        shown = {k: v for k, v in alert["data"].items() if k not in ("simulated", "source")}
        if shown:
            lines += ["## Alert detail", *[f"- {k}: {v}" for k, v in shown.items()], ""]
    if inc.get("fixes"):
        lines += [
            "## What fixed it before",
            *[f"- {f['id']} ({f['opened'][:10]}{', ' + f['by'] if f['by'] else ''}): {f['fix']}" for f in inc["fixes"]],
            "",
        ]
    if inc["changes"]:
        lines += ["## Recent changes", *[f"- {c['id']} applied {c['applied']} ({c['state']}): {c['summary']}. It {c['how']}." for c in inc["changes"]], ""]
    if inc["earlier"]:
        lines += [
            f"## This keeps happening: {len(inc['earlier']) + 1} times in {REPEAT_DAYS} days",
            *[f"- {e['id']} {e['opened'][:16]}: {e['alert']}" for e in inc["earlier"]],
            "",
        ]
    if inc["affected"]:
        lines += [
            f"## Also affected: {len(inc['affected'])} device(s) behind it",
            "| Device | Criticality | Application | Owner |",
            "|---|---|---|---|",
            *[f"| {a['device']} | {a['criticality']} | {a['application'] or ''} | {a['owner']} |" for a in inc["affected"]],
            "",
        ]
        if inc["notify"]:
            lines += ["Tell: " + "; ".join(inc["notify"]), ""]
    lines += ["## Steps", *[f"{n}. {s}" for n, s in enumerate(inc["steps"], 1)], ""]
    lines.append(f"_Raised from a MOCK {_source(inc)} alert and NetBox. Journal entry added to the device._")
    return "\n".join(lines) + "\n"


def save_incident(inc: dict[str, Any], alerts_dir: Path = ALERTS_DIR) -> None:
    alerts_dir.mkdir(parents=True, exist_ok=True)
    (alerts_dir / f"{inc['id']}.json").write_text(json.dumps(inc, indent=2), encoding="utf-8")
    (alerts_dir / f"{inc['id']}.md").write_text(render_ticket(inc), encoding="utf-8")


def show(inc: dict[str, Any]) -> None:
    dev = inc["device"]
    colour = {"P1": "red", "P2": "red", "P3": "yellow"}.get(inc["priority"], "white")
    state = "  [green]RESOLVED[/green]" if inc.get("state") == "resolved" else ""
    console.print(
        f"\n[bold {colour}]{inc['id']} {inc['priority']}[/bold {colour}]  {inc['alert']['type']}: "
        f"[bold]{dev['name']}[/bold] at {inc['site']['name']}{state}"
    )
    table = Table(show_header=False)
    table.add_column(style="bold")
    table.add_column(overflow="fold")
    kind = alert_kind(inc["alert"]["type"])
    if inc["in_netbox"]:
        table.add_row("What it is", f"{dev.get('role') or 'device'} {dev.get('model')}, criticality {dev['criticality'] or '?'}")
        table.add_row("Application", dev["application"] or "none recorded")
        table.add_row("Assign to", inc["assign_to"])
        table.add_row("Escalate to", inc["escalate_to"])
        circuit = inc.get("circuit")
        if circuit:
            table.add_row(
                "Circuit",
                f"{circuit['cid']}, {circuit['provider']} ({circuit['commit_mbps']} Mbps). "
                f"Call {circuit['noc'] or '?'}, account {circuit['account'] or '?'}, SLA {circuit['sla'] or '?'}",
            )
        if kind in ("device", "uplink-down"):
            behind = f"{len(inc['affected'])} device(s) lost connectivity" if inc["affected"] else "nothing lost connectivity"
            table.add_row("Behind it", behind + (f"; tell {'; '.join(inc['notify'])}" if inc["notify"] else ""))
    else:
        table.add_row("NetBox", "[yellow]not recorded[/yellow]: no owner, no site, no runbook can be looked up")
    table.add_row("Recent changes", "; ".join(f"{c['id']} ({c['how']}, {c['applied']})" for c in inc["changes"]) or "none in the lookback")
    if inc["earlier"]:
        sample = sum(1 for e in inc["earlier"] if e.get("source", "").startswith("ticket history"))
        note = f", {sample} from the ticket history" if sample else ""
        history = f"[yellow]{len(inc['earlier']) + 1} times in {REPEAT_DAYS} days[/yellow] ({', '.join(e['id'] for e in inc['earlier'])}{note})"
    else:
        history = f"first time in {REPEAT_DAYS} days"
    table.add_row("History", history)
    if inc.get("fixes"):
        last = inc["fixes"][0]
        table.add_row("Last fix", f"[green]{last['id']}[/green] ({last['opened'][:10]}{', ' + last['by'] if last['by'] else ''}): {last['fix']}")
    if inc.get("resolution"):
        fixed = inc["resolution"]
        table.add_row("Resolved", f"{fixed['at']} by {fixed['by']}: {fixed['fix']}")
    console.print(table)
    if inc["likely_cause"]:
        console.print(f"[bold]Likely cause:[/bold] {inc['likely_cause']}")
    console.print("[bold]Steps[/bold]")
    for number, step in enumerate(inc["steps"], 1):
        console.print(f"  {number}. {step}")
    console.print(f"[dim]Ticket: output/alerts/{inc['id']}.md" + ("   Journal entry added to the device in NetBox." if inc["in_netbox"] else "") + "[/dim]")


def brief(inc: dict[str, Any]) -> str:
    """One line for a list of new tickets."""
    extra = []
    if inc["affected"]:
        extra.append(f"{len(inc['affected'])} behind it")
    if inc.get("circuit"):
        extra.append(f"{inc['circuit']['provider']} {inc['circuit']['cid']}")
    if inc["earlier"]:
        extra.append(f"{len(inc['earlier']) + 1}x in {REPEAT_DAYS} days")
    if inc.get("fixes"):
        extra.append(f"last fix: {inc['fixes'][0]['fix']}")
    tail = f" ({'; '.join(extra)})" if extra else ""
    return f"{inc['id']} {inc['priority']}  {inc['alert']['type']}: {inc['device']['name']} -> {inc['assign_to']}{tail}"


# --------------------------------------------------------------------- actions
def handle(
    nb: Any, plan: dict[str, Any], body: dict[str, Any], lookback_hours: int,
    extra: dict[str, Any] | None = None, quiet: bool = False,
) -> dict[str, Any]:
    inc = build_incident(nb, plan, body, lookback_hours)
    inc["id"] = new_id(_now())
    inc.update(extra or {})
    save_incident(inc)
    if inc["in_netbox"]:
        related = f" Recent change: {', '.join(c['id'] for c in inc['changes'])}." if inc["changes"] else ""
        repeat = f" Seen {len(inc['earlier']) + 1} times in {REPEAT_DAYS} days." if inc["earlier"] else ""
        journal(
            nb,
            "dcim.device",
            inc["device"]["id"],
            f"[{inc['id']}] {inc['alert']['type']} ({inc['priority']}). Assigned to {inc['assign_to']}.{related}{repeat}",
            kind="warning",
        )
    if not quiet:
        show(inc)
    return inc


def do_simulate(nb: Any, plan: dict[str, Any], args: Any) -> int:
    with Dashboard(plan) as dash:
        wanted = args.device.lower()
        device = next((d for d in dash.world["devices"] if d["name"].lower() == wanted), None)
        if device is None:
            raise AlertError(f"The mock dashboard has no device named '{args.device}'.")
        restore = ""
        if args.type == "down":
            before = next(a["status"] for a in dash.world["availabilities"] if a["serial"] == device["serial"])
            if before != "offline":
                dash.inject_status(device["name"], "offline")
                restore = before
            body = webhook(dash.world, device, DOWN_TYPE.get(device["productType"], "Device went down"), "critical", {})
        elif device["productType"] != "appliance":
            raise AlertError(f"{device['name']} is not an MX; {args.type} alerts come from the site's MX.")
        elif args.type == "uplink":
            body = webhook(dash.world, device, UPLINK_DOWN, "critical", {"uplink": "wan1", "status": "failed"})
        else:
            assert dash.client is not None
            scopes = dash.client.dhcp_subnets(device["serial"])
            if not scopes:
                raise AlertError(f"{device['name']} serves no DHCP scope (the site relays to a server).")
            worst = max(scopes, key=lambda s: s["usedCount"] / max(1, s["usedCount"] + s["freeCount"]))
            body = dhcp_body(dash, device, worst)
    ALERTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = ALERTS_DIR / f"webhook-{_now():%Y%m%d%H%M%S}.json"
    payload.write_text(json.dumps(body, indent=2), encoding="utf-8")
    console.print(f"[yellow]Simulated[/yellow] {body['alertType']} on {device['name']} (MOCK dashboard). Webhook body: output/alerts/{payload.name}")
    handle(nb, plan, body, args.since_hours, {"restore_status": {device["name"]: restore} if restore else {}})
    return 0


def do_handle(nb: Any, plan: dict[str, Any], args: Any) -> int:
    path = Path(args.file)
    if not path.exists():
        raise AlertError(f"No file {path}.")
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise AlertError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(body, dict) or not (body.get("deviceSerial") or body.get("deviceName")):
        raise AlertError("The body names no device (deviceSerial or deviceName).")
    handle(nb, plan, body, args.since_hours)
    return 0


def _key(inc_or_body: dict[str, Any]) -> tuple[str, str, str]:
    """What makes two alerts the same problem: device, alert type, and which VLAN or uplink."""
    if "alert" in inc_or_body:
        name, alert_type, data = inc_or_body["device"]["name"], inc_or_body["alert"]["type"], inc_or_body["alert"]["data"]
    else:
        name, alert_type, data = inc_or_body["deviceName"], inc_or_body["alertType"], inc_or_body.get("alertData") or {}
    return name, alert_type, str(data.get("vlan") or data.get("uplink") or "")


def do_poll(nb: Any, plan: dict[str, Any], args: Any) -> int:
    """One pass of the monitoring the team does not have today, ending in tickets."""
    tag = plan["addon"]["tag"]
    watched = monitor.targets(nb, tag)
    names = {r.name.lower(): r.name for r in watched}
    for wanted, down in [*((n, True) for n in args.simulate_down or []), *((n, False) for n in args.simulate_up or [])]:
        if wanted.lower() not in names:
            raise AlertError(f"'{wanted}' is not Juniper or Ubiquiti gear the poller watches: {', '.join(sorted(names.values()))}")
        monitor.mark(names[wanted.lower()], down)
    down = monitor.load_down(plan)
    threshold = int(args.uplink_pct or (plan.get("monitor_mock") or {}).get("uplink_alert_pct", 80))

    open_now = {_key(i): i for i in load_incidents() if i.get("state", "open") == "open"}
    new: list[dict[str, Any]] = []
    still_open: list[dict[str, Any]] = []
    answering_again: list[dict[str, Any]] = []
    gaps: list[str] = []
    tally = {"reach": [0, 0], "circuit": [0, 0], "dhcp": [0, 0]}  # checked, problems

    def raise_or_skip(body: dict[str, Any], extra: dict[str, Any] | None = None) -> None:
        key = _key(body)
        if key in open_now:
            still_open.append(open_now[key])
            return
        inc = handle(nb, plan, body, args.since_hours, extra, quiet=True)
        open_now[key] = inc
        new.append(inc)

    for record in watched:
        result = monitor.probe(record, down)
        if result["status"] == "no-ip":
            gaps.append(f"{record.name}: {result['detail']}, so it cannot be polled")
            continue
        tally["reach"][0] += 1
        if result["status"] == "down":
            tally["reach"][1] += 1
            raise_or_skip(poller_body(record, result["detail"]), {"probe_down": record.name})
        elif (record.name, UNREACHABLE, "") in open_now:
            answering_again.append(open_now[(record.name, UNREACHABLE, "")])

    with Dashboard(plan) as dash:
        for site in plan["sites"]:
            mx, scopes = dash.dhcp_scopes(site["code"])
            if mx is None:
                continue
            usage = dash.uplink_mbps(site["code"])
            for circuit in circuits.site_circuits(nb, site["code"]):
                if circuit["role"] != "primary" or not circuit["commit_mbps"]:
                    continue
                mbps = usage.get(circuit["port"].lower())
                if mbps is None:
                    gaps.append(f"{circuit['cid']}: the dashboard reports no traffic on {mx['name']} {circuit['port']}")
                    continue
                tally["circuit"][0] += 1
                percent = round(100 * mbps / circuit["commit_mbps"])
                if percent >= threshold:
                    tally["circuit"][1] += 1
                    data = {
                        "uplink": circuit["port"].lower(),
                        "inUse": f"{percent}%",
                        "mbps": mbps,
                        "commitMbps": circuit["commit_mbps"],
                        "source": f"{POLLER}, from the dashboard's uplink usage",
                    }
                    raise_or_skip(webhook(dash.world, mx, UPLINK_FULL, "warning", data))
            for scope in scopes:
                tally["dhcp"][0] += 1
                _, level = scope_level(scope["usedCount"], scope["freeCount"], 80, 95)
                if level == "full":
                    tally["dhcp"][1] += 1
                    raise_or_skip(dhcp_body(dash, mx, scope, f"{POLLER}, from the dashboard's DHCP scopes"))

    table = Table(title="Monitoring pass (MOCK checks; tickets are real files and journal entries)")
    for column in ("Check", "Checked", "Problems"):
        table.add_column(column)
    table.add_row("Juniper / Ubiquiti gear answers (ping)", str(tally["reach"][0]), str(tally["reach"][1]))
    table.add_row(f"Internet circuits under {threshold}% of commit", str(tally["circuit"][0]), str(tally["circuit"][1]))
    table.add_row("DHCP scopes not full", str(tally["dhcp"][0]), str(tally["dhcp"][1]))
    console.print(table)
    if new:
        console.print(f"[bold]{len(new)} new ticket(s)[/bold]")
        for inc in new:
            console.print(f"  {brief(inc)}")
        console.print("[dim]  Full card: plants-alert show <id>[/dim]")
    for inc in still_open:
        console.print(f"  [dim]still open, no new ticket: {inc['id']} {inc['alert']['type']} on {inc['device']['name']}[/dim]")
    for inc in answering_again:
        console.print(
            f"  [green]{inc['device']['name']} answers again.[/green] {inc['id']} stays open until someone says what fixed it: "
            f'plants-alert resolve {inc["id"]} --fix "..."'
        )
    for gap in gaps:
        console.print(f"  [yellow]![/yellow] {gap}")
    if not (new or still_open):
        console.print("[green]Nothing needs a ticket.[/green]")
    return 0


def do_resolve(nb: Any, plan: dict[str, Any], args: Any) -> int:
    inc = load_incident(args.id)
    if inc.get("state") == "resolved":
        raise AlertError(f"{inc['id']} is already resolved: {inc['resolution']['fix']}")
    fix = (args.fix or "").strip()
    if not fix:
        raise AlertError("Say what fixed it (--fix): that is what the next person to get this alert will read.")
    by = args.by or "the service desk"
    inc["state"] = "resolved"
    inc["resolution"] = {"fix": fix, "by": by, "at": _now().strftime("%Y-%m-%d %H:%M UTC")}
    restore = inc.pop("restore_status", None) or {}
    if restore:
        with Dashboard(plan) as dash:
            for name, status in restore.items():
                dash.inject_status(name, status)
    probed = inc.pop("probe_down", None)
    if probed:
        monitor.mark(probed, False)
    save_incident(inc)
    if inc["in_netbox"]:
        journal(nb, "dcim.device", inc["device"]["id"], f"[{inc['id']}] Resolved by {by}: {fix}", kind="success")
    back = [*restore, *([probed] if probed else [])]
    console.print(f"[green]{inc['id']} resolved.[/green] The next alert on {inc['device']['name']} will quote this fix.")
    if back:
        console.print(f"[dim]Simulated outage over: {', '.join(dict.fromkeys(back))} back up.[/dim]")
    return 0


def do_show(nb: Any, plan: dict[str, Any], args: Any) -> int:  # noqa: ARG001
    show(load_incident(args.id))
    return 0


def do_list(nb: Any, plan: dict[str, Any], args: Any) -> int:  # noqa: ARG001
    incidents = sorted(load_incidents(), key=lambda i: i["opened"], reverse=True)
    if not incidents:
        console.print("No incidents yet. Try: alert simulate us-plt2-ap02")
        return 0
    table = Table(title=f"{len(incidents)} incident(s)")
    for column in ("Id", "Priority", "Alert", "Device", "State", "Assigned to", "Related change"):
        table.add_column(column, overflow="fold")
    for inc in incidents:
        table.add_row(
            inc["id"],
            inc["priority"],
            inc["alert"]["type"],
            inc["device"]["name"],
            "resolved" if inc.get("state") == "resolved" else "[yellow]open[/yellow]",
            inc["assign_to"],
            ", ".join(c["id"] for c in inc["changes"]),
        )
    console.print(table)
    return 0


def do_reset(nb: Any, plan: dict[str, Any], args: Any) -> int:  # noqa: ARG001
    incidents = load_incidents()
    restore: dict[str, str] = {}
    for inc in incidents:
        restore.update(inc.get("restore_status") or {})
    marked = monitor.STATE_FILE.exists()
    console.print(
        f"{len(incidents)} incident(s) to delete, {len(restore)} simulated outage(s) to undo"
        + (", and the poller's simulated outages." if marked else ".")
    )
    if not args.yes:
        console.print("[dim]Dry run. Add --yes to do it. NetBox journal entries are kept.[/dim]")
        return 0
    if restore:
        with Dashboard(plan) as dash:
            for name, status in restore.items():
                dash.inject_status(name, status)
    monitor.STATE_FILE.unlink(missing_ok=True)
    for path in ALERTS_DIR.glob("*") if ALERTS_DIR.exists() else []:
        if path.name.startswith(("INC-", "webhook-")):
            path.unlink()
    console.print("[green]Reset.[/green]")
    return 0


ACTIONS = {
    "simulate": do_simulate,
    "handle": do_handle,
    "poll": do_poll,
    "resolve": do_resolve,
    "show": do_show,
    "list": do_list,
    "reset": do_reset,
}


def run(nb: Any, plan: dict[str, Any], args: Any) -> int:
    try:
        return ACTIONS[args.action](nb, plan, args)
    except (AlertError, LookupError, ValueError) as exc:
        print(f"alert: {exc}", file=sys.stderr)
        return 1
