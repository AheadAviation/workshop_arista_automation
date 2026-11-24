"""Resolve real-world Day 2 operation inputs against live NetBox data (INIT-005/SPEC-001).

Populates dropdown menus live from NetBox:
* VLANs: merged from `ipam.vlan` AND the `tenant-core` config context (`l2_segments`), so menus
  are populated immediately after Day-Zero `full_import.py` as well as after IPAM seeding.
* Leaf switches: `dcim.device` (filtered to fabric leaves `leaf-01..leaf-04`).
* Server-facing interfaces: `dcim.interface` per leaf (protecting Management0, Loopbacks,
  spine uplinks eth1/eth2, and MLAG peer-links Port-Channel2000 / eth3-eth4 if cabled to peer).
* MLAG pairs: from `workbench.catalogue.MLAG_PAIRS`.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from workbench import catalogue, operations

FABRIC_SPINES = ("spine-01", "spine-02")
FABRIC_LEAVES = ("leaf-01", "leaf-02", "leaf-03", "leaf-04")
ALL_FABRIC_DEVICES = FABRIC_SPINES + FABRIC_LEAVES

#: Clean MLAG pair labels for the Arista EVPN/VXLAN fabric.
MLAG_PAIR_LABELS: dict[str, str] = {
    "pair-1": "MLAG Pair 1 (leaf-01 + leaf-02)",
    "pair-2": "MLAG Pair 2 (leaf-03 + leaf-04)",
}

#: Default server-facing interfaces exposed per leaf when NetBox has eth1..eth8.
_PROTECTED_IFACE_PREFIXES = ("management", "loopback", "vlan", "vxlan", "port-channel2000")
_PROTECTED_IFACE_NAMES = {"eth1", "eth2", "ethernet1", "ethernet2", "eth3", "eth4", "ethernet3", "ethernet4"}

_VLAN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_SAFE_DESC_RE = re.compile(r"^[A-Za-z0-9 _./:#-]{0,64}$")


class ResolveError(RuntimeError):
    """Submitted values do not resolve to a valid Day 2 change."""


@dataclass(frozen=True)
class Resolved:
    """Validated inputs, target devices, and human-readable summary for planning/execution."""

    operation: operations.Operation
    vlan_vid: int
    vlan_name: str
    devices: list[str]
    values: Mapping[str, Any]
    sentence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation.to_dict(),
            "vlan": {"vid": self.vlan_vid, "name": self.vlan_name},
            "devices": list(self.devices),
            "values": dict(self.values),
            "sentence": self.sentence,
        }


VlanFetcher = Callable[[], list[dict[str, Any]]]
LeafFetcher = Callable[[], list[dict[str, Any]]]
InterfaceFetcher = Callable[[], dict[str, list[dict[str, str]]]]


def _netbox_client():
    from netbox_ops import conf
    from workbench.netbox_ipam_write import NetboxWriter

    return NetboxWriter(conf.netbox_url(), conf.netbox_token())


def _django_orm_ready() -> bool:
    """Return True when running inside NetBox's Django process with apps loaded."""
    try:
        from django.apps import apps

        return bool(apps.ready)
    except Exception:
        return False


