"""Stage 4 (optional): sync what the Meraki dashboard knows into NetBox.

The dashboard is the authority for what is *observed*: which devices exist, their
serials, firmware and whether they are online. NetBox stays the authority for
what only people know: owners, approvers, change windows. So the sync:

  WRITES   serial, firmware version, online/offline status, the site's Meraki
           network id, and new devices the dashboard has that NetBox lacks;
  REPORTS  (never changes) a different model, site or IP, devices missing from
           the dashboard, devices that are offline or on odd firmware.

Safe by design:
  * validate-only unless ``--apply`` is given;
  * only devices tagged ``meraki`` + ``plants-demo`` are ever touched, so the core
    Arista lab cannot be affected;
  * it never deletes anything and never overwrites a planned, staged, failed or
    decommissioning status;
  * new devices are only created when their network is mapped to a NetBox site and
    their model is one NetBox has a device type for.

The decision logic is ``compute()``, a pure function of two snapshots, so it can
be tested without a network or a NetBox.
"""

from __future__ import annotations

import ipaddress
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from rich.table import Table

from . import dashboard, meraki_mock
from .common import Stats, console, interface_groups, upsert
from .meraki_client import MerakiClient, MerakiError
from .model import ModelBuilder
from .populate import Populator
from .sheet import clean_name, squash

STATUS_MAP = {"online": "active", "alerting": "active", "offline": "offline", "dormant": "offline"}
SUPPORTED_PRODUCTS = {"appliance", "switch", "wireless"}
# findings that mean "NetBox and the dashboard disagree or something is wrong"
DRIFT_KINDS = {
    "site-drift",
    "model-drift",
    "ip-drift",
    "firmware-drift",
    "device-offline",
    "device-alerting",
    "missing-from-meraki",
}
FINDING_ORDER = [
    "device-offline",
    "device-alerting",
    "missing-from-meraki",
    "firmware-drift",
    "ip-drift",
    "model-drift",
    "site-drift",
    "unsupported-model",
    "unknown-network",
    "network-id-mismatch",
    "name-conflict",
    "ip-conflict",
]


class SyncError(Exception):
    """A problem that stops the whole sync."""


@dataclass
class Finding:
    kind: str
    subject: str
    detail: str


@dataclass
class Action:
    kind: str  # link-site | update-device | create-device
    subject: str
    detail: str
    payload: dict[str, Any]


@dataclass
class Outcome:
    actions: list[Action] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    firmware: dict[str, dict[str, int]] = field(default_factory=dict)
    matched: int = 0

    def find(self, kind: str, subject: str, detail: str) -> None:
        self.findings.append(Finding(kind, subject, detail))

    def act(self, kind: str, subject: str, detail: str, payload: dict[str, Any]) -> None:
        self.actions.append(Action(kind, subject, detail, payload))


