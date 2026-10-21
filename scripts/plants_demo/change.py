"""A change from request to evidence: plan -> approve -> apply -> verify.

The workshop team described a change process that is a ticket with a free-text box,
an approval that "filters up through me", and a check afterwards that is mostly
"wait for someone to scream". This is the same change with the missing pieces filled
in from what NetBox already knows:

``change plan <device>``     who owns it, who has to approve, what loses connectivity,
                             who to tell, whether the site's change window is open, and
                             a baseline of how the dashboard sees everything today.
``change approve <id>``      record an approval by someone who is actually an approver.
``change apply <id>``        refuse until approved and inside the window, then do it.
``change verify <id>``       run the same checks every time, compare with the baseline,
                             and write evidence that can be pasted into the ticket.
``change confirm <id>``      an owner checks the box for what the dashboard cannot see
                             (servers, plant machines); ``verify`` then counts it.

The one change type is a Meraki firmware upgrade, against the MOCK dashboard (see
``dashboard.py``). The mock upgrades every device of that product type in the
network at once, which is how a Meraki network upgrade behaves, so the plan scopes
the change that way.

NetBox's journal on the device gets a line for every step, and after a passing
verification the device's *Firmware Version* in NetBox is brought up to date, so the
CMDB stays true.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rich.table import Table

from . import cards
from .common import REPO_ROOT, console, journal
from .dashboard import STATE_FILE, Dashboard, reset_state
from .l1 import undo_all

CHANGES_DIR = REPO_ROOT / "output" / "changes"
PRODUCT_NOTE = {
    "appliance": "Internet and inter-site traffic stop while a firewall restarts "
    "(an HA pair fails over).",
    "switch": "Everything wired behind a switch drops while it restarts.",
    "wireless": "Wireless clients drop while an access point restarts.",
}


class ChangeError(Exception):
    """Something the user can fix, explained plainly."""


# ------------------------------------------------------ change windows (pure)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WINDOW = re.compile(
    r"^\s*(daily|mon|tue|wed|thu|fri|sat|sun)\w*\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Window:
    """A standing window: a weekday (None = every day), start and end in minutes."""

    day: int | None
    start: int
    end: int  # <= start means it runs past midnight into the next day

    @property
    def overnight(self) -> bool:
        return self.end <= self.start


def parse_window(text: str | None) -> Window | None:
    """Read 'Sun 01:00-05:00 ET' or 'Daily 20:00-23:00 CT'. The zone letters are ignored:
    the window is judged in the site's own time zone."""
    match = WINDOW.match(text or "")
    if not match:
        return None
    day, h1, m1, h2, m2 = match.groups()
    day = day.lower()[:3]
    return Window(None if day == "dai" else DAYS.index(day), int(h1) * 60 + int(m1), int(h2) * 60 + int(m2))


def in_window(window: Window, when: datetime) -> bool:
    minutes = when.hour * 60 + when.minute
    today = window.day is None or window.day == when.weekday()
    yesterday = window.day is None or window.day == (when.weekday() - 1) % 7
    if window.overnight:
        return (today and minutes >= window.start) or (yesterday and minutes < window.end)
    return today and window.start <= minutes < window.end


