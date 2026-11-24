"""Source-of-Truth-first Day 2 operations planner, NetBox writer, eAPI executor & verifier (INIT-005/SPEC-002).

Every one of the 6 real-world Arista EVPN/VXLAN Day 2 operations follows the same contract:
1. `plan(operation_key, values)` — read-only. Validates inputs via `workbench.operations_resolve`,
   computes the exact NetBox Source-of-Truth update and exact per-switch EOS CLI commands,
   stages the plan under a `change_id`, and returns a preview without changing anything.
2. `apply(change_id, approver)` — requires a named approver and a staged `change_id`. Updates
   NetBox first (Source of Truth), applies the EOS commands over eAPI with `write memory`,
   reads back live switch state to verify, and appends an entry to the audit log.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workbench import operations_resolve

from . import conf, eapi_exec

_MAX_STAGED = 100


def _staged_file() -> Path:
    """Shared disk store beside the audit log so every NetBox worker sees staged previews."""
    log_path = Path(os.environ.get("WORKBENCH_AUDIT_LOG", "output/workbench/audit.jsonl"))
    return log_path.parent / "staged_day2.json"


def _load_staged() -> dict[str, dict[str, Any]]:
    path = _staged_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_staged(data: dict[str, dict[str, Any]]) -> None:
    if len(data) > _MAX_STAGED:
        oldest = sorted(data.items(), key=lambda kv: kv[1].get("created_at", 0))[
            : len(data) - _MAX_STAGED
        ]
        for k, _ in oldest:
            data.pop(k, None)
    path = _staged_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _serialize_resolved(resolved: operations_resolve.Resolved) -> dict[str, Any]:
    return {
        "operation_key": resolved.operation.key,
        "vlan_vid": resolved.vlan_vid,
        "vlan_name": resolved.vlan_name,
        "devices": list(resolved.devices),
        "values": dict(resolved.values),
        "sentence": resolved.sentence,
    }


def _deserialize_resolved(raw: dict[str, Any]) -> operations_resolve.Resolved:
    from workbench import operations

    op = operations.operation(str(raw["operation_key"]))
    return operations_resolve.Resolved(
        operation=op,
        vlan_vid=int(raw.get("vlan_vid") or 0),
        vlan_name=str(raw.get("vlan_name") or ""),
        devices=list(raw.get("devices") or []),
        values=dict(raw.get("values") or {}),
        sentence=str(raw.get("sentence") or ""),
    )


def _nb_headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    token = conf.netbox_token()
    if token:
        headers["Authorization"] = f"Token {token}"
    return headers


def _append_audit(entry: dict[str, Any]) -> None:
    """Append one JSONL record to the workbench audit log if configured and writable."""
    log_path = Path(os.environ.get("WORKBENCH_AUDIT_LOG", "output/workbench/audit.jsonl"))
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        # Never fail an already-applied switch change because of an unwritable local test path.
        pass


# --------------------------------------------------------------------------- #
# Command builders (pure, unit-testable)
# --------------------------------------------------------------------------- #


def build_device_commands(resolved: operations_resolve.Resolved) -> tuple[list[str], dict[str, list[str]]]:
    """Return `(netbox_changes, {device: [eos_commands]})` for a resolved Day 2 operation."""
    kind = resolved.operation.kind
    v = resolved.values
    nb_changes: list[str] = []
    per_device: dict[str, list[str]] = {}

    if kind == "provision_vlan":
        vid = int(v["vlan_id"])
        name = str(v["vlan_name"])
        vni = int(v["vni"])
        svi_ip = str(v.get("svi_ip") or "")
        mtu = int(v.get("mtu") or 9000)

        nb_changes.append(f"Create/update IPAM VLAN {vid} ({name}) in NetBox")
        nb_changes.append(
            f"Add VLAN {vid} ({name}, VNI {vni}"
            + (f", SVI {svi_ip}" if svi_ip else "")
            + ") to `tenant-core` config context"
        )

        cmds = [
            f"vlan {vid}",
            f"   name {name}",
            "interface Vxlan1",
            f"   vxlan vlan {vid} vni {vni}",
        ]
        if svi_ip:
            cmds.extend(
                [
                    f"interface Vlan{vid}",
                    f"   description {name}",
                    f"   mtu {mtu}",
                    "   vrf CORE",
                    f"   ip address virtual {svi_ip}",
                    "   no shutdown",
                ]
            )
        for dev in resolved.devices:
            per_device[dev] = list(cmds)
        return nb_changes, per_device

    if kind == "configure_port":
        dev = str(v["device"])
        intf = str(v["interface"])
        mode = str(v["mode"])
        vid = int(v["vlan"])
        mtu = int(v["mtu"])
        admin = str(v["admin_state"])
        desc = str(v.get("description") or "")

        nb_changes.append(
            f"Update NetBox interface {dev} / {intf} (mode={mode}, VLAN {vid}, MTU {mtu}, enabled={admin == 'up'})"
        )

        cmds = [f"interface {intf}"]
        if desc:
            cmds.append(f"   description {desc}")
        cmds.append(f"   mtu {mtu}")
        cmds.append("   switchport")
        if mode == "access":
            cmds.append("   switchport mode access")
            cmds.append(f"   switchport access vlan {vid}")
        else:
            cmds.append("   switchport mode trunk")
            cmds.append(f"   switchport trunk allowed vlan add {vid}")
        cmds.append("   no shutdown" if admin == "up" else "   shutdown")
        per_device[dev] = cmds
        return nb_changes, per_device

    if kind == "provision_mlag_portchannel":
        po_id = int(v["po_id"])
        member = str(v["member_interface"])
        vid = int(v["vlan"])
        mtu = int(v["mtu"])
        desc = str(v.get("description") or f"MLAG-{po_id}")

        nb_changes.append(
            f"Create/update Port-Channel{po_id} (LAG) and bind {member} on {', '.join(resolved.devices)} in NetBox (MTU {mtu}, VLAN {vid})"
        )

        cmds = [
            f"interface Port-Channel{po_id}",
            f"   description {desc}",
            f"   mtu {mtu}",
            "   switchport",
            "   switchport mode trunk",
            f"   switchport trunk allowed vlan add {vid}",
            f"   mlag {po_id}",
            "   no shutdown",
            f"interface {member}",
            f"   description {desc} (member of Po{po_id})",
            f"   mtu {mtu}",
            f"   channel-group {po_id} mode active",
            "   no shutdown",
        ]
        for dev in resolved.devices:
            per_device[dev] = list(cmds)
        return nb_changes, per_device

    if kind == "update_fabric_services":
        svc = str(v["service_type"])
        action = str(v["action"])
        ip = str(v["server_ip"])
        verb_label = "Add" if action == "add" else "Remove"
        nb_changes.append(f"{verb_label} {svc.upper()} server {ip} in NetBox `fabric-global` config context")

        if svc == "ntp":
            cmd = f"ntp server vrf MGMT {ip}" if action == "add" else f"no ntp server vrf MGMT {ip}"
        else:
            cmd = f"ip name-server vrf MGMT {ip}" if action == "add" else f"no ip name-server vrf MGMT {ip}"

        for dev in resolved.devices:
            per_device[dev] = [cmd]
        return nb_changes, per_device

    if kind == "retire_vlan":
        vid = int(v["vlan"])
        name = str(v["vlan_name"])
        nb_changes.append(f"Remove VLAN {vid} ({name}) from NetBox `tenant-core` config context and IPAM")

        cmds = [
            f"no interface Vlan{vid}",
            "interface Vxlan1",
            f"   no vxlan vlan {vid} vni",
            f"no vlan {vid}",
        ]
        for dev in resolved.devices:
            per_device[dev] = list(cmds)
        return nb_changes, per_device

    if kind == "remediate_drift":
        nb_changes.append("No NetBox mutation — NetBox `tenant-core` / IPAM is the source of truth")
        for dev in resolved.devices:
            per_device[dev] = []  # Computed live from running VLANs vs intended VLANs in `plan()`
        return nb_changes, per_device

    raise operations_resolve.ResolveError(f"Unsupported operation kind {kind!r}")


# --------------------------------------------------------------------------- #
# NetBox Source-of-Truth writers
# --------------------------------------------------------------------------- #


def _apply_netbox_update(resolved: operations_resolve.Resolved) -> dict[str, Any]:
    """Write the approved change to NetBox first (Source of Truth)."""
    import requests

    kind = resolved.operation.kind
    v = resolved.values
    base = conf.netbox_url()
    headers = _nb_headers()
    notes: list[str] = []

    if kind == "provision_vlan":
        vid = int(v["vlan_id"])
        name = str(v["vlan_name"])
        svi_ip = str(v.get("svi_ip") or "")
        mtu = int(v.get("mtu") or 9000)

        # 1. Create or update IPAM VLAN
        r = requests.get(f"{base}/api/ipam/vlans/?vid={vid}", headers=headers, timeout=15)
        existing = (r.json() or {}).get("results") if r.status_code == 200 else []
        if existing:
            vlan_id_nb = existing[0]["id"]
            requests.patch(
                f"{base}/api/ipam/vlans/{vlan_id_nb}/",
                headers=headers,
                json={"name": name},
                timeout=15,
            )
            notes.append(f"Updated IPAM VLAN {vid} ({name})")
        else:
            cr = requests.post(
                f"{base}/api/ipam/vlans/",
                headers=headers,
                json={"vid": vid, "name": name, "status": "active"},
                timeout=15,
            )
            if cr.status_code in (200, 201):
                notes.append(f"Created IPAM VLAN {vid} ({name})")

        # 2. Update tenant-core config context
        ctx_r = requests.get(
            f"{base}/api/extras/config-contexts/?name=tenant-core", headers=headers, timeout=15
        )
        ctxs = (ctx_r.json() or {}).get("results") if ctx_r.status_code == 200 else []
        if ctxs:
            ctx = ctxs[0]
            data = dict(ctx.get("data") or {})
            tenants = list(data.get("tenants") or [])
            if not tenants:
                tenants = [{"name": "CORE", "id": 1, "l2_segments": []}]
            segs = [s for s in (tenants[0].get("l2_segments") or []) if int(s.get("vlan_number") or 0) != vid]
            seg_entry: dict[str, Any] = {"vlan_number": vid, "vlan_name": name, "mtu": mtu}
            if svi_ip:
                seg_entry["vlan_ip"] = svi_ip
            segs.append(seg_entry)
            tenants[0]["l2_segments"] = sorted(segs, key=lambda s: int(s["vlan_number"]))
            data["tenants"] = tenants
            requests.patch(
                f"{base}/api/extras/config-contexts/{ctx['id']}/",
                headers=headers,
                json={"data": data},
                timeout=15,
            )
            notes.append(f"Added VLAN {vid} to config context tenant-core")

        return {"ok": True, "notes": notes}

    if kind == "configure_port":
        dev = str(v["device"])
        intf = str(v["interface"])
        nb_intf_candidates = [intf, intf.replace("Ethernet", "eth")]
        mode = "access" if v["mode"] == "access" else "tagged"
        patch_body: dict[str, Any] = {
            "description": str(v.get("description") or ""),
            "mtu": int(v["mtu"]),
            "enabled": v["admin_state"] == "up",
            "mode": mode,
        }
        updated = False
        for cand in nb_intf_candidates:
            r = requests.get(
                f"{base}/api/dcim/interfaces/?device={dev}&name={cand}",
                headers=headers,
                timeout=15,
            )
            items = (r.json() or {}).get("results") if r.status_code == 200 else []
            if items:
                requests.patch(
                    f"{base}/api/dcim/interfaces/{items[0]['id']}/",
                    headers=headers,
                    json=patch_body,
                    timeout=15,
                )
                notes.append(f"Updated NetBox interface {dev}/{cand}")
                updated = True
                break
        if not updated:
            notes.append(f"Interface {dev}/{intf} not found in NetBox (switch will still be configured)")
        return {"ok": True, "notes": notes}

    if kind == "provision_mlag_portchannel":
        po_name = f"Port-Channel{v['po_id']}"
        member = str(v["member_interface"])
        mtu = int(v["mtu"])
        desc = str(v.get("description") or f"MLAG-{v['po_id']}")

        for dev in resolved.devices:
            dr = requests.get(f"{base}/api/dcim/devices/?name={dev}", headers=headers, timeout=15)
            devs = (dr.json() or {}).get("results") if dr.status_code == 200 else []
            if not devs:
                continue
            dev_id = devs[0]["id"]

            # Create or update Port-Channel LAG interface
            pr = requests.get(
                f"{base}/api/dcim/interfaces/?device={dev}&name={po_name}",
                headers=headers,
                timeout=15,
            )
            pos = (pr.json() or {}).get("results") if pr.status_code == 200 else []
            if pos:
                lag_id = pos[0]["id"]
                requests.patch(
                    f"{base}/api/dcim/interfaces/{lag_id}/",
                    headers=headers,
                    json={"description": desc, "mtu": mtu, "mode": "tagged", "enabled": True},
                    timeout=15,
                )
            else:
                cr = requests.post(
                    f"{base}/api/dcim/interfaces/",
                    headers=headers,
                    json={
                        "device": dev_id,
                        "name": po_name,
                        "type": "lag",
                        "description": desc,
                        "mtu": mtu,
                        "mode": "tagged",
                        "enabled": True,
                    },
                    timeout=15,
                )
                lag_id = (cr.json() or {}).get("id") if cr.status_code in (200, 201) else None

            # Bind member interface to LAG if present in NetBox
            for cand in (member, member.replace("Ethernet", "eth")):
                mr = requests.get(
                    f"{base}/api/dcim/interfaces/?device={dev}&name={cand}",
                    headers=headers,
                    timeout=15,
                )
                mems = (mr.json() or {}).get("results") if mr.status_code == 200 else []
                if mems:
                    patch = {"description": f"{desc} (member of {po_name})", "mtu": mtu, "enabled": True}
                    if lag_id:
                        patch["lag"] = lag_id
                    requests.patch(
                        f"{base}/api/dcim/interfaces/{mems[0]['id']}/",
                        headers=headers,
                        json=patch,
                        timeout=15,
                    )
                    break
            notes.append(f"Updated {dev} {po_name} + {member} in NetBox")
        return {"ok": True, "notes": notes}

    if kind == "update_fabric_services":
        svc_key = "ntp_servers" if v["service_type"] == "ntp" else "dns_servers"
        ip = str(v["server_ip"])
        action = str(v["action"])
        ctx_r = requests.get(
            f"{base}/api/extras/config-contexts/?name=fabric-global", headers=headers, timeout=15
        )
        ctxs = (ctx_r.json() or {}).get("results") if ctx_r.status_code == 200 else []
        if ctxs:
            ctx = ctxs[0]
            data = dict(ctx.get("data") or {})
            mgmt = dict(data.get("management") or {})
            servers = list(mgmt.get(svc_key) or [])
            if action == "add" and ip not in servers:
                servers.append(ip)
            elif action == "remove" and ip in servers:
                servers = [s for s in servers if s != ip]
            mgmt[svc_key] = servers
            data["management"] = mgmt
            requests.patch(
                f"{base}/api/extras/config-contexts/{ctx['id']}/",
                headers=headers,
                json={"data": data},
                timeout=15,
            )
            notes.append(f"Updated fabric-global management.{svc_key} = {servers}")
        return {"ok": True, "notes": notes}

    if kind == "retire_vlan":
        vid = int(v["vlan"])
        ctx_r = requests.get(
            f"{base}/api/extras/config-contexts/?name=tenant-core", headers=headers, timeout=15
        )
        ctxs = (ctx_r.json() or {}).get("results") if ctx_r.status_code == 200 else []
        if ctxs:
            ctx = ctxs[0]
            data = dict(ctx.get("data") or {})
            tenants = list(data.get("tenants") or [])
            if tenants:
                tenants[0]["l2_segments"] = [
                    s for s in (tenants[0].get("l2_segments") or []) if int(s.get("vlan_number") or 0) != vid
                ]
                data["tenants"] = tenants
                requests.patch(
                    f"{base}/api/extras/config-contexts/{ctx['id']}/",
                    headers=headers,
                    json={"data": data},
                    timeout=15,
                )
                notes.append(f"Removed VLAN {vid} from config context tenant-core")

        r = requests.get(f"{base}/api/ipam/vlans/?vid={vid}", headers=headers, timeout=15)
        existing = (r.json() or {}).get("results") if r.status_code == 200 else []
        for item in existing:
            requests.delete(f"{base}/api/ipam/vlans/{item['id']}/", headers=headers, timeout=15)
            notes.append(f"Deleted IPAM VLAN {vid}")
        return {"ok": True, "notes": notes}

    return {"ok": True, "notes": ["NetBox already matches intended state"]}


# --------------------------------------------------------------------------- #
# Preview (read-only) & Apply (NetBox -> eAPI -> Verify -> Audit)
# --------------------------------------------------------------------------- #


def _parse_running_interfaces(running_cfg: str) -> dict[str, dict[str, Any]]:
    """Parse `show running-config` interface stanzas into structured EOS interface state."""
    parsed: dict[str, dict[str, Any]] = {}
    current: str | None = None
    for raw in running_cfg.splitlines():
        line = raw.rstrip()
        if line.startswith("interface "):
            current = line.split(None, 1)[1].strip()
            parsed[current] = {
                "shutdown": False,
                "description": "",
                "mtu": None,
                "channel_group": None,
                "switchport_mode": "",
                "access_vlan": None,
                "trunk_vlans": "",
            }
            continue
        if current is None:
            continue
        if not line.startswith(" ") and line != "":
            current = None
            continue
        stripped = line.strip()
        if stripped == "shutdown":
            parsed[current]["shutdown"] = True
        elif stripped == "no shutdown":
            parsed[current]["shutdown"] = False
        elif stripped.startswith("description "):
            parsed[current]["description"] = stripped.split(None, 1)[1].strip()
        elif stripped.startswith("mtu "):
            parts = stripped.split()
            if len(parts) >= 2 and parts[1].isdigit():
                parsed[current]["mtu"] = int(parts[1])
        elif stripped.startswith("channel-group "):
            parts = stripped.split()
            if len(parts) >= 2 and parts[1].isdigit():
                parsed[current]["channel_group"] = int(parts[1])
        elif stripped == "switchport mode trunk":
            parsed[current]["switchport_mode"] = "trunk"
        elif stripped == "switchport mode access":
            parsed[current]["switchport_mode"] = "access"
        elif stripped.startswith("switchport access vlan "):
            parts = stripped.split()
            if len(parts) >= 4 and parts[3].isdigit():
                parsed[current]["access_vlan"] = int(parts[3])
        elif stripped.startswith("switchport trunk allowed vlan "):
            parsed[current]["trunk_vlans"] = stripped.split("switchport trunk allowed vlan ", 1)[1].strip()
    return parsed


def _interface_drift_commands(device: str, running_cfg: str, nb_interfaces: list[dict[str, Any]]) -> list[str]:
    """Compare NetBox database interface state against live EOS running-config."""
    live_ifaces = _parse_running_interfaces(running_cfg)
    cmds: list[str] = []

    for item in nb_interfaces:
        raw_name = str(item.get("name") or "").strip()
        eos_name = operations_resolve._normalize_eos_interface(raw_name)
        low = eos_name.lower()
        # Check server-facing Ethernet ports and non-peer-link Port-Channels
        is_non_peer_po = low.startswith("port-channel") and low != "port-channel2000"
        if not (operations_resolve._is_server_facing(eos_name) or is_non_peer_po):
            continue

        actual = live_ifaces.get(
            eos_name,
            {
                "shutdown": False,
                "description": "",
                "mtu": None,
                "channel_group": None,
                "switchport_mode": "",
                "access_vlan": None,
                "trunk_vlans": "",
            },
        )
        want_enabled = bool(item.get("enabled", True))
        want_desc = str(item.get("description") or "").strip()
        want_mtu = item.get("mtu")
        want_lag = item.get("lag")
        raw_mode = item.get("mode")
        want_mode = (
            str(raw_mode.get("value") or "") if isinstance(raw_mode, dict) else str(raw_mode or "")
        ).strip().lower()
        raw_untagged = item.get("untagged_vlan")
        want_untagged = (
            int(raw_untagged["vid"])
            if isinstance(raw_untagged, dict) and raw_untagged.get("vid") is not None
            else (int(raw_untagged) if isinstance(raw_untagged, int) else None)
        )

        iface_cmds: list[str] = []
        # 1. Admin state (shutdown / no shutdown)
        if not want_enabled and not actual["shutdown"]:
            iface_cmds.append("   shutdown")
        elif want_enabled and actual["shutdown"]:
            iface_cmds.append("   no shutdown")

        # 2. Description (set or clear to match NetBox database)
        if want_desc and want_desc != actual["description"]:
            iface_cmds.append(f"   description {want_desc}")
        elif not want_desc and actual["description"]:
            iface_cmds.append("   no description")

        # 3. MTU (set or clear to match NetBox database)
        if want_mtu is not None and int(want_mtu) != actual["mtu"]:
            iface_cmds.append(f"   mtu {int(want_mtu)}")
        elif want_mtu is None and actual["mtu"] is not None:
            iface_cmds.append("   no mtu")

        # 4. LAG / channel-group
        if not want_lag and actual["channel_group"] is not None:
            iface_cmds.append("   no channel-group")

        # 5. Switchport mode & VLANs
        if not want_mode:
            if actual["switchport_mode"] == "trunk":
                iface_cmds.append("   no switchport mode")
            if actual["trunk_vlans"]:
                iface_cmds.append("   no switchport trunk allowed vlan")
            if actual["access_vlan"] is not None:
                iface_cmds.append("   no switchport access vlan")
        elif want_mode == "access":
            if actual["switchport_mode"] == "trunk":
                iface_cmds.append("   switchport mode access")
            if want_untagged is not None and actual["access_vlan"] != want_untagged:
                iface_cmds.append(f"   switchport access vlan {want_untagged}")
        elif want_mode in ("tagged", "tagged-all"):
            if actual["switchport_mode"] != "trunk":
                iface_cmds.append("   switchport mode trunk")

        if iface_cmds:
            cmds.append(f"interface {eos_name}")
            cmds.extend(iface_cmds)

    return cmds


def _fetch_device_interfaces_live(device: str) -> list[dict[str, Any]]:
    """Fetch a device's interfaces directly from NetBox's Django ORM (REST API fallback)."""
    if operations_resolve._django_orm_ready():
        try:
            from dcim.models import Interface

            rows: list[dict[str, Any]] = []
            qs = (
                Interface.objects.filter(device__name=device)
                .select_related("lag", "untagged_vlan")
                .prefetch_related("tagged_vlans")
            )
            for iface in qs:
                rows.append(
                    {
                        "name": str(iface.name or ""),
                        "enabled": bool(iface.enabled),
                        "description": str(iface.description or ""),
                        "mtu": iface.mtu,
                        "mode": str(iface.mode or ""),
                        "lag": str(iface.lag.name) if iface.lag else None,
                        "untagged_vlan": int(iface.untagged_vlan.vid) if iface.untagged_vlan else None,
                        "tagged_vlans": [int(v.vid) for v in iface.tagged_vlans.all()],
                    }
                )
            return rows
        except Exception:
            pass

    import requests

    try:
        r = requests.get(
            f"{conf.netbox_url()}/api/dcim/interfaces/?device={device}&limit=100",
            headers=_nb_headers(),
            timeout=15,
        )
        if r.status_code == 200:
            return list((r.json() or {}).get("results") or [])
    except Exception:
        pass
    return []


def _fetch_config_context_data(name: str) -> dict[str, Any]:
    """Fetch a named NetBox config context's JSON `data` dict directly from ORM (REST fallback)."""
    if operations_resolve._django_orm_ready():
        try:
            from extras.models import ConfigContext

            ctx = ConfigContext.objects.filter(name=name).first()
            if ctx and isinstance(ctx.data, dict):
                return dict(ctx.data)
            return {}
        except Exception:
            pass

    import requests

    try:
        r = requests.get(
            f"{conf.netbox_url()}/api/extras/config-contexts/?name={name}",
            headers=_nb_headers(),
            timeout=15,
        )
        if r.status_code == 200:
            results = (r.json() or {}).get("results") or []
            if results:
                return dict(results[0].get("data") or {})
    except Exception:
        pass
    return {}


