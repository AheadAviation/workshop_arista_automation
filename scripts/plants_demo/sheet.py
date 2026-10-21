"""Stage 3 (optional): validate and import a spreadsheet (CSV) of assets.

Most organisations already keep an asset register in a spreadsheet. This reads
one, checks it against NetBox and the add-on's address plan, and reports what is
wrong with it BEFORE anything is written: unknown sites, duplicate IPs, assets
nobody owns, operating systems past end-of-life.

Safe by design:
  * validate-only unless ``--apply`` is given;
  * a row with an error is never imported; a row with only warnings is;
  * it only creates or updates devices carrying the ``spreadsheet-import`` tag, so
    it cannot overwrite the generated sample devices or the core Arista lab;
  * a blank cell never erases something already recorded in NetBox;
  * everything it creates is also tagged ``plants-demo``, so ``remove`` cleans up.
"""

from __future__ import annotations

import csv
import difflib
import io
import ipaddress
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from rich.table import Table

from .common import REPO_ROOT, Stats, console, interface_groups, upsert
from .populate import Populator

DEFAULT_SHEET = REPO_ROOT / "netbox_data" / "samples" / "legacy_asset_register.csv"
NETWORK_KINDS = ("firewall", "switch", "ap")

# canonical column -> accepted header spellings (lower-case, letters and digits only)
COLUMNS: dict[str, set[str]] = {
    "name": {"assetname", "name", "hostname", "devicename", "computername", "asset"},
    "site": {"site", "location", "plant", "facility", "sitename"},
    "model": {"model", "modelnumber", "hardwaremodel"},
    "serial": {"serial", "serialnumber", "serialno", "sn"},
    "ip": {"ip", "ipaddress", "ipaddr", "mgmtip", "address"},
    "os": {"os", "operatingsystem", "osversion"},
    "owner": {"owner", "assetowner", "ownedby", "responsible", "primarycontact", "contact"},
    "application": {"application", "app", "businessapplication", "function", "purpose"},
    "criticality": {"criticality", "priority", "tier", "businesscriticality"},
    "status": {"status", "state"},
    "notes": {"notes", "comments", "remarks"},
}
REQUIRED_COLUMNS = ("name", "site", "model")

CRITICALITY = {
    "critical": "critical", "crit": "critical", "p1": "critical", "tier1": "critical",
    "high": "high", "p2": "high", "tier2": "high",
    "medium": "medium", "med": "medium", "moderate": "medium", "p3": "medium", "tier3": "medium",
    "low": "low", "p4": "low", "tier4": "low",
}  # fmt: skip
NO_IP_WORDS = {"", "dhcp", "na", "none", "tbd", "unknown"}


class SheetError(Exception):
    """A problem with the file or with NetBox that stops the whole import."""


# ---------------------------------------------------------------- text helpers
def squash(text: str) -> str:
    """Lower-case letters and digits only: 'MS120-24P' -> 'ms12024p'."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def words(text: str) -> str:
    """Lower-case words separated by single spaces: 'Win-7  Pro' -> 'win 7 pro'."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def has_phrase(text: str, phrase: str) -> bool:
    """True if ``phrase`` appears in ``text`` as whole words ('xp' is not in 'express')."""
    return f" {words(phrase)} " in f" {words(text)} "


def clean_name(text: str) -> tuple[str, bool]:
    """Lower-case, hyphen-separated name; the flag says more than just case changed."""
    stripped = text.strip()
    hyphenated = re.sub(r"-{2,}", "-", re.sub(r"[\s_]+", "-", stripped)).strip("-")
    return hyphenated.lower(), hyphenated != stripped


def _name_tokens(text: str) -> list[str]:
    text = text.strip()
    if "," in text:  # "Kim, Joanne" -> "Joanne Kim"
        last, _, first = text.partition(",")
        text = f"{first} {last}"
    return re.findall(r"[a-z]+", text.lower())