def _fetch_vlans_live() -> list[dict[str, Any]]:
    """Read VLANs from NetBox IPAM and merge any tenant VLANs defined in `tenant-core` config context."""
    by_vid: dict[int, dict[str, Any]] = {}

    if _django_orm_ready():
        try:
            from extras.models import ConfigContext
            from ipam.models import VLAN

            for v in VLAN.objects.select_related("tenant").filter(vid__lt=4000):
                vid = int(v.vid)
                by_vid[vid] = {
                    "vid": vid,
                    "name": str(v.name or f"VLAN{vid}"),
                    "tenant": str(v.tenant.name) if v.tenant else "",
                    "description": str(v.description or ""),
                    "source": "ipam",
                }
            ctx = ConfigContext.objects.filter(name="tenant-core").first()
            if ctx and isinstance(ctx.data, dict):
                for tenant in ctx.data.get("tenants") or []:
                    t_name = str(tenant.get("name") or "CORE")
                    for seg in tenant.get("l2_segments") or []:
                        vid = int(seg.get("vlan_number") or 0)
                        if vid <= 1 or vid >= 4000 or vid in by_vid:
                            continue
                        by_vid[vid] = {
                            "vid": vid,
                            "name": str(seg.get("vlan_name") or f"VLAN{vid}"),
                            "tenant": t_name,
                            "description": str(seg.get("description") or ""),
                            "source": "config-context",
                        }
            return sorted(by_vid.values(), key=lambda v: v["vid"])
        except Exception:
            by_vid.clear()

    client = _netbox_client()

    # 1. Read IPAM VLANs
    try:
        payload = client.get("/api/ipam/vlans/", limit=500)
        for item in payload.get("results") or []:
            if not isinstance(item, dict) or item.get("vid") is None:
                continue
            vid = int(item["vid"])
            if vid >= 4000:
                continue  # Protect MLAG/infra VLANs
            tenant = item.get("tenant")
            by_vid[vid] = {
                "vid": vid,
                "name": str(item.get("name") or f"VLAN{vid}"),
                "tenant": (tenant or {}).get("name") if isinstance(tenant, dict) else "",
                "description": str(item.get("description") or ""),
                "source": "ipam",
            }
    except Exception:
        pass

    # 2. Merge tenant-core config context l2_segments so fresh Day-Zero imports have real VLAN choices
    try:
        ctx_payload = client.get("/api/extras/config-contexts/", name="tenant-core", limit=5)
        for ctx in ctx_payload.get("results") or []:
            data = ctx.get("data") or {}
            for tenant in data.get("tenants") or []:
                t_name = str(tenant.get("name") or "CORE")
                for seg in tenant.get("l2_segments") or []:
                    vid = int(seg.get("vlan_number") or 0)
                    if vid <= 1 or vid >= 4000:
                        continue
                    if vid not in by_vid:
                        by_vid[vid] = {
                            "vid": vid,
                            "name": str(seg.get("vlan_name") or f"VLAN{vid}"),
                            "tenant": t_name,
                            "description": str(seg.get("description") or ""),
                            "source": "config-context",
                        }
    except Exception:
        pass

    return sorted(by_vid.values(), key=lambda v: v["vid"])


def _fetch_leaves_live() -> list[dict[str, Any]]:
    """Read leaf switches from NetBox DCIM."""
    if _django_orm_ready():
        try:
            from dcim.models import Device

            found = [
                {"name": str(d.name), "role": str(d.role.slug if d.role else "")}
                for d in Device.objects.select_related("role").filter(name__in=FABRIC_LEAVES)
            ]
            if found:
                return sorted(found, key=lambda d: d["name"])
        except Exception:
            pass
    try:
        client = _netbox_client()
        payload = client.get("/api/dcim/devices/", limit=100)
        found = [
            {"name": str(d["name"]), "role": str((d.get("role") or {}).get("slug") or "")}
            for d in (payload.get("results") or [])
            if str(d.get("name") or "") in FABRIC_LEAVES
        ]
        if found:
            return sorted(found, key=lambda d: d["name"])
    except Exception:
        pass
    return [{"name": d, "role": "leaf"} for d in FABRIC_LEAVES]


def _normalize_eos_interface(name: str) -> str:
    """Normalize NetBox `eth5` -> Arista EOS `Ethernet5`."""
    m = re.match(r"^eth(\d+(?:/\d+)*)$", name.strip(), re.IGNORECASE)
    if m:
        return f"Ethernet{m.group(1)}"
    return name.strip()


def _is_server_facing(name: str) -> bool:
    low = name.strip().lower()
    if any(low.startswith(p) for p in _PROTECTED_IFACE_PREFIXES):
        return False
    if low in _PROTECTED_IFACE_NAMES:
        return False
    return low.startswith("eth") or low.startswith("ethernet")


def _fetch_interfaces_live() -> dict[str, list[dict[str, str]]]:
    """Read server-facing interfaces for all leaf switches from NetBox DCIM in a single query."""
    by_device: dict[str, list[dict[str, str]]] = {}
    leaf_set = set(FABRIC_LEAVES)

    if _django_orm_ready():
        try:
            from dcim.models import Interface

            qs = Interface.objects.select_related("device").filter(device__name__in=FABRIC_LEAVES)
            for iface in qs:
                raw_name = str(iface.name or "")
                if not _is_server_facing(raw_name):
                    continue
                eos_name = _normalize_eos_interface(raw_name)
                desc = str(iface.description or "").strip()
                label = f"{eos_name} ({desc})" if desc else eos_name
                by_device.setdefault(str(iface.device.name), []).append(
                    {"value": eos_name, "label": label}
                )
        except Exception:
            by_device.clear()

    if not by_device:
        try:
            client = _netbox_client()
            payload = client.get("/api/dcim/interfaces/", limit=500)
            for iface in payload.get("results") or []:
                dev_obj = iface.get("device") or {}
                dev = str(dev_obj.get("name") or "") if isinstance(dev_obj, dict) else ""
                if dev not in leaf_set:
                    continue
                raw_name = str(iface.get("name") or "")
                if not _is_server_facing(raw_name):
                    continue
                eos_name = _normalize_eos_interface(raw_name)
                desc = str(iface.get("description") or "").strip()
                label = f"{eos_name} ({desc})" if desc else eos_name
                by_device.setdefault(dev, []).append({"value": eos_name, "label": label})
        except Exception:
            pass

    # Fallback to Ethernet5..Ethernet8 if NetBox had no server-facing interfaces yet
    for dev in FABRIC_LEAVES:
        if dev not in by_device:
            by_device[dev] = [
                {"value": f"Ethernet{n}", "label": f"Ethernet{n}"} for n in (5, 6, 7, 8)
            ]
    return by_device