def next_window(window: Window, after: datetime) -> datetime:
    """The next time the window opens, strictly after ``after``."""
    for offset in range(0, 9):
        day = (after + timedelta(days=offset)).date()
        if window.day is not None and day.weekday() != window.day:
            continue
        opens = datetime.combine(day, time(window.start // 60, window.start % 60), after.tzinfo)
        if opens > after:
            return opens
    raise ValueError("a window that never opens")  # unreachable for a parsed window


def window_verdict(text: str | None, when: datetime) -> dict[str, Any]:
    """Is ``when`` (site-local) inside the standing window? With the next opening if not."""
    window = parse_window(text)
    if window is None:
        reason = "no standing window recorded" if not text else f"cannot read '{text}'"
        return {"verdict": "none", "detail": f"{reason}: negotiate each time", "next_start": ""}
    if in_window(window, when):
        return {"verdict": "inside", "detail": "inside the standing window", "next_start": ""}
    opens = next_window(window, when)
    return {
        "verdict": "outside",
        "detail": f"outside the standing window; it next opens {opens:%a %Y-%m-%d %H:%M}",
        "next_start": opens.isoformat(timespec="minutes"),
    }


def site_clock(zone: str, at: str | None) -> datetime:
    """'Now' at the site, or the site-local time the user typed (YYYY-MM-DD HH:MM)."""
    try:
        tz: Any = ZoneInfo(zone) if zone else timezone.utc
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    if not at:
        return datetime.now(tz)
    try:
        return datetime.strptime(at, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    except ValueError as exc:
        raise ChangeError(f"'{at}' is not a time; use YYYY-MM-DD HH:MM (site local time).") from exc


# ------------------------------------------------------------ the checks (pure)
def _check(check_id: str, title: str, status: str, detail: str) -> dict[str, str]:
    return {"id": check_id, "title": title, "status": status, "detail": detail}


def _still_up(name: str, before: str, now: str) -> tuple[str, str]:
    """Judge one device: a change must not take down something that was up."""
    if now == "online":
        return "pass", ""
    if before != "online":
        return "warn", f"{name} was already {before} before the change"
    return "fail", f"{name} was online before and is {now} now"


def evaluate(
    record: dict[str, Any],
    after: dict[str, dict[str, Any]],
    netbox_ip: dict[str, str],
    clients: dict[str, int] | None = None,
) -> list[dict[str, str]]:
    """The same checks every time. ``after`` is the dashboard now, by device name;
    ``clients`` is the wireless clients on each access point now."""
    target = record["to_version"]
    checks: list[dict[str, str]] = []

    wrong = [
        f"{d['name']} is on {after.get(d['name'], {}).get('firmware') or 'nothing'}"
        for d in record["scope"]
        if after.get(d["name"], {}).get("firmware") != target
    ]
    checks.append(
        _check(
            "firmware",
            f"Every device in scope is on {target}",
            "fail" if wrong else "pass",
            "; ".join(wrong) or f"{len(record['scope'])} device(s) report {target}",
        )
    )

    problems, notes = [], []
    for d in record["scope"]:
        status, text = _still_up(d["name"], d["status"], after.get(d["name"], {}).get("status", "gone"))
        (problems if status == "fail" else notes).extend([text] if text else [])
    checks.append(
        _check(
            "scope-online",
            "The devices that were changed are online",
            "fail" if problems else ("warn" if notes else "pass"),
            "; ".join(problems + notes) or f"{len(record['scope'])} of {len(record['scope'])} online",
        )
    )

    managed = [d for d in record["downstream"] if d["managed"]]
    if managed:
        problems, notes = [], []
        for d in managed:
            status, text = _still_up(
                d["name"], d["baseline_status"], after.get(d["name"], {}).get("status", "gone")
            )
            (problems if status == "fail" else notes).extend([text] if text else [])
        online = sum(after.get(d["name"], {}).get("status") == "online" for d in managed)
        checks.append(
            _check(
                "downstream-online",
                "Everything that depends on them is still online",
                "fail" if problems else ("warn" if notes else "pass"),
                "; ".join(problems + notes) or f"{online} of {len(managed)} online",
            )
        )

    drift = []
    for d in record["scope"]:
        seen = after.get(d["name"], {}).get("lan_ip", "")
        if d["name"] in netbox_ip and seen and seen != netbox_ip[d["name"]]:
            drift.append(f"{d['name']}: dashboard {seen}, NetBox {netbox_ip[d['name']]}")
    checks.append(
        _check(
            "ip-matches-netbox",
            "The dashboard and NetBox agree on the management IP",
            "warn" if drift else "pass",
            "; ".join(drift) or "no difference",
        )
    )

    baseline_clients = record.get("clients_baseline") or {}
    if clients is not None and baseline_clients:
        empty, thin = [], []
        for name, before in sorted(baseline_clients.items()):
            now = clients.get(name, 0)
            if before and not now:
                empty.append(f"{name} had {before} client(s), has none")
            elif before and now * 2 < before:
                thin.append(f"{name} {before} -> {now}")
        total_before, total_now = sum(baseline_clients.values()), sum(clients.get(n, 0) for n in baseline_clients)
        checks.append(
            _check(
                "clients-back",
                "Wireless clients are connecting again",
                "fail" if empty else ("warn" if thin else "pass"),
                "; ".join(empty + thin)
                or f"{total_now} client(s) on {len(baseline_clients)} access point(s), {total_before} before",
            )
        )

    unmanaged = [d for d in record["downstream"] if not d["managed"]]
    if unmanaged:
        confirmed_by = {c["name"]: c for c in record.get("confirmations", [])}
        done: dict[str, list[str]] = defaultdict(list)
        asks: dict[str, list[str]] = defaultdict(list)
        orphans: list[str] = []
        for d in unmanaged:
            owners = d.get("owner_names") or (
                [] if d["owner"] == "nobody recorded" else [part.split(" (")[0] for part in d["owner"].split(", ")]
            )
            who = next((name for name in owners if name in confirmed_by), None)
            if who:
                done[who].append(d["name"])
            elif owners:
                asks[d["owner"]].append(d["name"])
            else:
                orphans.append(d["name"])
        parts = [f"confirmed by {who}: {', '.join(names)}" for who, names in done.items()]
        parts += [f"ask {owner}: {', '.join(names)}" for owner, names in asks.items()]
        if orphans:
            parts.append(f"nobody owns {', '.join(orphans)}: record an owner in NetBox")
        checks.append(
            _check(
                "owner-confirms",
                "Owners confirm what the dashboard cannot see",
                "pending" if asks or orphans else "pass",
                "; ".join(parts),
            )
        )
    return checks


def verdict_of(checks: list[dict[str, str]]) -> str:
    return "FAIL" if any(c["status"] == "fail" for c in checks) else "PASS"


# --------------------------------------------------------------------- storage
def _path(change_id: str, suffix: str = ".json") -> Path:
    return CHANGES_DIR / f"{change_id.upper()}{suffix}"


def load_record(change_id: str) -> dict[str, Any]:
    path = _path(change_id)
    if not path.exists():
        raise ChangeError(f"No change '{change_id}'. Plan one first: change plan <device>.")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save_record(record: dict[str, Any]) -> None:
    CHANGES_DIR.mkdir(parents=True, exist_ok=True)
    with open(_path(record["id"]), "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)


def new_id(today: datetime) -> str:
    stamp = today.strftime("%Y%m%d")
    taken = {p.stem for p in CHANGES_DIR.glob(f"CHG-{stamp}-*.json")} if CHANGES_DIR.exists() else set()
    number = 1
    while f"CHG-{stamp}-{number:03d}" in taken:
        number += 1
    return f"CHG-{stamp}-{number:03d}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event(record: dict[str, Any], text: str) -> None:
    record["history"].append({"at": _now(), "event": text})


def _note(nb: Any, record: dict[str, Any], text: str) -> None:
    """One line on the device's journal in NetBox."""
    journal(nb, "dcim.device", record["device_id"], f"[{record['id']}] {text}")


# ------------------------------------------------------------------------ plan
def _people(people: list[cards.Person]) -> list[dict[str, str]]:
    return [{"name": p.name, "label": p.label()} for p in people]


def build_plan(
    nb: Any,
    plan: dict[str, Any],
    dash: Dashboard,
    name: str,
    to_version: str | None,
    summary: str,
    at: str | None,
    requested_by: str,
) -> dict[str, Any]:
    record = cards.find_device(nb, name)
    dev = cards._dev(record)
    snapshot = dash.snapshot()
    target = snapshot.get(dev.name)
    if target is None:
        raise ChangeError(
            f"{dev.name} is not in the Meraki dashboard. This demo changes Meraki firmware only; "
            "Juniper, Ubiquiti, servers and plant machines are not integrated yet."
        )
    product = target["product"]
    current = plan["meraki_mock"]["firmware"]["current"].get(product)
    version = to_version or current
    if not version:
        raise ChangeError(f"No version given and no current version known for {product}.")

    scope = sorted(
        (d for d in snapshot.values() if d["network_id"] == target["network_id"] and d["product"] == product),
        key=lambda d: d["name"],
    )
    if all(d["firmware"] == version for d in scope):
        raise ChangeError(f"Every {product} device in that network is already on {version}.")

    lookup_site = cards._Site(nb)
    site = lookup_site(dev.site)
    devices, adj = cards.load_site(nb, dev.site)
    by_name = {d.name: d for d in devices.values()}
    scope_ids = {by_name[d["name"]].id for d in scope if d["name"] in by_name}
    roots = {d.id for d in devices.values() if d.role == "firewall"}
    contacts = cards.load_contacts(nb)
    inherit = set(plan["audit"]["site_inherit_roles"])
    warnings: list[str] = []

    def who(dev_: cards.Dev, role: str) -> list[cards.Person]:
        return cards.effective(contacts, dev_, site.id, role, inherit)[0]

    lost: set[int] = set()
    if not roots:
        warnings.append("No firewall is recorded at this site, so what depends on it is unknown.")
    elif not scope_ids & set(adj):
        warnings.append("None of the devices in scope has cabling in NetBox, so what depends on it is unknown.")
    else:
        lost = cards.reachable(adj, roots) - cards.reachable(adj, roots, removed=scope_ids) - scope_ids
    if PRODUCT_NOTE.get(product):
        warnings.append(PRODUCT_NOTE[product])

    downstream = []
    for other in sorted((devices[i] for i in lost), key=lambda d: (-cards.CRITICALITY_RANK.get(d.criticality, 1), d.name)):
        owners = who(other, "owner")
        downstream.append(
            {
                "name": other.name,
                "role": other.role,
                "criticality": other.criticality or "?",
                "application": other.application,
                "managed": other.name in snapshot,
                "baseline_status": snapshot.get(other.name, {}).get("status", ""),
                "owner": cards.names(owners),
                "owner_names": [p.name for p in owners],
            }
        )

    approvers: dict[int, dict[str, Any]] = {}
    notify: dict[int, dict[str, Any]] = {}
    touched = [devices[i] for i in scope_ids] + [devices[i] for i in lost]
    for item in touched:
        for role, bucket in (("approver", approvers), ("owner", notify)):
            for person in who(item, role):
                entry = bucket.setdefault(person.id, {"person": person, "for": []})
                entry["for"].append(item.name)

    rollout = cards.rollout_order(nb, dev, lookup_site)
    for row in rollout:
        if row["canary"] and row["site"] != dev.site and f"{version} " not in row["firmware"] + " ":
            warnings.append(
                f"Canary site {row['name']} runs this model on {row['firmware']}, not {version}. "
                "Roll it there first."
            )
    for item in scope:
        if item["status"] != "online":
            warnings.append(f"{item['name']} is {item['status']} before the change starts.")

    when = site_clock(site.zone, at)
    verdict = window_verdict(site.window, when)
    created = datetime.now(timezone.utc)
    change_id = new_id(created)

    baseline = {
        d["name"]: {"status": d["status"], "firmware": d["firmware"], "lan_ip": d["lan_ip"]}
        for d in snapshot.values()
        if d["name"] in {s["name"] for s in scope} | {x["name"] for x in downstream}
    }
    plan_record = {
        "id": change_id,
        "type": "firmware",
        "summary": summary or f"Upgrade {product} firmware to {version} at {site.name}",
        "requested_by": requested_by,
        "created": created.isoformat(timespec="seconds"),
        "state": "planned",
        "target": dev.name,
        "device_id": record.id,
        "site": {"slug": site.slug, "name": site.name, "criticality": site.criticality, "zone": site.zone},
        "product": product,
        "network_id": target["network_id"],
        "to_version": version,
        "scope": [
            {k: d[k] for k in ("name", "serial", "model", "firmware", "status", "lan_ip")} for d in scope
        ],
        "window": {"text": site.window, "planned_for": when.isoformat(timespec="minutes"), **verdict},
        "approvals_required": [
            {"name": e["person"].name, "label": e["person"].label(), "for": e["for"]}
            for e in approvers.values()
        ],
        "approvals": [],
        "notify": [
            {"name": e["person"].name, "label": e["person"].label(), "email": e["person"].email, "for": e["for"]}
            for e in notify.values()
        ],
        "downstream": downstream,
        "warnings": warnings,
        "baseline": baseline,
        "clients_baseline": dash.client_counts(sorted(baseline)),
        "confirmations": [],
        "history": [],
    }
    _event(plan_record, f"planned by {requested_by}")
    return plan_record


# ---------------------------------------------------------------- rendering
def render_ticket(rec: dict[str, Any]) -> str:
    """The change request as text for the ticket (replaces the free-text box)."""
    lines = [
        f"# {rec['id']}: {rec['summary']}",
        "",
        f"- **Requested by:** {rec['requested_by']}",
        f"- **Site:** {rec['site']['name']} ({rec['site']['criticality'] or '?'} criticality)",
        f"- **Change:** {rec['product']} firmware to `{rec['to_version']}` on {len(rec['scope'])} device(s)",
        f"- **Window:** {rec['window']['text'] or 'none recorded'}. "
        f"Planned for {rec['window']['planned_for']}: {rec['window']['detail']}.",
        "",
        "## Devices in scope",
        "| Device | Model | Firmware now | Status now |",
        "|---|---|---|---|",
        *[f"| {d['name']} | {d['model']} | {d['firmware']} | {d['status']} |" for d in rec["scope"]],
        "",
        "## Approval needed from",
        *(
            [f"- {a['label']} (for {', '.join(a['for'])})" for a in rec["approvals_required"]]
            or ["- Nobody is recorded as approver: assign one in NetBox before this goes ahead."]
        ),
        "",
        f"## If it goes wrong: {len(rec['downstream'])} device(s) lose their path to the firewall",
        *(
            ["| Device | Criticality | Owner |", "|---|---|---|"]
            + [f"| {d['name']} | {d['criticality']} | {d['owner']} |" for d in rec["downstream"]]
            if rec["downstream"]
            else ["Nothing is recorded behind these devices."]
        ),
        "",
        "## Tell before starting",
        *[f"- {n['label']}: {', '.join(n['for'])}" for n in rec["notify"]],
        "",
    ]
    if rec["warnings"]:
        lines += ["## Heads-up", *[f"- {w}" for w in rec["warnings"]], ""]
    lines += [
        "## Checks after the change (run by `change verify`)",
        "- Every device in scope reports the new firmware and is online",
        "- Everything behind them is still online",
        "- Dashboard and NetBox agree on each management IP",
        "- Wireless clients are connecting again (compared with the baseline)",
        "- Owners confirm what the dashboard cannot see (servers, plant machines): `change confirm`",
    ]
    return "\n".join(lines) + "\n"


def render_evidence(
    rec: dict[str, Any],
    after: dict[str, dict[str, Any]],
    checks: list[dict[str, str]],
    verdict: str,
    updates: list[dict[str, str]],
) -> str:
    icon = {"pass": "PASS", "fail": "FAIL", "warn": "WARN", "pending": "ASK"}
    lines = [
        f"# Evidence for {rec['id']}: {verdict}",
        "",
        f"{rec['summary']}",
        "",
        "## Timeline",
        *[f"- {h['at']}  {h['event']}" for h in rec["history"]],
        "",
        "## Before and after",
        "| Device | Firmware before | Firmware after | Status before | Status after |",
        "|---|---|---|---|---|",
    ]
    for d in rec["scope"]:
        now = after.get(d["name"], {})
        lines.append(
            f"| {d['name']} | {d['firmware']} | {now.get('firmware', '?')} | {d['status']} | {now.get('status', '?')} |"
        )
    for d in rec["downstream"]:
        if d["managed"]:
            lines.append(
                f"| {d['name']} (behind) | | | {d['baseline_status']} | {after.get(d['name'], {}).get('status', '?')} |"
            )
    lines += ["", "## Checks", "| Result | Check | Detail |", "|---|---|---|"]
    lines += [f"| {icon[c['status']]} | {c['title']} | {c['detail']} |" for c in checks]
    if rec.get("confirmations"):
        lines += ["", "## Confirmed by owners"]
        lines += [
            f"- {c['at']}  {c['name']}: {', '.join(c['devices'])}" + (f" ({c['note']})" if c["note"] else "")
            for c in rec["confirmations"]
        ]
    if updates:
        lines += ["", "## NetBox updated", *[f"- {u['device']}: firmware {u['old'] or 'blank'} -> {u['new']}" for u in updates]]
    lines += [
        "",
        "_Source: the MOCK Meraki dashboard and NetBox. Run again with `change verify` to re-check._",
    ]
    return "\n".join(lines) + "\n"


STATUS_STYLE = {"pass": "green", "fail": "red", "warn": "yellow", "pending": "cyan"}


def render_plan(rec: dict[str, Any]) -> None:
    console.print(f"\n[bold]{rec['id']}[/bold]  {rec['summary']}")
    scope = Table(title=f"In scope: {len(rec['scope'])} {rec['product']} device(s) in one Meraki network")
    for column in ("Device", "Model", "Firmware now", "Status"):
        scope.add_column(column)
    for d in rec["scope"]:
        scope.add_row(d["name"], d["model"], d["firmware"], d["status"])
    console.print(scope)

    window = rec["window"]
    colour = {"inside": "green", "outside": "yellow", "none": "yellow"}[window["verdict"]]
    console.print(
        f"Window: {window['text'] or 'none recorded'}  ->  planned for {window['planned_for']}: "
        f"[{colour}]{window['detail']}[/{colour}]"
    )
    approvals = rec["approvals_required"]
    console.print(
        "Approval needed from: "
        + (", ".join(a["label"] for a in approvals) if approvals else "[yellow]nobody recorded[/yellow]")
    )
    if rec["downstream"]:
        table = Table(title=f"{len(rec['downstream'])} device(s) behind them lose connectivity while it runs")
        for column in ("Device", "Criticality", "Owner", "Dashboard can see it"):
            table.add_column(column, overflow="fold")
        for d in rec["downstream"]:
            table.add_row(d["name"], d["criticality"], d["owner"], "yes" if d["managed"] else "no: owner must confirm")
        console.print(table)
    if rec["notify"]:
        console.print("Tell: " + "; ".join(f"{n['label']} ({len(n['for'])} device(s))" for n in rec["notify"]))
    for warning in rec["warnings"]:
        console.print(f"[yellow]![/yellow] {warning}")
    console.print(
        f"\n[dim]Ticket text: output/changes/{rec['id']}.md   Next: change approve {rec['id']} --by \"<name>\"[/dim]"
    )


def render_checks(rec: dict[str, Any], checks: list[dict[str, str]], verdict: str) -> None:
    table = Table(title=f"{rec['id']} after the change")
    for column in ("", "Check", "Detail"):
        table.add_column(column, overflow="fold")
    for c in checks:
        style = STATUS_STYLE[c["status"]]
        table.add_row(f"[{style}]{c['status'].upper()}[/{style}]", c["title"], c["detail"])
    console.print(table)
    colour = "green" if verdict == "PASS" else "red"
    console.print(f"[bold {colour}]{verdict}[/bold {colour}]")


# --------------------------------------------------------------------- actions
def do_plan(nb: Any, plan: dict[str, Any], args: Any) -> int:
    with Dashboard(plan) as dash:
        rec = build_plan(nb, plan, dash, args.device, args.to, args.summary, args.at, args.by)
    save_record(rec)
    _path(rec["id"], ".md").write_text(render_ticket(rec), encoding="utf-8")
    _note(nb, rec, f"Planned: {rec['summary']}. Window: {rec['window']['detail']}.")
    render_plan(rec)
    return 0


def _find_approver(rec: dict[str, Any], by: str) -> dict[str, Any] | None:
    wanted = by.strip().lower()
    for item in rec["approvals_required"]:
        if wanted in (item["name"].lower(), item["label"].lower()) or wanted in item["name"].lower().split():
            return item
    return None


def do_approve(nb: Any, plan: dict[str, Any], args: Any) -> int:  # noqa: ARG001
    rec = load_record(args.id)
    if rec["state"] not in ("planned", "approved"):
        raise ChangeError(f"{rec['id']} is {rec['state']}; it can no longer be approved.")
    match = _find_approver(rec, args.by)
    if rec["approvals_required"] and match is None:
        names = ", ".join(a["name"] for a in rec["approvals_required"])
        raise ChangeError(f"'{args.by}' is not an approver for this change. Approval is needed from: {names}.")
    name = match["name"] if match else args.by
    if any(a["name"] == name for a in rec["approvals"]):
        console.print(f"{name} has already approved {rec['id']}.")
        return 0
    rec["approvals"].append({"name": name, "at": _now()})
    missing = [a["name"] for a in rec["approvals_required"] if a["name"] not in {x["name"] for x in rec["approvals"]}]
    rec["state"] = "planned" if missing else "approved"
    _event(rec, f"approved by {name}")
    save_record(rec)
    _note(nb, rec, f"Approved by {name}.")
    console.print(
        f"[green]Approved[/green] by {name}. "
        + (f"Still waiting for: {', '.join(missing)}." if missing else "Nothing else is needed: ready to apply.")
    )
    return 0


def do_apply(nb: Any, plan: dict[str, Any], args: Any) -> int:
    rec = load_record(args.id)
    if rec["state"] not in ("planned", "approved"):
        raise ChangeError(f"{rec['id']} is already {rec['state']}.")
    done = {a["name"] for a in rec["approvals"]}
    missing = [a["name"] for a in rec["approvals_required"] if a["name"] not in done]
    if missing or (not rec["approvals"]):
        raise ChangeError(
            "Not approved yet. "
            + (f"Waiting for: {', '.join(missing)}." if missing else "Record an approval first.")
        )
    clock = site_clock(rec["site"]["zone"], args.as_of)
    verdict = window_verdict(rec["window"]["text"], clock)
    if verdict["verdict"] != "inside" and not args.override_window:
        raise ChangeError(
            f"Not now: {verdict['detail']}. Wait for the window, or take responsibility "
            "with --override-window \"reason\"."
        )
    if args.as_of:
        console.print(f"[dim]Simulated clock: {clock:%Y-%m-%d %H:%M %Z}[/dim]")

    with Dashboard(plan) as dash:
        assert dash.client is not None
        reply = dash.client.upgrade_firmware(rec["network_id"], rec["product"], rec["to_version"])
        for spec in args.simulate_fault or []:
            dash.inject_status(spec, "offline")
        for spec in args.simulate_no_clients or []:
            dash.inject_clients(spec, 0)

    rec["state"] = "applied"
    rec["applied"] = {
        "at": _now(),
        "window": verdict["verdict"],
        "override_reason": args.override_window or "",
        "simulated_faults": list(args.simulate_fault or []) + [f"{n} (no clients)" for n in args.simulate_no_clients or []],
        "reply": reply,
    }
    note = f"applied to {len(rec['scope'])} device(s)"
    if args.override_window:
        note += f" OUTSIDE the window, by override: {args.override_window}"
    _event(rec, note)
    save_record(rec)
    _note(nb, rec, f"Applied. {note}.")
    console.print(f"[green]Applied[/green] {rec['product']} {rec['to_version']} to {len(rec['scope'])} device(s) (MOCK dashboard).")
    for spec in args.simulate_fault or []:
        console.print(f"[yellow]Simulated fault:[/yellow] {spec} now reports offline.")
    for spec in args.simulate_no_clients or []:
        console.print(f"[yellow]Simulated fault:[/yellow] {spec} is online but no client can join.")
    console.print(f"[dim]Next: change verify {rec['id']}[/dim]")
    return 0


def do_confirm(nb: Any, plan: dict[str, Any], args: Any) -> int:  # noqa: ARG001
    """An owner checks the box: what they own behind the change works."""
    rec = load_record(args.id)
    if rec["state"] not in ("applied", "verified", "failed"):
        raise ChangeError(f"{rec['id']} has not been applied yet (state: {rec['state']}).")
    wanted = args.by.strip().lower()
    owners: dict[str, list[str]] = defaultdict(list)
    for d in rec["downstream"]:
        if d["managed"]:
            continue
        for name in d.get("owner_names") or []:
            owners[name].append(d["name"])
    match = next(
        (name for name in owners if wanted == name.lower() or wanted in name.lower().split()),
        None,
    )
    if match is None:
        listed = ", ".join(sorted(owners)) or "nobody (no owners are recorded for them)"
        raise ChangeError(f"'{args.by}' owns nothing this change has to confirm. Owners asked: {listed}.")
    if any(c["name"] == match for c in rec.setdefault("confirmations", [])):
        console.print(f"{match} has already confirmed {rec['id']}.")
        return 0
    rec["confirmations"].append({"name": match, "at": _now(), "note": args.note or "", "devices": owners[match]})
    text = f"{match} confirmed {', '.join(owners[match])} OK" + (f": {args.note}" if args.note else "")
    _event(rec, text)
    save_record(rec)
    _note(nb, rec, text + ".")
    done = {c["name"] for c in rec["confirmations"]}
    waiting = sorted(set(owners) - done)
    console.print(
        f"[green]Confirmed[/green] by {match} ({', '.join(owners[match])}). "
        + (f"Still to confirm: {', '.join(waiting)}." if waiting else "Every owner has confirmed.")
    )
    console.print(f"[dim]Next: change verify {rec['id']} to refresh the evidence[/dim]")
    return 0


def do_verify(nb: Any, plan: dict[str, Any], args: Any) -> int:
    rec = load_record(args.id)
    if rec["state"] not in ("applied", "verified", "failed"):
        raise ChangeError(f"{rec['id']} has not been applied yet (state: {rec['state']}).")
    with Dashboard(plan) as dash:
        after = dash.snapshot()
        clients = dash.client_counts(sorted(rec.get("clients_baseline") or {}))
    netbox_ip: dict[str, str] = {}
    for d in rec["scope"]:
        found = nb.dcim.devices.get(name=d["name"])
        address = getattr(getattr(found, "primary_ip4", None), "address", "") if found else ""
        if address:
            netbox_ip[d["name"]] = address.split("/")[0]
    checks = evaluate(rec, after, netbox_ip, clients)
    verdict = verdict_of(checks)

    updates: list[dict[str, str]] = []
    if verdict == "PASS":
        for d in rec["scope"]:
            found = nb.dcim.devices.get(name=d["name"])
            if found is None:
                continue
            old = (found.custom_fields or {}).get("firmware_version") or ""
            if old != rec["to_version"]:
                found.update({"custom_fields": {"firmware_version": rec["to_version"]}})
                updates.append({"device": d["name"], "old": old, "new": rec["to_version"]})
    rec["state"] = "verified" if verdict == "PASS" else "failed"
    rec["netbox_updates"] = updates or rec.get("netbox_updates", [])
    rec["checks"] = checks
    _event(rec, f"verification {verdict}")
    save_record(rec)

    evidence = render_evidence(rec, after, checks, verdict, updates)
    _path(rec["id"], "-evidence.md").write_text(evidence, encoding="utf-8")
    failed = [c["title"] for c in checks if c["status"] == "fail"]
    _note(nb, rec, f"Verification {verdict}." + (f" Failed: {'; '.join(failed)}." if failed else ""))
    render_checks(rec, checks, verdict)
    for u in updates:
        console.print(f"NetBox updated: {u['device']} firmware {u['old'] or 'blank'} -> {u['new']}")
    console.print(f"[dim]Evidence: output/changes/{rec['id']}-evidence.md[/dim]")
    return 0 if verdict == "PASS" else 1


def do_show(nb: Any, plan: dict[str, Any], args: Any) -> int:  # noqa: ARG001
    rec = load_record(args.id)
    path = _path(rec["id"], "-evidence.md" if args.evidence else ".md")
    if not path.exists():
        raise ChangeError(f"{path.name} does not exist yet.")
    print(path.read_text(encoding="utf-8"))
    return 0


def do_fault(nb: Any, plan: dict[str, Any], args: Any) -> int:  # noqa: ARG001
    """A demo lever: make a device in the mock dashboard go offline, or come back."""
    with Dashboard(plan) as dash:
        if args.no_clients:
            dash.inject_clients(args.device, None if args.clear else 0)
            what = "has its usual clients again" if args.clear else "is online but no client can join"
        else:
            status = "online" if args.clear else "offline"
            dash.inject_status(args.device, status)
            what = f"now reports {status}"
    console.print(f"[yellow]Simulated:[/yellow] {args.device} {what} (MOCK dashboard).")
    return 0


def do_reset(nb: Any, plan: dict[str, Any], args: Any) -> int:  # noqa: ARG001
    """Undo the demo: forget dashboard changes, restore NetBox firmware values, delete records."""
    records = sorted(CHANGES_DIR.glob("CHG-*.json")) if CHANGES_DIR.exists() else []
    restores: list[tuple[str, str]] = []
    for path in records:
        with open(path, encoding="utf-8") as handle:
            for item in json.load(handle).get("netbox_updates", []):
                restores.append((item["device"], item["old"]))
    console.print(
        f"{len(records)} change record(s), {len(restores)} NetBox value(s) to restore, "
        f"dashboard changes {'saved' if STATE_FILE.exists() else 'none'}."
    )
    if not args.yes:
        console.print("[dim]Dry run. Add --yes to do it. NetBox journal entries are kept.[/dim]")
        return 0
    for device, old in restores:
        found = nb.dcim.devices.get(name=device)
        if found is not None:
            found.update({"custom_fields": {"firmware_version": old or None}})
    reset_state()
    for path in CHANGES_DIR.glob("CHG-*") if CHANGES_DIR.exists() else []:
        path.unlink()
    console.print("[green]Reset.[/green]")
    return undo_all(nb, apply=True)


ACTIONS = {
    "plan": do_plan,
    "approve": do_approve,
    "apply": do_apply,
    "verify": do_verify,
    "confirm": do_confirm,
    "show": do_show,
    "fault": do_fault,
    "reset": do_reset,
}


def run(nb: Any, plan: dict[str, Any], args: Any) -> int:
    try:
        return ACTIONS[args.action](nb, plan, args)
    except (ChangeError, LookupError, ValueError) as exc:
        print(f"change: {exc}", file=sys.stderr)
        return 1