def match_contact(text: str, contacts: list[tuple[int, str]]) -> tuple[str, list[tuple[int, str]]]:
    """Find the contact a free-text owner refers to.

    Returns ``(how, matches)`` where ``how`` is ``exact``, ``partial`` (initials or a
    single name), ``similar`` (typo), ``ambiguous`` or ``none``. Only a single
    unambiguous match is ever accepted.
    """
    wanted = _name_tokens(text)
    if not wanted:
        return "none", []
    people = [(cid, name, _name_tokens(name)) for cid, name in contacts]

    exact = [(cid, name) for cid, name, tokens in people if tokens == wanted]
    if len(exact) == 1:
        return "exact", exact

    def fits(tokens: list[str]) -> bool:
        remaining = list(tokens)
        full = False
        for token in wanted:
            if token in remaining:
                remaining.remove(token)
                full = True
                continue
            initial = next((t for t in remaining if len(token) == 1 and t.startswith(token)), None)
            if initial is None:
                return False
            remaining.remove(initial)
        return full

    partial = [(cid, name) for cid, name, tokens in people if fits(tokens)]
    if len(partial) == 1:
        return "partial", partial
    if partial:
        return "ambiguous", partial

    joined = " ".join(wanted)
    close = [
        (cid, name)
        for cid, name, tokens in people
        if difflib.SequenceMatcher(None, joined, " ".join(tokens)).ratio() >= 0.85
    ]
    if len(close) == 1:
        return "similar", close
    return ("ambiguous", close) if close else ("none", [])