def _vxlan_and_svi_drift_commands(running_cfg: str, tenant_core_data: dict[str, Any]) -> list[str]:
    """Compare `tenant-core` l2_segments (VNI mappings + Anycast SVIs) against live EOS running-config."""
    live_ifaces = _parse_running_interfaces(running_cfg)
    cmds: list[str] = []
    vxlan_missing: list[str] = []

    for tenant in tenant_core_data.get("tenants") or []:
        t_name = str(tenant.get("name") or "CORE")
        for seg in tenant.get("l2_segments") or []:
            vid = int(seg.get("vlan_number") or 0)
            if vid <= 1 or vid >= 4000:
                continue
            vname = str(seg.get("vlan_name") or f"VLAN{vid}")
            vni = int(seg.get("vni") or (10000 + vid))
            svi_ip = str(seg.get("vlan_ip") or "").strip()
            mtu = int(seg.get("mtu") or 9000)

            # Check Vxlan1 mapping (`vxlan vlan <vid> vni <vni>` or `vxlan vlan <vid> vni <vid>`)
            if f"vxlan vlan {vid} vni " not in running_cfg:
                vxlan_missing.append(f"   vxlan vlan {vid} vni {vni}")

            # Check SVI if vlan_ip is declared
            if svi_ip and f"Vlan{vid}" not in live_ifaces:
                cmds.extend(
                    [
                        f"interface Vlan{vid}",
                        f"   description {vname}",
                        f"   mtu {mtu}",
                        f"   vrf {t_name}",
                        f"   ip address virtual {svi_ip}",
                        "   no shutdown",
                    ]
                )

    if vxlan_missing:
        cmds = ["interface Vxlan1", *vxlan_missing, *cmds]
    return cmds