def field_choices(
    field: operations.Field,
    vlans: list[dict[str, Any]],
    *,
    leaves: list[dict[str, Any]] | None = None,
    interfaces_by_device: dict[str, list[dict[str, str]]] | None = None,
) -> list[dict[str, str]]:
    """Build `{value, label}` options for one field."""
    if field.source == "ipam.vlan":
        return [
            {
                "value": str(v["vid"]),
                "label": f"VLAN {v['vid']} — {v['name']}"
                + (f" ({v['tenant']})" if v.get("tenant") else ""),
            }
            for v in vlans
        ]

    if field.source == "dcim.leaf":
        items = leaves if leaves is not None else [{"name": d} for d in FABRIC_LEAVES]
        return [{"value": d["name"], "label": d["name"]} for d in items]

    if field.source == "dcim.interface":
        mapping = interfaces_by_device or {}
        first_dev = FABRIC_LEAVES[0]
        return list(mapping.get(first_dev, [{"value": f"Ethernet{n}", "label": f"Ethernet{n}"} for n in (5, 6, 7, 8)]))

    if field.source == "mlag.pair":
        return [
            {"value": key, "label": MLAG_PAIR_LABELS.get(key, key)}
            for key in catalogue.MLAG_PAIRS
        ]

    if field.source == "static":
        return [{"value": v, "label": lbl} for v, lbl in field.choices]

    return []


def describe_all(
    *,
    fetch_vlans: VlanFetcher | None = None,
    fetch_leaves: LeafFetcher | None = None,
    fetch_interfaces: InterfaceFetcher | None = None,
) -> dict[str, Any]:
    """Return every operation with live NetBox choices for the UI."""
    vlans = (fetch_vlans or _fetch_vlans_live)()
    leaves = (fetch_leaves or _fetch_leaves_live)()
    ifaces = (fetch_interfaces or _fetch_interfaces_live)()

    ops_out: list[dict[str, Any]] = []
    for op in operations.OPERATIONS:
        op_dict = op.to_dict()
        enriched_fields = []
        for f, f_dict in zip(op.fields, op_dict["fields"]):
            f_dict = dict(f_dict)
            if f.source in ("ipam.vlan", "dcim.leaf", "dcim.interface", "mlag.pair", "static"):
                f_dict["choices"] = field_choices(
                    f, vlans, leaves=leaves, interfaces_by_device=ifaces
                )
            if f.source == "dcim.interface":
                f_dict["choices_by_device"] = ifaces
            enriched_fields.append(f_dict)
        op_dict["fields"] = enriched_fields
        ops_out.append(op_dict)

    return {
        "ok": True,
        "operations": ops_out,
        "vlans": vlans,
        "leaves": [d["name"] for d in leaves],
        "interfaces_by_device": ifaces,
    }


