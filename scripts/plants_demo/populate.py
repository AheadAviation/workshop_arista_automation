"""Stage 2: create the sample sites, devices, addressing, cabling and owners.

Everything created here is tagged ``plants-demo`` and is NEVER tagged
``containerlab``, which is how the core lab's inventory, config generation and
ContainerLab topology scripts select their devices. The add-on is therefore
invisible to them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .common import Stats, console, interface_groups, netbox_version, upsert

# kind -> (NetBox app, endpoint) for reference lookups by slug
REFS: dict[str, tuple[str, str]] = {
    "tag": ("extras", "tags"),
    "manufacturer": ("dcim", "manufacturers"),
    "platform": ("dcim", "platforms"),
    "role": ("dcim", "device_roles"),
    "device_type": ("dcim", "device_types"),
    "prefix_role": ("ipam", "roles"),
    "region": ("dcim", "regions"),
    "tenant": ("tenancy", "tenants"),
    "contact_group": ("tenancy", "contact_groups"),
    "contact_role": ("tenancy", "contact_roles"),
    "circuit_type": ("circuits", "circuit_types"),
}

PRIORITIES = ["primary", "secondary", "tertiary"]
# Last octet where each kind of device starts inside its VLAN
IP_BASE = {"firewall": 2, "switch": 11, "ap": 21, "server": 11, "endpoint": 11}
NAME_PREFIX = {"firewall": "fw", "switch": "sw", "ap": "ap"}


@dataclass
class DevicePlan:
    """A device the add-on will create, resolved from the data file."""

    name: str
    type_key: str
    kind: str
    site: str
    vid: int
    extra_tags: list[str] = field(default_factory=list)
    custom_fields: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    owners: list[str] = field(default_factory=list)
    approvers: list[str] = field(default_factory=list)


def meraki_serial(name: str) -> str:
    """Deterministic, Meraki-looking serial so the later API sync can match on it."""
    digest = hashlib.sha1(name.encode()).hexdigest().upper()  # noqa: S324 - not security related
    return f"Q2{digest[0:2]}-{digest[2:6]}-{digest[6:10]}"


def site_vlans(plan: dict[str, Any], site: dict[str, Any]) -> list[dict[str, Any]]:
    """The VLANs a site gets: the common ones, OT at plants, and its profile's extras."""
    vlans = list(plan["vlans"]["common"])
    if site["kind"] == "plant":
        vlans += plan["vlans"]["plant_only"]
    for vid in plan["profiles"][site["profile"]].get("extra_vlans", []):
        vlans.append({"vid": vid, **plan["vlans"]["extra"][vid]})
    return vlans


def expand_site(plan: dict[str, Any], site: dict[str, Any]) -> list[DevicePlan]:
    """Turn a site entry + its profile into concrete devices (pure function)."""
    profile = plan["profiles"][site["profile"]]
    overrides = site.get("overrides", {})
    counts = site.get("counts", {})
    has_servers_vlan = 50 in profile.get("extra_vlans", [])
    devices: list[DevicePlan] = []

    for kind in ("firewall", "switch", "ap"):
        type_key = overrides.get(kind, profile[kind]["type"])
        count = counts.get(kind, profile[kind]["count"])
        for number in range(1, count + 1):
            devices.append(
                DevicePlan(
                    name=f"{site['code']}-{NAME_PREFIX[kind]}{number:02d}",
                    type_key=type_key,
                    kind=kind,
                    site=site["code"],
                    vid=99,
                    custom_fields={"criticality": site["criticality"]},
                )
            )

    for srv in site.get("servers", []):
        devices.append(
            DevicePlan(
                name=srv["name"],
                type_key="poweredge-r650",
                kind="server",
                site=site["code"],
                vid=50 if has_servers_vlan else 10,
                extra_tags=srv.get("tags", []),
                status=srv.get("status", "active"),
                owners=srv.get("owner", []),
                approvers=srv.get("approver", []),
                custom_fields={
                    "criticality": srv["criticality"],
                    "business_application": srv.get("application") or None,
                    "os_eol_date": srv.get("eol"),
                    "_platform": srv.get("platform"),
                },
            )
        )

    for end in site.get("endpoints", []):
        devices.append(
            DevicePlan(
                name=end["name"],
                type_key="hmi-workstation",
                kind="endpoint",
                site=site["code"],
                vid=end.get("vlan") or (100 if site["kind"] == "plant" else 10),
                extra_tags=["ot-network", *end.get("tags", [])],
                status=end.get("status", "active"),
                owners=end.get("owner", []),
                approvers=end.get("approver", []),
                custom_fields={
                    "criticality": end["criticality"],
                    "business_application": end.get("application") or None,
                    "os_eol_date": end.get("eol"),
                    "_platform": end.get("platform"),
                    "_depends_on": end.get("depends_on", []),
                },
            )
        )
    return devices