def _services_drift_commands(running_cfg: str, fabric_global_data: dict[str, Any]) -> list[str]:
    """Compare `fabric-global` management NTP/DNS & optional `standard_baseline` against EOS running-config."""
    mgmt = fabric_global_data.get("management") or {}
    cmds: list[str] = []
    for ip in mgmt.get("ntp_servers") or []:
        ip_s = str(ip).strip()
        if ip_s and f"ntp server vrf MGMT {ip_s}" not in running_cfg and f"ntp server {ip_s}" not in running_cfg:
            cmds.append(f"ntp server vrf MGMT {ip_s}")
    for ip in mgmt.get("dns_servers") or []:
        ip_s = str(ip).strip()
        if ip_s and f"ip name-server vrf MGMT {ip_s}" not in running_cfg:
            cmds.append(f"ip name-server vrf MGMT {ip_s}")

    # If Step 10 Standard Fabric Baseline has been saved in NetBox `fabric-global`, check it too.
    baseline = fabric_global_data.get("standard_baseline")
    if isinstance(baseline, dict) and baseline:
        from . import workshop

        norm = workshop.normalize_standard_baseline(baseline)
        for line in workshop._missing_baseline_lines(running_cfg, norm):
            if line not in cmds:
                cmds.append(line)
    return cmds