def _require_existing_vlan(raw: Any, vlans: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        vid = int(str(raw or "").strip())
    except ValueError as exc:
        raise ResolveError("Choose a VLAN from the NetBox list.") from exc
    for v in vlans:
        if int(v["vid"]) == vid:
            return v
    raise ResolveError(
        f"VLAN {vid} is not in NetBox. Provision it first or pick an existing VLAN."
    )


def _validate_description(raw: Any) -> str:
    text = str(raw or "").strip()
    if not _SAFE_DESC_RE.match(text):
        raise ResolveError("Description may only contain letters, numbers, spaces, and _ . / : # - (max 64 chars).")
    return text


def resolve(
    *,
    operation_key: str,
    values: Mapping[str, Any],
    fetch_vlans: VlanFetcher | None = None,
) -> Resolved:
    """Validate and resolve submitted form values into a concrete Day 2 plan target."""
    try:
        op = operations.operation(operation_key)
    except operations.UnknownOperation as exc:
        raise ResolveError(str(exc)) from exc

    vlans = (fetch_vlans or _fetch_vlans_live)()

    if op.kind == "provision_vlan":
        try:
            vid = int(str(values.get("vlan_id") or "").strip())
        except ValueError as exc:
            raise ResolveError("VLAN ID must be an integer between 2 and 4000.") from exc
        if vid < 2 or vid > 4000:
            raise ResolveError("VLAN ID must be between 2 and 4000 (4001+ are reserved for MLAG/infra).")
        vlan_name = str(values.get("vlan_name") or "").strip()
        if not _VLAN_NAME_RE.match(vlan_name):
            raise ResolveError("VLAN Name must be 1–32 alphanumeric/underscore/hyphen characters with no spaces.")
        svi_ip = str(values.get("svi_ip") or "").strip()
        if svi_ip:
            try:
                ipaddress.IPv4Interface(svi_ip)
                if "/" not in svi_ip:
                    raise ValueError("missing prefix")
            except ValueError as exc:
                raise ResolveError("Anycast Gateway IP must be IPv4 with prefix length, e.g. 10.100.150.1/24.") from exc
        mtu = str(values.get("mtu") or "9000").strip()
        if mtu not in ("1500", "9000"):
            raise ResolveError("SVI MTU must be 1500 or 9000.")
        vni = 10000 + vid
        svi_note = f" with SVI {svi_ip} (MTU {mtu}) in VRF CORE" if svi_ip else " (L2-only VXLAN)"
        sentence = op.sentence.format(vlan_id=vid, vlan_name=vlan_name, vni=vni, svi_note=svi_note)
        return Resolved(
            operation=op,
            vlan_vid=vid,
            vlan_name=vlan_name,
            devices=list(FABRIC_LEAVES),
            values={"vlan_id": vid, "vlan_name": vlan_name, "vni": vni, "svi_ip": svi_ip, "mtu": int(mtu)},
            sentence=sentence,
        )

    if op.kind == "configure_port":
        device = str(values.get("device") or "").strip()
        if device not in FABRIC_LEAVES:
            raise ResolveError(f"Choose a valid leaf switch ({', '.join(FABRIC_LEAVES)}).")
        interface = _normalize_eos_interface(str(values.get("interface") or ""))
        if not interface or not _is_server_facing(interface):
            raise ResolveError(
                f"{interface or 'Interface'} is not an allowed server-facing port. "
                "Uplinks (Ethernet1–2) and MLAG peer-links (Ethernet3–4, Port-Channel2000) are protected."
            )
        mode = str(values.get("mode") or "access").strip().lower()
        if mode not in ("access", "trunk"):
            raise ResolveError("Switchport mode must be 'access' or 'trunk'.")
        vlan_obj = _require_existing_vlan(values.get("vlan"), vlans)
        mtu = str(values.get("mtu") or "9214").strip()
        if mtu not in ("1500", "9214"):
            raise ResolveError("Interface MTU must be 1500 or 9214.")
        admin_state = str(values.get("admin_state") or "up").strip().lower()
        if admin_state not in ("up", "down"):
            raise ResolveError("Admin state must be 'up' or 'down'.")
        desc = _validate_description(values.get("description"))
        sentence = op.sentence.format(
            device=device,
            interface=interface,
            mode=mode,
            vlan=f"{vlan_obj['vid']} ({vlan_obj['name']})",
            mtu=mtu,
            admin_state="enabled" if admin_state == "up" else "shutdown",
        )
        return Resolved(
            operation=op,
            vlan_vid=int(vlan_obj["vid"]),
            vlan_name=vlan_obj["name"],
            devices=[device],
            values={
                "device": device,
                "interface": interface,
                "mode": mode,
                "vlan": int(vlan_obj["vid"]),
                "vlan_name": vlan_obj["name"],
                "mtu": int(mtu),
                "admin_state": admin_state,
                "description": desc,
            },
            sentence=sentence,
        )

    if op.kind == "provision_mlag_portchannel":
        pair = str(values.get("pair") or "").strip()
        if pair not in catalogue.MLAG_PAIRS:
            raise ResolveError("Choose an MLAG pair.")
        try:
            po_id = int(str(values.get("po_id") or "").strip())
        except ValueError as exc:
            raise ResolveError("Port-Channel ID must be an integer between 1 and 1999.") from exc
        if po_id < 1 or po_id >= 2000:
            raise ResolveError("Port-Channel ID must be between 1 and 1999 (2000 is the MLAG peer-link).")
        member = _normalize_eos_interface(str(values.get("member_interface") or "Ethernet5"))
        if not _is_server_facing(member):
            raise ResolveError(f"{member} is not a server-facing interface.")
        vlan_obj = _require_existing_vlan(values.get("vlan"), vlans)
        mtu = str(values.get("mtu") or "9214").strip()
        if mtu not in ("1500", "9214"):
            raise ResolveError("MTU must be 1500 or 9214.")
        desc = _validate_description(values.get("description"))
        pair_label = MLAG_PAIR_LABELS.get(pair, pair)
        devices = list(catalogue.MLAG_PAIRS[pair])
        sentence = op.sentence.format(
            po_id=po_id,
            pair_label=pair_label,
            member_interface=member,
            vlan=f"{vlan_obj['vid']} ({vlan_obj['name']})",
            mtu=mtu,
        )
        return Resolved(
            operation=op,
            vlan_vid=int(vlan_obj["vid"]),
            vlan_name=vlan_obj["name"],
            devices=devices,
            values={
                "pair": pair,
                "pair_label": pair_label,
                "po_id": po_id,
                "member_interface": member,
                "vlan": int(vlan_obj["vid"]),
                "vlan_name": vlan_obj["name"],
                "mtu": int(mtu),
                "description": desc,
            },
            sentence=sentence,
        )

    if op.kind == "update_fabric_services":
        svc = str(values.get("service_type") or "ntp").strip().lower()
        if svc not in ("ntp", "dns"):
            raise ResolveError("Service type must be 'ntp' or 'dns'.")
        action = str(values.get("action") or "add").strip().lower()
        if action not in ("add", "remove"):
            raise ResolveError("Action must be 'add' or 'remove'.")
        server_ip = str(values.get("server_ip") or "").strip()
        try:
            ipaddress.IPv4Address(server_ip)
        except ValueError as exc:
            raise ResolveError("Server address must be a valid IPv4 address (e.g. 172.30.30.10).") from exc
        sentence = op.sentence.format(
            action_word="Add" if action == "add" else "Remove",
            service_type=svc.upper(),
            server_ip=server_ip,
        )
        return Resolved(
            operation=op,
            vlan_vid=0,
            vlan_name="",
            devices=list(ALL_FABRIC_DEVICES),
            values={"service_type": svc, "action": action, "server_ip": server_ip},
            sentence=sentence,
        )

    if op.kind == "retire_vlan":
        vlan_obj = _require_existing_vlan(values.get("vlan"), vlans)
        vid = int(vlan_obj["vid"])
        if vid >= 4000:
            raise ResolveError("Infrastructure MLAG VLANs (4000+) cannot be retired here.")
        sentence = op.sentence.format(vlan=f"{vid} ({vlan_obj['name']})")
        return Resolved(
            operation=op,
            vlan_vid=vid,
            vlan_name=vlan_obj["name"],
            devices=list(FABRIC_LEAVES),
            values={"vlan": vid, "vlan_name": vlan_obj["name"]},
            sentence=sentence,
        )

    if op.kind == "remediate_drift":
        target = str(values.get("target") or "all-leaves").strip()
        if target == "all-leaves":
            devices = list(FABRIC_LEAVES)
            target_label = "all 4 leaves"
        elif target == "all-fabric":
            devices = list(ALL_FABRIC_DEVICES)
            target_label = "all 6 fabric switches"
        elif target in catalogue.MLAG_PAIRS:
            devices = list(catalogue.MLAG_PAIRS[target])
            target_label = MLAG_PAIR_LABELS.get(target, target)
        else:
            raise ResolveError("Choose a valid target scope.")
        sentence = op.sentence.format(target_label=target_label)
        return Resolved(
            operation=op,
            vlan_vid=0,
            vlan_name="",
            devices=devices,
            values={"target": target, "target_label": target_label},
            sentence=sentence,
        )

    raise ResolveError(f"Unsupported operation kind {op.kind!r}")


__all__ = [
    "ALL_FABRIC_DEVICES",
    "FABRIC_LEAVES",
    "FABRIC_SPINES",
    "ResolveError",
    "Resolved",
    "describe_all",
    "field_choices",
    "resolve",
]