# --------------------------------------------------------------- the decisions
def compute(meraki: dict[str, Any], netbox: dict[str, Any]) -> Outcome:
    """Compare a dashboard snapshot with a NetBox snapshot. Pure: no I/O.

    ``meraki``: ``networks``, ``devices``, ``availabilities`` (as the API returns them).
    ``netbox``: ``sites`` (slug, name, network_id), ``devices`` (id, name, serial,
    meraki_serial, site, model, status, firmware, source_system, ip), ``names`` and
    ``ips`` (everything already used), ``models`` (supported model, lower-case).
    """
    out = Outcome()
    mapped = _map_networks(out, meraki["networks"], netbox["sites"])

    by_serial: dict[str, dict[str, Any]] = {}
    for device in netbox["devices"]:
        for value in (device["serial"], device["meraki_serial"]):
            if value:
                by_serial.setdefault(value.upper(), device)
    # Devices with no serial anywhere (e.g. a blank cell in the spreadsheet) can only be
    # recognised by name; they get their serial from the dashboard.
    unserialled = {
        d["name"].lower(): d
        for d in netbox["devices"]
        if not d["serial"] and not d["meraki_serial"]
    }
    status_of = {item["serial"]: item["status"] for item in meraki["availabilities"]}
    taken_names, taken_ips = set(netbox["names"]), set(netbox["ips"])
    site_names = {site["slug"]: site["name"] for site in netbox["sites"]}
    seen: set[int] = set()

    for dev in meraki["devices"]:
        status = status_of.get(dev["serial"])
        site = mapped.get(dev.get("networkId"))
        match = by_serial.get(dev["serial"].upper())
        if match is None and dev.get("name"):
            candidate = unserialled.get(clean_name(dev["name"])[0])
            if candidate is not None and candidate["id"] not in seen:
                match = candidate
        if match is not None:
            seen.add(match["id"])
            out.matched += 1
            _compare(out, dev, status, site, match, site_names)
        else:
            _consider_new(out, dev, status, site, netbox["models"], taken_names, taken_ips)

    for device in netbox["devices"]:
        if device["id"] not in seen:
            out.find(
                "missing-from-meraki",
                device["name"],
                f"serial {device['serial'] or device['meraki_serial'] or '(none)'} at "
                f"{device['site']} is not in the dashboard",
            )
    _firmware(out, meraki["devices"])
    return out