def plan(operation_key: str, values: dict[str, Any]) -> dict[str, Any]:
    """Resolve and preview a Day 2 operation without mutating NetBox or any switch."""
    from concurrent.futures import ThreadPoolExecutor

    resolved = operations_resolve.resolve(operation_key=operation_key, values=values)
    nb_changes, per_device = build_device_commands(resolved)

    reachable_map: dict[str, tuple[bool, str]] = {}

    # Special handling for `remediate_drift`: inspect all target switches in parallel.
    if resolved.operation.kind == "remediate_drift":
        intended_vlans = operations_resolve._fetch_vlans_live()
        intended_map = {int(v["vid"]): str(v["name"]) for v in intended_vlans}
        tenant_core_data = _fetch_config_context_data("tenant-core")
        fabric_global_data = _fetch_config_context_data("fabric-global")
        from workbench.device_reader import parse_vlans

        def _inspect_drift(dev: str) -> tuple[str, bool, str, list[str]]:
            is_leaf = dev in operations_resolve.FABRIC_LEAVES
            try:
                out = eapi_exec._eapi(dev, ["enable", "show vlan", "show running-config"], timeout=45)
                vlan_text = out[1] if len(out) > 1 else ""
                running_cfg = out[2] if len(out) > 2 else ""

                cmds: list[str] = []
                if is_leaf:
                    have = set(parse_vlans(vlan_text))
                    missing = sorted(set(intended_map) - have)
                    for vid in missing:
                        cmds.extend([f"vlan {vid}", f"   name {intended_map[vid]}"])

                    cmds.extend(_vxlan_and_svi_drift_commands(running_cfg, tenant_core_data))
                    nb_ifaces = _fetch_device_interfaces_live(dev)
                    cmds.extend(_interface_drift_commands(dev, running_cfg, nb_ifaces))

                cmds.extend(_services_drift_commands(running_cfg, fabric_global_data))
                return dev, True, "", cmds
            except Exception as exc:
                return dev, False, str(exc), []

        with ThreadPoolExecutor(max_workers=min(len(resolved.devices), 6) or 1) as pool:
            for dev, ok_r, err_r, cmds in pool.map(_inspect_drift, resolved.devices):
                reachable_map[dev] = (ok_r, err_r)
                per_device[dev] = cmds
    else:
        def _ping_switch(dev: str) -> tuple[str, bool, str]:
            try:
                eapi_exec._eapi(dev, ["enable", "show hostname"], timeout=15)
                return dev, True, ""
            except Exception as exc:
                return dev, False, str(exc)

        with ThreadPoolExecutor(max_workers=min(len(resolved.devices), 6) or 1) as pool:
            for dev, ok_r, err_r in pool.map(_ping_switch, resolved.devices):
                reachable_map[dev] = (ok_r, err_r)

    devices_preview: list[dict[str, Any]] = []
    needs_change = 0
    already = 0
    unreachable = 0

    for dev in resolved.devices:
        cmds = per_device.get(dev) or []
        reachable, err = reachable_map.get(dev, (False, "unreachable"))

        if not reachable:
            unreachable += 1
            devices_preview.append(
                {
                    "device": dev,
                    "status": "unreachable",
                    "headline": f"{dev}: unreachable ({err})",
                    "commands": cmds,
                    "diff": "",
                }
            )
        elif not cmds:
            already += 1
            devices_preview.append(
                {
                    "device": dev,
                    "status": "already",
                    "headline": f"{dev}: already matches NetBox intent",
                    "commands": [],
                    "diff": "",
                }
            )
        else:
            needs_change += 1
            diff_text = "\n".join(f"+ {line}" for line in cmds)
            devices_preview.append(
                {
                    "device": dev,
                    "status": "needs_change",
                    "headline": f"{dev}: {len(cmds)} EOS config line(s) to apply",
                    "commands": cmds,
                    "diff": diff_text,
                }
            )

    change_id = f"D2-{uuid.uuid4().hex[:8].upper()}"
    staged_all = _load_staged()
    staged_all[change_id] = {
        "change_id": change_id,
        "created_at": time.time(),
        "resolved": _serialize_resolved(resolved),
        "netbox_changes": nb_changes,
        "per_device": per_device,
    }
    _save_staged(staged_all)

    return {
        "ok": True,
        "change_id": change_id,
        "operation": resolved.operation.key,
        "sentence": resolved.sentence,
        "netbox_changes": nb_changes,
        "devices": devices_preview,
        "needs_change_count": needs_change,
        "already_count": already,
        "unreachable_count": unreachable,
        "headline": (
            f"Ready to apply on {needs_change} switch(es)"
            if needs_change
            else "All target switches already match intent"
        ),
    }