class Populator:
    """Creates the sample organisation on top of an existing NetBox."""

    def __init__(self, nb: Any, plan: dict[str, Any]):
        self.nb = nb
        self.plan = plan
        self.stats = Stats()
        self.version = netbox_version(nb)
        self.domain = plan["addon"]["domain"]
        self.addon_tag = plan["addon"]["tag"]
        self._refs: dict[tuple[str, str], int] = {}
        self.contact_ids: dict[str, int] = {}
        self._assignments: set[tuple[str, int, int, int]] = set()

    # ---------------------------------------------------------------- lookups
    def ref(self, kind: str, slug: str) -> int:
        key = (kind, slug)
        if key not in self._refs:
            app, endpoint = REFS[kind]
            record = getattr(getattr(self.nb, app), endpoint).get(slug=slug)
            if record is None:
                raise LookupError(
                    f"{kind} '{slug}' not found in NetBox - run the 'model' stage first"
                )
            self._refs[key] = record.id
        return self._refs[key]

    def tags(self, *extra: str) -> list[int]:
        slugs = [self.addon_tag, *extra]
        return [self.ref("tag", slug) for slug in dict.fromkeys(slugs)]

    # ------------------------------------------------------------------- run
    def run(self, link_fabric: bool = False) -> Stats:
        console.print("\n[bold]Stage 2: Sample organisation[/bold]")
        self._load_assignments()
        self._contacts()
        for site_cfg in self.plan["sites"]:
            console.print(f"[cyan]{site_cfg['code']}[/cyan]  {site_cfg['name']}")
            site = self._site(site_cfg)
            if site is None:
                continue
            self._addressing(site_cfg, site)
            devices = self._devices(site_cfg, site)
            self._ips(site_cfg, site, devices)
            self._cables(site, devices)
            self._assign(site_cfg.get("contacts", {}), "dcim.site", site.id, site_cfg["code"])
        self._circuits()
        if link_fabric:
            self._link_fabric()
        console.print(f"  [dim]{self.stats.line()}[/dim]")
        return self.stats

    # --------------------------------------------------------------- contacts
    def _load_assignments(self) -> None:
        for item in self.nb.tenancy.contact_assignments.all():
            self._assignments.add((item.object_type, item.object_id, item.contact.id, item.role.id))

    def _contacts(self) -> None:
        console.print("[cyan]Contacts[/cyan]")
        for index, (key, item) in enumerate(self.plan["contacts"].items(), start=1):
            data = {
                "name": item["name"],
                "title": item.get("title", ""),
                "email": f"{key}@{self.domain}",
                "phone": f"+1-555-01{index:02d}",
                "group": self.ref("contact_group", item["group"]),
                "tags": self.tags(),
            }
            record = upsert(self.nb.tenancy.contacts, {"name": item["name"]}, data, self.stats)
            if record is not None:
                self.contact_ids[key] = record.id

    def _assign(
        self, mapping: dict[str, list[str]], object_type: str, object_id: int, label: str
    ) -> None:
        """Attach contacts (by role) to a site/device. First listed = primary."""
        for role_slug, keys in mapping.items():
            role_id = self.ref("contact_role", role_slug)
            for position, key in enumerate(keys):
                contact_id = self.contact_ids[key]
                marker = (object_type, object_id, contact_id, role_id)
                if marker in self._assignments:
                    self.stats.unchanged += 1
                    continue
                try:
                    self.nb.tenancy.contact_assignments.create(
                        object_type=object_type,
                        object_id=object_id,
                        contact=contact_id,
                        role=role_id,
                        priority=PRIORITIES[min(position, 2)],
                    )
                    self._assignments.add(marker)
                    self.stats.created += 1
                except Exception as exc:  # noqa: BLE001
                    self.stats.errors += 1
                    console.print(f"  [red]✗[/red] contact {key} -> {label}: {exc}")

    # ----------------------------------------------------------------- sites
    def _site(self, cfg: dict[str, Any]) -> Any | None:
        fields = {
            "criticality": cfg["criticality"],
            "change_window": cfg.get("change_window") or None,
            "firmware_canary": cfg.get("firmware_canary", False),
            "source_system": "spreadsheet",
        }
        extra_tags = ["ot-network"] if cfg["kind"] == "plant" else []
        data = {
            "name": cfg["name"],
            "slug": cfg["code"],
            "status": "active",
            "region": self.ref("region", cfg["region"]),
            "tenant": self.ref("tenant", cfg["tenant"]),
            "time_zone": cfg["time_zone"],
            "physical_address": cfg["address"],
            "tags": self.tags(*extra_tags),
            "custom_fields": fields,
        }
        return upsert(
            self.nb.dcim.sites, {"slug": cfg["code"]}, data, self.stats, f"site {cfg['code']}"
        )

    # ------------------------------------------------------------ VLANs/prefixes
    def _addressing(self, cfg: dict[str, Any], site: Any) -> None:
        vlans = site_vlans(self.plan, cfg)
        tenant_id = self.ref("tenant", cfg["tenant"])
        for item in vlans:
            role_id = self.ref("prefix_role", item["role"])
            vlan = upsert(
                self.nb.ipam.vlans,
                {"site_id": site.id, "vid": item["vid"]},
                {
                    "site": site.id,
                    "vid": item["vid"],
                    "name": item["name"],
                    "status": "active",
                    "role": role_id,
                    "tenant": tenant_id,
                    "tags": self.tags(),
                },
                self.stats,
                f"vlan {cfg['code']}/{item['vid']}",
                quiet=True,
            )
            cidr = f"10.{cfg['index']}.{item['vid']}.0/24"
            data: dict[str, Any] = {
                "prefix": cidr,
                "status": "active",
                "role": role_id,
                "tenant": tenant_id,
                "description": f"{cfg['name']} - {item['name']}",
                "tags": self.tags(),
            }
            if vlan is not None:
                data["vlan"] = vlan.id
            if self.version >= (4, 2):  # prefix scope replaced the plain `site` field in 4.2
                data.update({"scope_type": "dcim.site", "scope_id": site.id})
            else:
                data["site"] = site.id
            upsert(self.nb.ipam.prefixes, {"prefix": cidr}, data, self.stats, cidr, quiet=True)

    # --------------------------------------------------------------- devices
    def _devices(self, cfg: dict[str, Any], site: Any) -> list[tuple[DevicePlan, Any]]:
        tenant_id = self.ref("tenant", cfg["tenant"])
        created: list[tuple[DevicePlan, Any]] = []
        for dev in expand_site(self.plan, cfg):
            dtype = self.plan["device_types"][dev.type_key]
            fields = {k: v for k, v in dev.custom_fields.items() if not k.startswith("_")}
            fields["source_system"] = "spreadsheet"
            needs = dev.custom_fields.get("_depends_on") or []
            if needs:
                fields["depends_on"] = self._device_ids(needs, dev.name)
            if dtype["manufacturer"] == "cisco-meraki":
                fields["meraki_serial"] = meraki_serial(dev.name)

            platform_slug = dev.custom_fields.get("_platform") or dtype.get("platform")
            data: dict[str, Any] = {
                "name": dev.name,
                "device_type": self.ref("device_type", dtype["slug"]),
                "role": self.ref("role", dtype["role"]),
                "site": site.id,
                "tenant": tenant_id,
                "status": dev.status,
                "tags": self.tags(*dtype.get("tags", []), *dev.extra_tags),
                "custom_fields": fields,
            }
            if platform_slug:
                data["platform"] = self.ref("platform", platform_slug)
            if dtype["manufacturer"] == "cisco-meraki":
                data["serial"] = meraki_serial(dev.name)

            record = upsert(self.nb.dcim.devices, {"name": dev.name}, data, self.stats, dev.name)
            if record is not None:
                created.append((dev, record))
                owner_map = {}
                if dev.owners:
                    owner_map["owner"] = dev.owners
                if dev.approvers:
                    owner_map["approver"] = dev.approvers
                if owner_map:
                    self._assign(owner_map, "dcim.device", record.id, dev.name)
        return created

    def _device_ids(self, names: list[str], label: str) -> list[int]:
        """Ids of devices that already exist (a server is created before what depends on it)."""
        ids = []
        for name in names:
            record = self.nb.dcim.devices.get(name=name)
            if record is None:
                self.stats.errors += 1
                console.print(f"  [red]✗[/red] {label}: depends on '{name}', which is not in NetBox")
            else:
                ids.append(record.id)
        return ids

    # ------------------------------------------------------------------- IPs
    def _ips(self, cfg: dict[str, Any], site: Any, devices: list[tuple[DevicePlan, Any]]) -> None:
        interfaces = self._interfaces(site)
        tenant_id = self.ref("tenant", cfg["tenant"])
        counters: dict[tuple[int, str], int] = {}
        used: set[str] = set()

        for dev, record in devices:
            groups = interface_groups(self.plan["device_types"][dev.type_key])
            names = groups.get("mgmt") if dev.kind in ("firewall", "switch", "ap") else None
            names = names or groups.get("uplink") or []
            if not names:
                continue
            iface = interfaces.get((dev.name, names[0]))
            if iface is None:
                self.stats.errors += 1
                console.print(
                    f"  [red]✗[/red] {dev.name}: interface '{names[0]}' not found "
                    "(device type templates incomplete? re-run 'model')"
                )
                continue

            # Kinds sharing a VLAN (a server and an endpoint on CORP) would start on the same address.
            slot = counters.get((dev.vid, dev.kind), 0)
            address = f"10.{cfg['index']}.{dev.vid}.{IP_BASE[dev.kind] + slot}/24"
            while address in used:
                slot += 1
                address = f"10.{cfg['index']}.{dev.vid}.{IP_BASE[dev.kind] + slot}/24"
            counters[(dev.vid, dev.kind)] = slot + 1
            used.add(address)
            ip = upsert(
                self.nb.ipam.ip_addresses,
                {"address": address},
                {
                    "address": address,
                    "status": "active",
                    "assigned_object_type": "dcim.interface",
                    "assigned_object_id": iface.id,
                    "dns_name": f"{dev.name}.{self.domain}",
                    "tenant": tenant_id,
                    "tags": self.tags(),
                },
                self.stats,
                address,
                quiet=True,
            )
            primary = record.serialize().get("primary_ip4")
            if ip is not None and primary != ip.id:
                record.primary_ip4 = ip.id
                record.save()

    def _interfaces(self, site: Any) -> dict[tuple[str, str], Any]:
        return {
            (item.device.name, item.name): item
            for item in self.nb.dcim.interfaces.filter(site_id=site.id)
        }

    # ---------------------------------------------------------------- cables
    def _cables(self, site: Any, devices: list[tuple[DevicePlan, Any]]) -> None:
        """Firewall -> core switch -> downstream switches; APs/servers/PCs on access ports."""
        interfaces = self._interfaces(site)
        free: dict[str, dict[str, list[str]]] = {
            dev.name: interface_groups(self.plan["device_types"][dev.type_key])
            for dev, _ in devices
        }
        by_kind: dict[str, list[str]] = {}
        for dev, _ in devices:
            by_kind.setdefault(dev.kind, []).append(dev.name)

        def take(device: str, *groups: str) -> str | None:
            for group in groups:
                if free[device].get(group):
                    return free[device][group].pop(0)
            return None

        def link(a_dev: str, a_groups: tuple[str, ...], b_dev: str, b_groups: tuple[str, ...]):
            a_port, b_port = take(a_dev, *a_groups), take(b_dev, *b_groups)
            if not a_port or not b_port:
                console.print(f"  [yellow]![/yellow] no free port to cable {a_dev} <-> {b_dev}")
                return
            a_if, b_if = interfaces.get((a_dev, a_port)), interfaces.get((b_dev, b_port))
            if a_if is None or b_if is None:
                self.stats.errors += 1
                console.print(
                    f"  [red]✗[/red] cable {a_dev}:{a_port} <-> {b_dev}:{b_port}: interface not found"
                )
                return
            if a_if.cable or b_if.cable:
                self.stats.unchanged += 1
                return
            try:
                self.nb.dcim.cables.create(
                    a_terminations=[{"object_type": "dcim.interface", "object_id": a_if.id}],
                    b_terminations=[{"object_type": "dcim.interface", "object_id": b_if.id}],
                    status="connected",
                    tags=self.tags(),
                )
                self.stats.created += 1
            except Exception as exc:  # noqa: BLE001
                self.stats.errors += 1
                console.print(f"  [red]✗[/red] cable {a_dev}:{a_port} <-> {b_dev}:{b_port}: {exc}")

        switches = by_kind.get("switch", [])
        if not switches:
            return
        core = switches[0]
        for firewall in by_kind.get("firewall", []):
            link(firewall, ("lan",), core, ("uplink", "access"))
        for downstream in switches[1:]:
            link(core, ("uplink", "access"), downstream, ("uplink",))
        for index, ap in enumerate(by_kind.get("ap", [])):
            link(ap, ("uplink",), switches[index % len(switches)], ("access",))
        for server in by_kind.get("server", []):
            link(server, ("uplink",), core, ("access",))
        for endpoint in by_kind.get("endpoint", []):
            link(endpoint, ("uplink",), switches[-1], ("access",))

    # --------------------------------------------------------------- circuits
    def _circuits(self) -> None:
        """Providers, each site's internet circuits, and the cable to the firewall port."""
        if not self.plan.get("circuits"):
            return
        console.print("[cyan]Circuits[/cyan]")
        providers: dict[str, int] = {}
        for item in self.plan.get("providers", []):
            data = {
                "name": item["name"],
                "slug": item["slug"],
                "description": f"NOC {item['noc']}, account {item['account']}",
                "comments": (
                    f"NOC: {item['noc']}\n\nPortal: {item['portal']}\n\n"
                    f"Account: {item['account']}\n\nSLA: {item['sla']}"
                ),
                "tags": self.tags(),
            }
            record = upsert(self.nb.circuits.providers, {"slug": item["slug"]}, data, self.stats, item["name"])
            if record is not None:
                providers[item["slug"]] = record.id

        sites = {s["code"]: s for s in self.plan["sites"]}
        for item in self.plan["circuits"]:
            site = self.nb.dcim.sites.get(slug=item["site"])
            if site is None or item["provider"] not in providers:
                self.stats.errors += 1
                console.print(f"  [red]✗[/red] circuit {item['cid']}: site or provider missing")
                continue
            data = {
                "cid": item["cid"],
                "provider": providers[item["provider"]],
                "type": self.ref("circuit_type", item["type"]),
                "status": "active",
                "tenant": self.ref("tenant", sites[item["site"]]["tenant"]),
                "commit_rate": int(item["commit_mbps"]) * 1000,  # NetBox stores Kbps
                "description": f"{item['role'].capitalize()} internet for {site.name}",
                "tags": self.tags(),
            }
            circuit = upsert(self.nb.circuits.circuits, {"cid": item["cid"]}, data, self.stats, item["cid"])
            if circuit is None:
                continue
            self._termination(circuit, site, f"{item['port']} on {item['site']}-fw01")

    def _termination(self, circuit: Any, site: Any, lands_on: str) -> None:
        """The A end at the site. Not cabled: the core lab's topology scripts expect
        every cable to join two interfaces, so the firewall port goes in the description."""
        existing = self.nb.circuits.circuit_terminations.get(circuit_id=circuit.id, term_side="A")
        if existing is not None:
            if existing.description != lands_on:
                existing.update({"description": lands_on})
                self.stats.updated += 1
            else:
                self.stats.unchanged += 1
            return
        data: dict[str, Any] = {"circuit": circuit.id, "term_side": "A", "description": lands_on}
        if self.version >= (4, 2):
            data.update(termination_type="dcim.site", termination_id=site.id)
        else:
            data["site"] = site.id
        try:
            self.nb.circuits.circuit_terminations.create(data)
            self.stats.created += 1
        except Exception as exc:  # noqa: BLE001
            self.stats.errors += 1
            console.print(f"  [red]✗[/red] {circuit.cid} termination: {exc}")

    # ----------------------------------------------------------- fabric bridge
    def _link_fabric(self) -> None:
        """Give the core lab's DC1 site owners + a change window (adds data only)."""
        cfg = self.plan.get("fabric_link")
        if not cfg:
            return
        console.print(f"[cyan]Linking core fabric site '{cfg['site']}'[/cyan]")
        site = self.nb.dcim.sites.get(slug=cfg["site"])
        if site is None:
            console.print(
                f"  [yellow]![/yellow] site '{cfg['site']}' not found - run the core import first"
            )
            return
        current = site.serialize().get("custom_fields", {})
        wanted = cfg.get("custom_fields", {})
        if any(current.get(k) != v for k, v in wanted.items()):
            site.update({"custom_fields": wanted})
            self.stats.updated += 1
            console.print(f"  [blue]~[/blue] {cfg['site']} custom fields")
        self._assign(cfg.get("contacts", {}), "dcim.site", site.id, cfg["site"])