def _map_networks(
    out: Outcome, networks: list[dict[str, Any]], sites: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Tie each dashboard network to a NetBox site (by saved id, else by name)."""
    by_id = {site["network_id"]: site for site in sites if site["network_id"]}
    by_name = {squash(site["name"]): site for site in sites}
    mapped: dict[str, dict[str, Any]] = {}
    for net in networks:
        site = by_id.get(net["id"])
        if site is None:
            site = by_name.get(squash(net["name"]))
            if site is not None and site["network_id"] and site["network_id"] != net["id"]:
                out.find(
                    "network-id-mismatch",
                    site["slug"],
                    f"NetBox links network {site['network_id']} but the dashboard network "
                    f"'{net['name']}' is {net['id']} - left alone",
                )
                continue
        if site is None:
            out.find("unknown-network", net["name"], "no NetBox site has this name or network id")
            continue
        mapped[net["id"]] = site
        if site["network_id"] != net["id"]:
            out.act(
                "link-site",
                site["slug"],
                f"network id {net['id']}",
                {"slug": site["slug"], "network_id": net["id"]},
            )
    return mapped


def _compare(
    out: Outcome,
    dev: dict[str, Any],
    status: str | None,
    site: Any,
    match: dict[str, Any],
    site_names: dict[str, str],
) -> None:
    """A dashboard device NetBox already has: write what is observed, report the rest."""
    serial = dev["serial"]
    fields: dict[str, Any] = {}
    custom: dict[str, Any] = {}
    if (match["serial"] or "").upper() != serial.upper():
        fields["serial"] = serial
    if (match["meraki_serial"] or "").upper() != serial.upper():
        custom["meraki_serial"] = serial
    if dev.get("firmware") and dev["firmware"] != match["firmware"]:
        custom["firmware_version"] = dev["firmware"]
    if match["source_system"] != "meraki-api":
        custom["source_system"] = "meraki-api"
    wanted = STATUS_MAP.get(status or "")
    # only flip between active and offline; never overwrite a lifecycle status
    if wanted and match["status"] in ("active", "offline") and wanted != match["status"]:
        fields["status"] = wanted
    if fields or custom:
        out.act(
            "update-device",
            match["name"],
            ", ".join(sorted([*fields, *custom])),
            {"id": match["id"], "fields": fields, "custom_fields": custom},
        )

    if site is not None and site["slug"] != match["site"]:
        out.find(
            "site-drift",
            match["name"],
            f"dashboard says {site['name']}, NetBox says {site_names.get(match['site'], match['site'])}",
        )
    if dev.get("model") and dev["model"] != match["model"]:
        out.find(
            "model-drift",
            match["name"],
            f"dashboard says {dev['model']}, NetBox says {match['model']}",
        )
    lan = dev.get("lanIp")
    if lan and lan != match["ip"]:
        out.find(
            "ip-drift", match["name"], f"dashboard says {lan}, NetBox says {match['ip'] or 'none'}"
        )
    if status in ("offline", "dormant"):
        out.find("device-offline", match["name"], f"dashboard reports it {status}")
    elif status == "alerting":
        out.find("device-alerting", match["name"], "dashboard reports it alerting")


def _consider_new(
    out: Outcome,
    dev: dict[str, Any],
    status: str | None,
    site: Any,
    models: set[str],
    taken_names: set[str],
    taken_ips: set[str],
) -> None:
    """A dashboard device NetBox does not have: create it if that is safe."""
    label = dev.get("name") or dev["serial"]
    model = dev.get("model", "")
    if dev.get("productType") not in SUPPORTED_PRODUCTS or model.lower() not in models:
        out.find(
            "unsupported-model",
            label,
            f"{model or 'unknown model'} ({dev.get('productType')}) has no device type - not imported",
        )
        return
    if site is None:
        out.find("unknown-network", label, "its dashboard network is not linked to a NetBox site")
        return
    name = clean_name(label)[0]
    if name in taken_names:
        out.find("name-conflict", label, f"a different device named '{name}' is already in NetBox")
        return
    address = dev.get("lanIp")
    if address and address in taken_ips:
        out.find("ip-conflict", label, f"{address} is already used in NetBox - created without IP")
        address = None
    taken_names.add(name)
    if address:
        taken_ips.add(address)
    out.act(
        "create-device",
        name,
        f"{model} at {site['slug']}",
        {
            "name": name,
            "original_name": label,
            "site": site["slug"],
            "model": model,
            "serial": dev["serial"],
            "mac": dev.get("mac", ""),
            "firmware": dev.get("firmware", ""),
            "status": STATUS_MAP.get(status or "", "active"),
            "lan_ip": address,
        },
    )


def _firmware(out: Outcome, devices: list[dict[str, Any]]) -> None:
    """Firmware spread per model; flag devices not on the version most of them run."""
    by_model: dict[str, Counter[str]] = {}
    for dev in devices:
        if dev.get("firmware"):
            by_model.setdefault(dev["model"], Counter())[dev["firmware"]] += 1
    out.firmware = {model: dict(counts.most_common()) for model, counts in by_model.items()}
    common = {m: max(c.items(), key=lambda kv: (kv[1], kv[0]))[0] for m, c in by_model.items()}
    for dev in devices:
        firmware = dev.get("firmware")
        if firmware and firmware != common[dev["model"]]:
            out.find(
                "firmware-drift",
                dev.get("name") or dev["serial"],
                f"{firmware} (most {dev['model']} devices run {common[dev['model']]})",
            )


# ---------------------------------------------------------------- the syncing
class MerakiSync:
    def __init__(self, nb: Any, plan: dict[str, Any], client: MerakiClient, org_id: str | None):
        self.nb = nb
        self.plan = plan
        self.client = client
        self.org_id = org_id
        self.addon_tag: str = plan["addon"]["tag"]
        self.pop = Populator(nb, plan)
        self.stats = Stats()
        self.org: dict[str, Any] = {}
        self.outcome = Outcome()
        self.meraki: dict[str, Any] = {}
        self.models = {
            dtype["model"].lower(): key
            for key, dtype in plan["device_types"].items()
            if dtype["manufacturer"] == "cisco-meraki"
        }
        self.site_cfg = {site["code"]: site for site in plan["sites"]}
        self._prefixes: list[Any] = []
        self._site_ids: dict[str, int] = {}

    # ------------------------------------------------------------------ load
    def load(self) -> None:
        self.org = self._pick_org()
        org = self.org["id"]
        self.meraki = {
            "networks": self.client.networks(org),
            "devices": self.client.devices(org),
            "availabilities": self.client.availabilities(org),
        }
        netbox = self._read_netbox()
        self.outcome = compute(self.meraki, netbox)

    def _pick_org(self) -> dict[str, Any]:
        orgs = self.client.organizations()
        if self.org_id:
            org = next((o for o in orgs if str(o["id"]) == str(self.org_id)), None)
            if org is None:
                known = ", ".join(f"{o['id']} ({o['name']})" for o in orgs) or "none"
                raise SyncError(
                    f"Organization {self.org_id} not visible to this key. Visible: {known}"
                )
            return org
        if len(orgs) == 1:
            return orgs[0]
        listing = ", ".join(f"{o['id']} ({o['name']})" for o in orgs) or "none"
        raise SyncError(
            f"This key sees {len(orgs)} organizations; choose one with --org-id: {listing}"
        )

    def _read_netbox(self) -> dict[str, Any]:
        sites = [
            {
                "slug": s.slug,
                "name": s.name,
                "network_id": s.custom_fields.get("meraki_network_id") or "",
            }
            for s in self.nb.dcim.sites.filter(tag=self.addon_tag)
        ]
        if not sites:
            raise SyncError("The sample organisation is not in NetBox yet - run 'plants' first.")
        devices = []
        for d in self.nb.dcim.devices.filter(tag="meraki"):
            if self.addon_tag not in {t.slug for t in d.tags}:
                continue  # never touch anything outside the add-on
            address = getattr(d.primary_ip4, "address", None)
            cf = d.custom_fields
            devices.append(
                {
                    "id": d.id,
                    "name": d.name,
                    "serial": d.serial or "",
                    "meraki_serial": cf.get("meraki_serial") or "",
                    "site": d.site.slug,
                    "model": d.device_type.model,
                    "status": d.status.value,
                    "firmware": cf.get("firmware_version") or "",
                    "source_system": cf.get("source_system") or "",
                    "ip": str(ipaddress.ip_interface(address).ip) if address else None,
                }
            )
        self._prefixes = [
            ipaddress.ip_network(p.prefix) for p in self.nb.ipam.prefixes.filter(tag=self.addon_tag)
        ]
        return {
            "sites": sites,
            "devices": devices,
            "names": {d.name.lower() for d in self.nb.dcim.devices.all()},
            "ips": {
                str(ipaddress.ip_interface(i.address).ip) for i in self.nb.ipam.ip_addresses.all()
            },
            "models": set(self.models),
        }

    # ----------------------------------------------------------------- apply
    def apply(self) -> Stats:
        added = ModelBuilder(self.nb, self.plan)
        was_quiet, console.quiet = console.quiet, True  # the model stage is chatty
        try:
            ensured = added.ensure_extras()
        finally:
            console.quiet = was_quiet
        if ensured.created:
            console.print(f"  [green]+[/green] added {ensured.created} tag/field definition(s)")
        for action in self.outcome.actions:
            if action.kind == "link-site":
                upsert(
                    self.nb.dcim.sites,
                    {"slug": action.payload["slug"]},
                    {"custom_fields": {"meraki_network_id": action.payload["network_id"]}},
                    self.stats,
                    f"site {action.subject}",
                    quiet=True,
                )
            elif action.kind == "update-device":
                data = dict(action.payload["fields"])
                if action.payload["custom_fields"]:
                    data["custom_fields"] = action.payload["custom_fields"]
                upsert(
                    self.nb.dcim.devices,
                    {"id": action.payload["id"]},
                    data,
                    self.stats,
                    action.subject,
                    quiet=True,
                )
            elif action.kind == "create-device":
                self._create(action.payload)
        return self.stats

    def _site_id(self, code: str) -> int:
        if code not in self._site_ids:
            self._site_ids[code] = self.nb.dcim.sites.get(slug=code).id
        return self._site_ids[code]

    def _create(self, p: dict[str, Any]) -> None:
        site_cfg = self.site_cfg[p["site"]]
        dtype = self.plan["device_types"][self.models[p["model"].lower()]]
        data = {
            "name": p["name"],
            "device_type": self.pop.ref("device_type", dtype["slug"]),
            "role": self.pop.ref("role", dtype["role"]),
            "site": self._site_id(p["site"]),
            "tenant": self.pop.ref("tenant", site_cfg["tenant"]),
            "status": p["status"],
            "serial": p["serial"],
            "tags": self.pop.tags("meraki-sync", *dtype.get("tags", [])),
            "custom_fields": {
                "criticality": site_cfg["criticality"],  # new network gear inherits its site's
                "meraki_serial": p["serial"],
                "firmware_version": p["firmware"] or None,
                "source_system": "meraki-api",
            },
            "comments": (
                f"Created by the Meraki sync from dashboard device '{p['original_name']}' "
                f"(MAC {p['mac'] or 'unknown'})."
            ),
        }
        if dtype.get("platform"):
            data["platform"] = self.pop.ref("platform", dtype["platform"])
        device = upsert(self.nb.dcim.devices, {"name": p["name"]}, data, self.stats, p["name"])
        if device is not None and p["lan_ip"]:
            self._create_ip(device, dtype, p, site_cfg)

    def _create_ip(self, device: Any, dtype: dict[str, Any], p: dict[str, Any], cfg: Any) -> None:
        groups = interface_groups(dtype)
        names = groups.get("mgmt") or groups.get("uplink") or []
        interface = (
            self.nb.dcim.interfaces.get(device_id=device.id, name=names[0]) if names else None
        )
        if interface is None:
            self.stats.errors += 1
            console.print(f"  [red]✗[/red] {p['name']}: no interface to hold {p['lan_ip']}")
            return
        host = ipaddress.ip_address(p["lan_ip"])
        length = max((n.prefixlen for n in self._prefixes if host in n), default=host.max_prefixlen)
        address = f"{p['lan_ip']}/{length}"
        record = upsert(
            self.nb.ipam.ip_addresses,
            {"address": address},
            {
                "address": address,
                "status": "active",
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": interface.id,
                "dns_name": f"{p['name']}.{self.pop.domain}",
                "tenant": self.pop.ref("tenant", cfg["tenant"]),
                "tags": self.pop.tags("meraki-sync"),
            },
            self.stats,
            address,
            quiet=True,
        )
        if record is not None and device.serialize().get("primary_ip4") != record.id:
            device.primary_ip4 = record.id
            device.save()

    # ---------------------------------------------------------------- report
    def counts(self) -> dict[str, Any]:
        outcome = self.outcome
        fields = Counter(
            name
            for action in outcome.actions
            if action.kind == "update-device"
            for name in [*action.payload["fields"], *action.payload["custom_fields"]]
        )
        return {
            "dashboard": {
                "networks": len(self.meraki.get("networks", [])),
                "devices": len(self.meraki.get("devices", [])),
            },
            "matched_devices": outcome.matched,
            "to_create": sum(a.kind == "create-device" for a in outcome.actions),
            "sites_to_link": sum(a.kind == "link-site" for a in outcome.actions),
            "devices_to_update": sum(a.kind == "update-device" for a in outcome.actions),
            "fields_to_write": dict(fields.most_common()),
            "findings": dict(Counter(f.kind for f in outcome.findings).most_common()),
        }

    def drift(self) -> int:
        return sum(1 for f in self.outcome.findings if f.kind in DRIFT_KINDS)


def _render(sync: MerakiSync, source: str, applied: bool) -> None:
    counts = sync.counts()
    mode = "applied" if applied else "validate only - nothing written"
    console.print(f"\n[bold]Meraki sync[/bold]  [dim]({mode})[/dim]")
    console.print(f"  Source: {source}")
    console.print(
        f"  Organization: {sync.org['name']} [{sync.org['id']}]   "
        f"API calls: {sync.client.calls}"
        + (f" ({sync.client.retries} retried after HTTP 429)" if sync.client.retries else "")
    )
    console.print(
        f"  Dashboard: {counts['dashboard']['networks']} networks, "
        f"{counts['dashboard']['devices']} devices  ->  NetBox: "
        f"{counts['matched_devices']} devices matched, "
        f"{counts['to_create']} to create, {counts['sites_to_link']} sites to link"
    )
    written = ", ".join(f"{name} {count}" for name, count in counts["fields_to_write"].items())
    console.print(f"  Fields {'written' if applied else 'to write'}: {written or 'none'}")

    table = Table(title="Findings (reported, never auto-fixed)", show_lines=False)
    table.add_column("Kind", no_wrap=True)
    table.add_column("Device / site", no_wrap=True)
    table.add_column("Detail", overflow="fold")
    rank = {kind: i for i, kind in enumerate(FINDING_ORDER)}
    for finding in sorted(sync.outcome.findings, key=lambda f: (rank.get(f.kind, 99), f.subject)):
        style = "yellow" if finding.kind in DRIFT_KINDS else "dim"
        table.add_row(f"[{style}]{finding.kind}[/{style}]", finding.subject, finding.detail)
    console.print(table)

    spread = Table(title="Firmware in use (from the dashboard)")
    spread.add_column("Model", no_wrap=True)
    spread.add_column("Versions", overflow="fold")
    for model, versions in sorted(sync.outcome.firmware.items()):
        spread.add_row(model, ", ".join(f"{v} x{n}" for v, n in versions.items()))
    console.print(spread)

    if applied:
        console.print(f"  [dim]NetBox: {sync.stats.line()}[/dim]")
    else:
        console.print(
            "\n[yellow]Nothing was written.[/yellow] Re-run with --apply to write the fields above "
            "and create the new devices. Findings are only ever reported."
        )


def _json(sync: MerakiSync, source: str, applied: bool) -> str:
    return json.dumps(
        {
            "mode": "apply" if applied else "validate",
            "source": source,
            "organization": sync.org,
            "api": {"calls": sync.client.calls, "retries": sync.client.retries},
            "summary": sync.counts(),
            "findings": [vars(f) for f in sync.outcome.findings],
            "actions": [
                {"kind": a.kind, "subject": a.subject, "detail": a.detail}
                for a in sync.outcome.actions
            ],
            "firmware": sync.outcome.firmware,
            "netbox": vars(sync.stats) if applied else None,
        },
        indent=2,
    )


def run(
    nb: Any,
    plan: dict[str, Any],
    *,
    base_url: str | None,
    api_key: str | None,
    org_id: str | None,
    apply: bool,
    as_json: bool,
    fail_on_drift: bool,
    mock_rate_limit: int = 0,
) -> int:
    """Sync the dashboard into NetBox (a mock dashboard unless ``base_url`` is given)."""
    server = None
    try:
        if base_url is None:
            server = meraki_mock.start(
                dashboard.load_world(plan), rate_limit_every=mock_rate_limit
            )
            base_url, api_key = server.base_url, meraki_mock.DEFAULT_KEY
            source = "MOCK Meraki API (embedded; not a real dashboard)"
        else:
            api_key = api_key or os.environ.get("MERAKI_API_KEY")
            if not api_key:
                raise SyncError(
                    "An API key is required with --base-url (--api-key or MERAKI_API_KEY)."
                )
            source = base_url
        sync = MerakiSync(nb, plan, MerakiClient(base_url, api_key), org_id)
        sync.load()
        if apply:
            sync.apply()
    except (MerakiError, SyncError, ValueError) as exc:
        print(f"meraki-sync: {exc}", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            meraki_mock.stop(server)

    if as_json:
        print(_json(sync, source, apply))
    else:
        _render(sync, source, apply)
    if apply and sync.stats.errors:
        return 1
    return 1 if fail_on_drift and sync.drift() else 0