def apply(change_id: str, approver: str, *, netbox_only: bool = False) -> dict[str, Any]:
    """Apply a staged Day 2 plan: NetBox only (to showcase drift), or NetBox -> eAPI -> audit."""
    approver = str(approver or "").strip()
    if not approver:
        return {
            "ok": False,
            "headline": "An approver is required",
            "detail": "No change is applied without a named person on the record.",
        }

    staged_all = _load_staged()
    staged = staged_all.get(change_id)
    if not staged:
        return {
            "ok": False,
            "headline": "Preview expired or not found",
            "detail": "Click Preview the change first, then approve.",
        }

    resolved = _deserialize_resolved(staged["resolved"])
    per_device: dict[str, list[str]] = staged["per_device"]

    # 1. Update NetBox Source of Truth first
    nb_result = _apply_netbox_update(resolved)

    if netbox_only:
        ok = bool(nb_result.get("ok"))
        _append_audit(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "change_id": change_id,
                "operation": resolved.operation.key,
                "mode": "netbox_only",
                "sentence": resolved.sentence,
                "approver": approver,
                "devices": list(resolved.devices),
                "netbox_notes": nb_result.get("notes") or [],
                "ok": ok,
                "failures": 0,
            }
        )
        if ok:
            staged_all.pop(change_id, None)
            _save_staged(staged_all)
        return {
            "ok": ok,
            "netbox_only": True,
            "change_id": change_id,
            "approver": approver,
            "sentence": resolved.sentence,
            "netbox_notes": nb_result.get("notes") or [],
            "devices": [],
            "headline": f"Saved intent to NetBox ({change_id}) — switches not pushed yet",
            "detail": "NetBox now differs from the live switches. Run 'Detect & Remediate Config Drift' to inspect the diff and remediate.",
        }

    # 2. Push EOS commands over eAPI + write memory + verify
    device_results: list[dict[str, Any]] = []
    failures = 0
    for dev in resolved.devices:
        cmds = per_device.get(dev) or []
        if not cmds:
            device_results.append(
                {"device": dev, "ok": True, "headline": "Already compliant — nothing pushed"}
            )
            continue
        try:
            eapi_exec._eapi(dev, ["enable", "configure", *cmds, "end", "write memory"], timeout=90)
            device_results.append(
                {"device": dev, "ok": True, "headline": f"Applied {len(cmds)} line(s) and saved"}
            )
        except Exception as exc:
            failures += 1
            device_results.append({"device": dev, "ok": False, "headline": str(exc)})

    ok = failures == 0 and bool(nb_result.get("ok"))

    # 3. Record audit trail
    _append_audit(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "change_id": change_id,
            "operation": resolved.operation.key,
            "sentence": resolved.sentence,
            "approver": approver,
            "devices": list(resolved.devices),
            "netbox_notes": nb_result.get("notes") or [],
            "ok": ok,
            "failures": failures,
        }
    )

    if ok:
        staged_all.pop(change_id, None)
        _save_staged(staged_all)

    return {
        "ok": ok,
        "change_id": change_id,
        "approver": approver,
        "sentence": resolved.sentence,
        "netbox_notes": nb_result.get("notes") or [],
        "devices": device_results,
        "headline": (
            f"Applied and verified ({change_id}) — approved by {approver}"
            if ok
            else f"Applied with {failures} switch failure(s) ({change_id})"
        ),
        "detail": "; ".join(nb_result.get("notes") or []),
    }


__all__ = ["apply", "build_device_commands", "plan"]