def match_site(text: str, sites: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None, list]:
    """Resolve a free-text site to one of ``sites`` -> (how, site, suggestions)."""
    key = squash(text)
    if not key:
        return "none", None, []
    exact = [s for s in sites if key in s["aliases"]]
    if len(exact) == 1:
        return "exact", exact[0], []
    scored = sorted(
        (
            (max(difflib.SequenceMatcher(None, key, a).ratio() for a in s["aliases"]), s)
            for s in sites
            if s["aliases"]
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    close = [s for score, s in scored if score >= 0.8]
    if len(close) == 1:
        return "similar", close[0], []
    return "none", None, [s for score, s in scored[:2] if score >= 0.6]


def match_model(
    text: str, catalog: dict[str, dict[str, Any]], aliases: dict[str, str]
) -> tuple[str | None, str]:
    """Resolve a sheet model string to a device type key -> (key, how)."""
    squashed = squash(text)
    if not squashed:
        return None, "blank"
    exact = [
        key
        for key, dtype in catalog.items()
        if squashed in (squash(dtype["model"]), squash(dtype["slug"]))
    ]
    if len(exact) == 1:
        return exact[0], "exact"
    inside = [key for key, dtype in catalog.items() if has_phrase(text, dtype["model"])]
    if len(inside) == 1:
        return inside[0], "exact"
    for phrase, key in aliases.items():
        if has_phrase(text, phrase) and key in catalog:
            return key, "alias"
    if len(squashed) >= 4:
        partial = [key for key, dtype in catalog.items() if has_phrase(dtype["model"], text)]
        if len(partial) == 1:
            return partial[0], "partial"
    return None, "unknown"


# ------------------------------------------------------------------ row model
@dataclass
class Issue:
    severity: str  # "error" rejects the row; "warning" imports it with a caveat
    code: str
    message: str


@dataclass
class Row:
    number: int  # spreadsheet row number (the header is row 1)
    raw: dict[str, str]
    name: str = ""
    site: dict[str, Any] | None = None
    type_key: str | None = None
    ip: str | None = None
    platform: str | None = None
    eol: str | None = None
    owner: tuple[int, str] | None = None
    status: str = "active"
    criticality: str | None = None
    tags: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    existing: bool = False  # already imported by an earlier run

    def add(self, severity: str, code: str, message: str) -> None:
        self.issues.append(Issue(severity, code, message))

    @property
    def rejected(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def result(self) -> str:
        if self.rejected:
            return "rejected"
        return "warning" if self.issues else "ready"

    def comments(self, source: str) -> str:
        text = f"Imported from {source}, row {self.number}."
        if self.raw.get("notes"):
            text += f"\n\nSheet notes: {self.raw['notes']}"
        return text


# ------------------------------------------------------------------- importer
class SheetImporter:
    def __init__(self, nb: Any, plan: dict[str, Any], path: Path):
        self.nb = nb
        self.plan = plan
        self.path = Path(path)
        self.cfg: dict[str, Any] = plan.get("sheet_import") or {}
        if not self.cfg:
            raise SheetError("The data file has no 'sheet_import' section.")
        self.import_tag: str = self.cfg.get("tag", "spreadsheet-import")
        self.addon_tag: str = plan["addon"]["tag"]
        self.catalog: dict[str, dict[str, Any]] = plan["device_types"]
        self.inherit_roles: list[str] = plan.get("audit", {}).get("site_inherit_roles", [])
        self.pop = Populator(nb, plan)
        self.stats = Stats()
        self.rows: list[Row] = []
        self.unused_columns: list[str] = []
        self.sites: list[dict[str, Any]] = []
        self.contacts: list[tuple[int, str]] = []
        self.devices: dict[str, Any] = {}
        self.ips: dict[str, dict[str, Any]] = {}
        self.serials: dict[str, str] = {}
        self.prefixes: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        self._site_ids: dict[str, int] = {}

    # ------------------------------------------------------------------ load
    def load(self) -> None:
        """Read the CSV and look up everything validation needs from NetBox."""
        self._read_file()
        self._read_netbox()

    def _read_file(self) -> None:
        if not self.path.exists():
            raise SheetError(
                f"File not found: {self.path}. Put the CSV under netbox_data/ (the container "
                "cannot see files outside the folders the repo mounts) and pass e.g. "
                "netbox_data/samples/yours.csv"
            )
        try:
            text = self.path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:  # Excel's plain "CSV" on Windows is cp1252
            text = self.path.read_text(encoding="cp1252")
        first_line = text.splitlines()[0] if text.strip() else ""
        delimiter = max(",;\t", key=first_line.count)
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        header = next(reader, None)
        if not header:
            raise SheetError(f"{self.path.name} is empty.")

        mapping: dict[str, int] = {}
        for index, title in enumerate(header):
            canonical = next((c for c, names in COLUMNS.items() if squash(title) in names), None)
            if canonical and canonical not in mapping:
                mapping[canonical] = index
            elif title.strip():
                self.unused_columns.append(title.strip())
        missing = [column for column in REQUIRED_COLUMNS if column not in mapping]
        if missing:
            raise SheetError(
                f"No column found for: {', '.join(missing)}. Headers in the file: "
                f"{', '.join(h.strip() for h in header if h.strip())}"
            )
        for number, cells in enumerate(reader, start=2):
            if not any(cell.strip() for cell in cells):
                continue
            record = {
                column: (cells[index].strip() if index < len(cells) else "")
                for column, index in mapping.items()
            }
            for column in COLUMNS:
                record.setdefault(column, "")
            self.rows.append(Row(number, record))
        if not self.rows:
            raise SheetError(f"{self.path.name} has a header but no data rows.")

    def _read_netbox(self) -> None:
        present = {site.slug for site in self.nb.dcim.sites.filter(tag=self.addon_tag)}
        if not present:
            raise SheetError(
                "The sample organisation is not in NetBox yet - run the 'plants' command first."
            )
        by_alias: dict[str, set[str]] = {}
        for cfg in self.plan["sites"]:
            first_word = cfg["name"].split()[0]
            for alias in {squash(cfg["code"]), squash(cfg["name"]), squash(first_word)}:
                if len(alias) >= 4 and cfg["code"] in present:
                    by_alias.setdefault(alias, set()).add(cfg["code"])
        for cfg in self.plan["sites"]:
            if cfg["code"] in present:
                aliases = {a for a, codes in by_alias.items() if codes == {cfg["code"]}}
                self.sites.append({**cfg, "aliases": aliases})

        self.contacts = [
            (c.id, c.name) for c in self.nb.tenancy.contacts.filter(tag=self.addon_tag)
        ]
        self.devices = {device.name.lower(): device for device in self.nb.dcim.devices.all()}
        self.serials = {d.serial.lower(): d.name for d in self.devices.values() if d.serial}
        for ip in self.nb.ipam.ip_addresses.all():
            assigned = getattr(ip, "assigned_object", None)
            device = getattr(getattr(assigned, "device", None), "name", None)
            self.ips[str(ipaddress.ip_interface(ip.address).ip)] = {
                "record": ip,
                "device": device,
                "ours": any(tag.slug == self.import_tag for tag in ip.tags),
            }
        self.prefixes = [
            ipaddress.ip_network(p.prefix) for p in self.nb.ipam.prefixes.filter(tag=self.addon_tag)
        ]

    # -------------------------------------------------------------- validate
    def validate(self) -> None:
        names: dict[str, int] = {}
        addresses: dict[str, int] = {}
        serials: dict[str, int] = {}
        for row in self.rows:
            self._check_row(row, names, addresses, serials)

    def _owned(self, device: Any) -> bool:
        return any(tag.slug == self.import_tag for tag in device.tags)

    def _check_row(
        self, row: Row, names: dict[str, int], addresses: dict[str, int], serials: dict[str, int]
    ) -> None:
        raw = row.raw
        if not raw["name"]:
            row.add("error", "missing-name", "no asset name in this row")
            return
        row.name, altered = clean_name(raw["name"])
        if altered:
            row.add("warning", "name-normalized", f"name '{raw['name']}' stored as '{row.name}'")
        if row.name in names:
            row.add("error", "duplicate-name", f"same asset as row {names[row.name]}")
            return
        names[row.name] = row.number
        existing = self.devices.get(row.name)
        if existing is not None:
            if not self._owned(existing):
                row.add(
                    "error",
                    "name-exists",
                    f"a device named '{row.name}' is already in NetBox and was not created "
                    "by this import - left alone",
                )
                return
            row.existing = True

        self._check_site(row)
        self._check_model(row)
        self._check_os(row)
        self._check_ip(row, addresses)
        self._check_serial(row, serials)
        self._check_owner(row)
        self._check_status(row)
        self._check_criticality(row)

    def _check_site(self, row: Row) -> None:
        text = row.raw["site"]
        how, site, suggestions = match_site(text, self.sites)
        if site is not None:
            row.site = site
            if how == "similar":
                row.add("warning", "site-matched", f"site '{text}' taken to mean {site['name']}")
            return
        if not text:
            row.add("error", "unknown-site", "no site given")
            return
        hint = (
            f" - did you mean {' or '.join(s['name'] for s in suggestions)}?" if suggestions else ""
        )
        row.add("error", "unknown-site", f"site '{text}' is not in NetBox{hint}")

    def _check_model(self, row: Row) -> None:
        text = row.raw["model"]
        aliases = {str(k): v for k, v in self.cfg.get("model_aliases", {}).items()}
        key, how = match_model(text, self.catalog, aliases)
        if key is None:
            if how == "blank":
                row.add("error", "unknown-model", "no model given")
            else:
                known = ", ".join(t["model"] for t in self.catalog.values())
                row.add(
                    "error",
                    "unknown-model",
                    f"model '{text}' matches no device type in NetBox (known: {known})",
                )
            return
        row.type_key = key
        if how == "partial":
            row.add(
                "warning",
                "model-matched",
                f"model '{text}' taken to mean {self.catalog[key]['model']}",
            )

    def _check_os(self, row: Row) -> None:
        if row.type_key is None:
            return
        dtype = self.catalog[row.type_key]
        if dtype["kind"] in NETWORK_KINDS:
            row.platform = dtype.get("platform")
            return
        text = row.raw["os"]
        if not text:
            row.add("warning", "no-os", "no operating system recorded")
            return
        for phrase, slug in self.cfg.get("platform_aliases", {}).items():
            if has_phrase(text, str(phrase)):
                row.platform = slug
                break
        else:
            row.add("warning", "unknown-os", f"OS '{text}' not recognised - platform left blank")
            return
        eol = self.cfg.get("eol_dates", {}).get(row.platform)
        if eol:
            row.eol = str(eol)
            if date.fromisoformat(row.eol) < date.today():
                row.tags.append("legacy-os")
                row.add("warning", "os-eol", f"{text} reached end-of-life on {row.eol}")

    def _plan_network(self, site: dict[str, Any]) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(f"10.{site['index']}.0.0/16")

    def _check_ip(self, row: Row, addresses: dict[str, int]) -> None:
        text = row.raw["ip"]
        if squash(text) in NO_IP_WORDS:
            detail = f"'{text}' is not an address" if text else "no IP address recorded"
            row.add("warning", "no-ip", detail)
            return
        try:
            host = ipaddress.ip_interface(text).ip
        except ValueError:
            row.add("error", "bad-ip", f"'{text}' is not a valid IP address")
            return
        label = str(host)
        if label in addresses:
            row.add("error", "duplicate-ip", f"{label} is also used on row {addresses[label]}")
            return
        addresses[label] = row.number
        known = self.ips.get(label)
        if known is not None and not (known["ours"] and known["device"] == row.name):
            where = f"assigned to {known['device']}" if known["device"] else "not assigned"
            row.add("error", "ip-in-use", f"{label} is already in NetBox ({where})")
            return

        if row.site is not None and host not in self._plan_network(row.site):
            owner = next((s for s in self.sites if host in self._plan_network(s)), None)
            detail = f" (it belongs to {owner['name']})" if owner else ""
            row.add(
                "warning",
                "ip-outside-plan",
                f"{label} is outside {row.site['name']}'s address plan "
                f"{self._plan_network(row.site)}{detail}",
            )
        containing = [net for net in self.prefixes if host in net]
        length = max((net.prefixlen for net in containing), default=host.max_prefixlen)
        row.ip = f"{label}/{length}"

    def _check_serial(self, row: Row, serials: dict[str, int]) -> None:
        serial = row.raw["serial"]
        if not serial:
            return
        key = serial.lower()
        if key in serials:
            row.add("warning", "duplicate-serial", f"serial {serial} is also on row {serials[key]}")
        elif key in self.serials and self.serials[key] != row.name:
            row.add("warning", "duplicate-serial", f"serial {serial} is on {self.serials[key]}")
        serials.setdefault(key, row.number)

    def _check_owner(self, row: Row) -> None:
        text = row.raw["owner"]
        inherits = row.type_key is not None and self.catalog[row.type_key]["role"] in (
            self.inherit_roles
        )
        if not text:
            if not inherits:  # network gear falls back to its site's owner
                row.add("warning", "no-owner", "nobody recorded as owner")
            return
        how, matches = match_contact(text, self.contacts)
        if how in ("exact", "partial", "similar"):
            row.owner = matches[0]
            if how != "exact":
                row.add("warning", "owner-matched", f"owner '{text}' taken to mean {matches[0][1]}")
        elif how == "ambiguous":
            names = " or ".join(name for _, name in matches)
            row.add("warning", "unknown-owner", f"owner '{text}' could be {names} - left blank")
        else:
            row.add("warning", "unknown-owner", f"owner '{text}' is not a known contact")

    def _check_status(self, row: Row) -> None:
        text = row.raw["status"]
        aliases = {squash(str(k)): v for k, v in self.cfg.get("status_aliases", {}).items()}
        if not text:
            return
        mapped = aliases.get(squash(text))
        if mapped is None:
            row.add(
                "warning", "unknown-status", f"status '{text}' not recognised - imported as active"
            )
            return
        row.status = mapped
        if mapped == "decommissioning":
            row.tags.append("decommission-candidate")

    def _check_criticality(self, row: Row) -> None:
        text = row.raw["criticality"]
        if not text:
            row.add("warning", "no-criticality", "no criticality recorded")
            return
        row.criticality = CRITICALITY.get(squash(text))
        if row.criticality is None:
            row.add("warning", "unknown-criticality", f"criticality '{text}' not recognised")

    # ----------------------------------------------------------------- apply
    def apply(self) -> Stats:
        """Write every row that has no errors."""
        tag = next((t for t in self.plan["tags"] if t["slug"] == self.import_tag), None) or {
            "name": self.import_tag,
            "slug": self.import_tag,
            "color": "607d8b",
            "description": "Imported from a spreadsheet by the Plants Demo add-on",
        }
        upsert(self.nb.extras.tags, {"slug": self.import_tag}, tag, self.stats, quiet=True)
        for row in self.rows:
            if not row.rejected:
                self._write(row)
        return self.stats

    def _site_id(self, code: str) -> int:
        if code not in self._site_ids:
            self._site_ids[code] = self.nb.dcim.sites.get(slug=code).id
        return self._site_ids[code]

    def _write(self, row: Row) -> None:
        assert row.type_key is not None and row.site is not None  # guaranteed: no errors
        dtype = self.catalog[row.type_key]
        serial = row.raw["serial"]
        fields: dict[str, Any] = {
            "criticality": row.criticality,
            "business_application": row.raw["application"] or None,
            "os_eol_date": row.eol,
            "source_system": self.cfg.get("source_system", "spreadsheet"),
        }
        if dtype["manufacturer"] == "cisco-meraki" and serial:
            fields["meraki_serial"] = serial
        extra = ["ot-network"] if dtype["kind"] == "endpoint" else []
        data: dict[str, Any] = {
            "name": row.name,
            "device_type": self.pop.ref("device_type", dtype["slug"]),
            "role": self.pop.ref("role", dtype["role"]),
            "site": self._site_id(row.site["code"]),
            "tenant": self.pop.ref("tenant", row.site["tenant"]),
            "status": row.status,
            "tags": self.pop.tags(self.import_tag, *dtype.get("tags", []), *extra, *row.tags),
            "custom_fields": fields,
            "comments": row.comments(self.path.name),
        }
        if row.platform:
            data["platform"] = self.pop.ref("platform", row.platform)
        if serial:
            data["serial"] = serial
        known = self.devices.get(row.name)
        if known is not None and known.custom_fields.get("source_system") == "meraki-api":
            # The Meraki dashboard owns what is observed; the sheet must not revert it.
            for key in ("status", "serial"):
                data.pop(key, None)
            for key in ("source_system", "meraki_serial"):
                fields.pop(key, None)
        device = upsert(self.nb.dcim.devices, {"name": row.name}, data, self.stats, row.name)
        if device is None:
            return
        self._write_ip(row, device, dtype)
        self._write_owner(row, device)

    def _write_ip(self, row: Row, device: Any, dtype: dict[str, Any]) -> None:
        if row.ip is None:
            return  # a blank cell never erases an address already in NetBox
        groups = interface_groups(dtype)
        names = (groups.get("mgmt") if dtype["kind"] in NETWORK_KINDS else None) or groups.get(
            "uplink", []
        )
        interface = (
            self.nb.dcim.interfaces.get(device_id=device.id, name=names[0]) if names else None
        )
        if interface is None:
            self.stats.errors += 1
            console.print(f"  [red]✗[/red] {row.name}: no interface to hold {row.ip}")
            return
        record = upsert(
            self.nb.ipam.ip_addresses,
            {"address": row.ip},
            {
                "address": row.ip,
                "status": "active",
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": interface.id,
                "dns_name": f"{row.name}.{self.pop.domain}",
                "tenant": self.pop.ref("tenant", row.site["tenant"]),
                "tags": self.pop.tags(self.import_tag),
            },
            self.stats,
            row.ip,
            quiet=True,
        )
        if record is None:
            return
        if device.serialize().get("primary_ip4") != record.id:
            device.primary_ip4 = record.id
            device.save()
        host = row.ip.split("/")[0]
        for other, info in self.ips.items():  # replace an address this import put there before
            if other != host and info["ours"] and info["device"] == row.name:
                info["record"].delete()
                console.print(f"  [red]-[/red] {row.name}: old address {other}")

    def _write_owner(self, row: Row, device: Any) -> None:
        if row.owner is None:
            return  # a blank or unknown owner never removes an existing one
        role_id = self.pop.ref("contact_role", "owner")
        wanted = row.owner[0]
        current = list(
            self.nb.tenancy.contact_assignments.filter(
                object_type="dcim.device", object_id=device.id, role_id=role_id
            )
        )
        if any(item.contact.id == wanted for item in current):
            self.stats.unchanged += 1
            return
        for item in current:  # the sheet names a different owner: it wins for its own rows
            item.delete()
        try:
            self.nb.tenancy.contact_assignments.create(
                object_type="dcim.device",
                object_id=device.id,
                contact=wanted,
                role=role_id,
                priority="primary",
            )
            self.stats.created += 1
        except Exception as exc:  # noqa: BLE001
            self.stats.errors += 1
            console.print(f"  [red]✗[/red] owner for {row.name}: {exc}")

    # ---------------------------------------------------------------- report
    def summary(self) -> dict[str, int]:
        results = Counter(row.result for row in self.rows)
        importable = [row for row in self.rows if not row.rejected]
        return {
            "rows": len(self.rows),
            "ready": results["ready"],
            "warnings": results["warning"],
            "rejected": results["rejected"],
            "importable": len(importable),
            "already_imported": sum(1 for row in importable if row.existing),
        }

    def by_code(self) -> dict[str, int]:
        counts = Counter(issue.code for row in self.rows for issue in row.issues)
        return dict(counts.most_common())


def _render(importer: SheetImporter, applied: bool) -> None:
    summary = importer.summary()
    mode = "applied" if applied else "validate only - nothing written"
    console.print(f"\n[bold]Spreadsheet import: {importer.path.name}[/bold]  [dim]({mode})[/dim]")
    console.print(
        f"  {summary['rows']} rows: [green]{summary['ready']} ready[/green], "
        f"[yellow]{summary['warnings']} with warnings[/yellow], "
        f"[red]{summary['rejected']} rejected[/red]"
    )
    if importer.unused_columns:
        console.print(f"  [dim]Columns not used: {', '.join(importer.unused_columns)}[/dim]")

    table = Table(title="Findings", show_lines=False)
    table.add_column("Row", justify="right", no_wrap=True)
    table.add_column("Asset", no_wrap=True)
    table.add_column("Result", no_wrap=True)
    table.add_column("What we found", overflow="fold")
    for row in importer.rows:
        if not row.issues:
            continue
        style = "red" if row.rejected else "yellow"
        ordered = sorted(row.issues, key=lambda i: i.severity != "error")
        lines = [
            f"{'[red]✗[/red]' if i.severity == 'error' else '[yellow]![/yellow]'} {i.message}"
            for i in ordered
        ]
        table.add_row(
            str(row.number),
            row.name or row.raw["name"] or "(blank)",
            f"[{style}]{row.result}[/{style}]",
            "\n".join(lines),
        )
    console.print(table)

    codes = ", ".join(f"{code} {count}" for code, count in importer.by_code().items())
    console.print(f"  [dim]Findings by type: {codes or 'none'}[/dim]")
    if applied:
        console.print(f"  [dim]NetBox: {importer.stats.line()}[/dim]")
        return
    new = summary["importable"] - summary["already_imported"]
    console.print(
        f"\n[yellow]Nothing was written.[/yellow] Re-run with --apply to import the "
        f"{summary['importable']} importable rows ({new} new, {summary['already_imported']} "
        "already imported). Rejected rows are never imported."
    )


def _json(importer: SheetImporter, applied: bool) -> str:
    return json.dumps(
        {
            "file": importer.path.name,
            "mode": "apply" if applied else "validate",
            "summary": importer.summary(),
            "findings_by_code": importer.by_code(),
            "unused_columns": importer.unused_columns,
            "rows": [
                {
                    "row": row.number,
                    "asset": row.name or row.raw["name"],
                    "result": row.result,
                    "issues": [vars(issue) for issue in row.issues],
                }
                for row in importer.rows
            ],
            "netbox": vars(importer.stats) if applied else None,
        },
        indent=2,
    )


def run(
    nb: Any,
    plan: dict[str, Any],
    path: Path,
    apply: bool,
    as_json: bool,
    fail_on_errors: bool,
) -> int:
    """Validate (and with ``apply`` import) a CSV asset register. Returns an exit code."""
    try:
        importer = SheetImporter(nb, plan, path)
        importer.load()
        importer.validate()
    except SheetError as exc:
        print(f"import-sheet: {exc}", file=sys.stderr)
        return 1
    if apply:
        importer.apply()
    if as_json:
        print(_json(importer, apply))
    else:
        _render(importer, apply)
    if apply and importer.stats.errors:
        return 1
    return 1 if fail_on_errors and importer.summary()["rejected"] else 0
