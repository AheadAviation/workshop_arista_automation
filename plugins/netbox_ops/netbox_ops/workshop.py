"""Workshop Quick Start & Day-2 Operations engine (INIT-004/SPEC-002).

Drives the full `wiki/Quick-Start.md` (Steps 1–9) and `wiki/Day-2-Operations.md` workflows from
inside NetBox without requiring the CLI or AI agents, while preserving the `INIT-002` security
boundary:

* Host Docker / ContainerLab / Ansible operations go exclusively through fixed, parameter-free
  POST endpoints on `lab-runner` (`conf.lab_runner_url()`).
* Live Step 8 device verification (BGP, MLAG, VXLAN, EVPN, VLANs/VRF CORE, fabric pings) runs
  directly over eAPI (`eapi_exec._eapi`) — no Docker socket needed.
* File inspection (`topology-docker.clab.yml`, `output/configs/<dev>.cfg`) reads from read-only
  mounts (`/opt/netbox/clab`, `/opt/netbox/configs`) using a strict allowlist of device names.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import conf, devices_admin, eapi_exec, labs, live_config

#: Fixed allowlist of the six Arista cEOS fabric devices.
FABRIC_DEVICES = ("spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04")
SPINES = ("spine-01", "spine-02")
LEAVES = ("leaf-01", "leaf-02", "leaf-03", "leaf-04")

#: Workshop config contexts created by `scripts/bootstrap_netbox.py`.
WORKSHOP_CONFIG_CONTEXTS = (
    "fabric-global",
    "fabric-underlay",
    "fabric-overlay",
    "role-spine",
    "role-leaf",
    "tenant-core",
    "mlag-pair-1",
    "mlag-pair-2",
)

#: Standard network-architect fabric baseline defaults (Step 10).
DEFAULT_STANDARD_BASELINE: dict[str, Any] = {
    "mgmt_vrf": "MGMT",
    "syslog_host": "172.30.30.10",
    "syslog_port": 514,
    "syslog_buffer_bytes": 65536,
    "snmp_community": "FABRIC-RO",
    "snmp_contact": "netops@dc1.lab.local",
    "snmp_location": "DC1-EVPN-Fabric",
    "snmp_trap_host": "172.30.30.10",
    "ntp_servers": ["172.30.30.1"],
    "dns_servers": ["172.30.30.1"],
    "domain_name": "dc1.lab.local",
    "timezone": "UTC",
    "lldp_timer": 30,
    "lldp_hold_time": 120,
    "errdisable_interval": 300,
    "ssh_idle_timeout": 30,
    "ssh_connection_limit": 10,
    "console_idle_timeout": 15,
    "banner_login": (
        "***************************************************************************\n"
        "* AUTHORIZED ACCESS ONLY -- DC1 Arista EVPN/VXLAN Fabric                  *\n"
        "* All sessions are logged and monitored. Disconnect if unauthorized.      *\n"
        "***************************************************************************"
    ),
}

#: Read-only mounted paths inside the NetBox container.
CLAB_DIR = Path("/opt/netbox/clab")
CONFIGS_DIR = Path("/opt/netbox/configs")
TOPOLOGY_FILENAME = "topology-docker.clab.yml"


class WorkshopError(RuntimeError):
    """A workshop operation failed with a user-readable reason."""


# --------------------------------------------------------------------------- #
# Persistent step-completion state (shared across NetBox workers)
# --------------------------------------------------------------------------- #


def _state_file() -> Path:
    log_path = Path(os.environ.get("WORKBENCH_AUDIT_LOG", "output/workbench/audit.jsonl"))
    return log_path.parent / "workshop_state.json"


def _load_workshop_state() -> dict[str, Any]:
    path = _state_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_workshop_state(data: dict[str, Any]) -> None:
    path = _state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _record_step_done(step_key: str, ok: bool) -> None:
    state = _load_workshop_state()
    if ok:
        state[step_key] = {
            "ok": True,
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }
    else:
        state.pop(step_key, None)
    _save_workshop_state(state)


def _reset_workshop_state(*, include_standard: bool = False) -> None:
    state = _load_workshop_state()
    for key in ("push", "verify", "validate"):
        state.pop(key, None)
    if include_standard:
        state.pop("standard_config", None)
    _save_workshop_state(state)


# --------------------------------------------------------------------------- #
# Scoped lab-runner HTTP client
# --------------------------------------------------------------------------- #


def _runner_post(path: str, *, timeout: float = 900.0) -> dict[str, Any]:
    """Call one fixed POST endpoint on `lab-runner`."""
    import requests

    url = f"{conf.lab_runner_url()}{path}"
    try:
        response = requests.post(url, timeout=timeout)
    except requests.RequestException as exc:
        raise WorkshopError(
            f"Could not reach lab-runner at {conf.lab_runner_url()}: {exc}. "
            "Check that the lab-runner service is up."
        ) from exc

    if response.status_code >= 400:
        raise WorkshopError(f"lab-runner returned HTTP {response.status_code} for {path}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise WorkshopError(f"lab-runner returned non-JSON for {path}") from exc

    return payload


# --------------------------------------------------------------------------- #
# NetBox REST helpers for clean-slate fabric reset
# --------------------------------------------------------------------------- #


def _nb_headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    token = conf.netbox_token()
    if token:
        headers["Authorization"] = f"Token {token}"
    return headers


def clean_netbox_fabric() -> dict[str, Any]:
    """Remove the 6 workshop fabric devices, their cables/IPs, and workshop config contexts.

    Leaves NetBox itself, admin users, API tokens, and unrelated data intact so Quick Start
    Step 3 (`full_import.py`) can be run again from a blank fabric.
    """
    import requests

    base = conf.netbox_url()
    headers = _nb_headers()
    deleted_devices: list[str] = []
    deleted_ips = 0
    deleted_cables = 0
    deleted_contexts: list[str] = []
    errors: list[str] = []

    device_ids: dict[str, int] = {}
    for name in FABRIC_DEVICES:
        try:
            resp = requests.get(
                f"{base}/api/dcim/devices/?name={name}", headers=headers, timeout=15
            )
            if resp.status_code == 200:
                for item in (resp.json() or {}).get("results") or []:
                    if item.get("name") == name:
                        device_ids[name] = int(item["id"])
        except requests.RequestException as exc:
            errors.append(f"lookup {name}: {exc}")

    # 1. Delete cables attached to any of the fabric devices
    seen_cables: set[int] = set()
    for name in device_ids:
        try:
            resp = requests.get(
                f"{base}/api/dcim/cables/?device={name}&limit=100", headers=headers, timeout=15
            )
            if resp.status_code == 200:
                for cable in (resp.json() or {}).get("results") or []:
                    cid = int(cable["id"])
                    if cid in seen_cables:
                        continue
                    seen_cables.add(cid)
                    del_r = requests.delete(
                        f"{base}/api/dcim/cables/{cid}/", headers=headers, timeout=15
                    )
                    if del_r.status_code in (200, 204, 404):
                        deleted_cables += 1
                    else:
                        errors.append(f"cable {cid}: HTTP {del_r.status_code}")
        except requests.RequestException as exc:
            errors.append(f"cables for {name}: {exc}")

    # 2. Delete IP addresses assigned to interfaces on the fabric devices
    seen_ips: set[int] = set()
    for name in device_ids:
        try:
            resp = requests.get(
                f"{base}/api/ipam/ip-addresses/?device={name}&limit=200",
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                for ip_obj in (resp.json() or {}).get("results") or []:
                    ipid = int(ip_obj["id"])
                    if ipid in seen_ips:
                        continue
                    seen_ips.add(ipid)
                    del_r = requests.delete(
                        f"{base}/api/ipam/ip-addresses/{ipid}/", headers=headers, timeout=15
                    )
                    if del_r.status_code in (200, 204, 404):
                        deleted_ips += 1
                    else:
                        errors.append(f"ip {ipid}: HTTP {del_r.status_code}")
        except requests.RequestException as exc:
            errors.append(f"ips for {name}: {exc}")

    # 3. Delete the 6 fabric devices (cascades their interfaces)
    for name, did in device_ids.items():
        try:
            del_r = requests.delete(
                f"{base}/api/dcim/devices/{did}/", headers=headers, timeout=20
            )
            if del_r.status_code in (200, 204, 404):
                deleted_devices.append(name)
            else:
                errors.append(f"device {name}: HTTP {del_r.status_code}")
        except requests.RequestException as exc:
            errors.append(f"device {name}: {exc}")

    # 4. Delete the workshop config contexts
    for ctx_name in WORKSHOP_CONFIG_CONTEXTS:
        try:
            resp = requests.get(
                f"{base}/api/extras/config-contexts/?name={ctx_name}",
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                for ctx in (resp.json() or {}).get("results") or []:
                    if ctx.get("name") == ctx_name:
                        del_r = requests.delete(
                            f"{base}/api/extras/config-contexts/{ctx['id']}/",
                            headers=headers,
                            timeout=15,
                        )
                        if del_r.status_code in (200, 204, 404):
                            deleted_contexts.append(ctx_name)
                        else:
                            errors.append(f"config-context {ctx_name}: HTTP {del_r.status_code}")
        except requests.RequestException as exc:
            errors.append(f"config-context {ctx_name}: {exc}")

    return {
        "ok": len(errors) == 0,
        "deleted_devices": deleted_devices,
        "deleted_cables": deleted_cables,
        "deleted_ips": deleted_ips,
        "deleted_contexts": deleted_contexts,
        "errors": errors,
    }


def _django_orm_ready() -> bool:
    try:
        from django.apps import apps

        return bool(apps.ready)
    except Exception:
        return False


def _count_fabric_devices() -> tuple[int, list[str]]:
    """Return how many of the 6 expected fabric devices exist in NetBox."""
    if _django_orm_ready():
        try:
            from dcim.models import Device

            names = set(
                Device.objects.filter(name__in=FABRIC_DEVICES).values_list("name", flat=True)
            )
            found = [d for d in FABRIC_DEVICES if d in names]
            return len(found), found
        except Exception:
            pass

    import requests

    found = []
    try:
        resp = requests.get(
            f"{conf.netbox_url()}/api/dcim/devices/?limit=100",
            headers=_nb_headers(),
            timeout=15,
        )
        if resp.status_code == 200:
            names = {item.get("name") for item in (resp.json() or {}).get("results") or []}
            found = [d for d in FABRIC_DEVICES if d in names]
    except Exception:
        return 0, []
    return len(found), found


def _read_saved_standard_baseline() -> tuple[bool, dict[str, Any]]:
    """Return `(is_saved, baseline_dict)` from NetBox `fabric-global` config context."""
    baseline = dict(DEFAULT_STANDARD_BASELINE)

    if _django_orm_ready():
        try:
            from extras.models import ConfigContext

            ctx = ConfigContext.objects.filter(name="fabric-global").first()
            if ctx and isinstance(ctx.data, dict):
                saved = ctx.data.get("standard_baseline")
                if isinstance(saved, dict) and saved:
                    baseline.update(saved)
                    return True, baseline
                mgmt = ctx.data.get("management") or {}
                if mgmt.get("ntp_servers"):
                    baseline["ntp_servers"] = list(mgmt["ntp_servers"])
                if mgmt.get("dns_servers"):
                    baseline["dns_servers"] = list(mgmt["dns_servers"])
            return False, baseline
        except Exception:
            pass

    import requests

    try:
        resp = requests.get(
            f"{conf.netbox_url()}/api/extras/config-contexts/?name=fabric-global",
            headers=_nb_headers(),
            timeout=10,
        )
        if resp.status_code == 200:
            results = (resp.json() or {}).get("results") or []
            if results:
                data = results[0].get("data") or {}
                saved = data.get("standard_baseline")
                if isinstance(saved, dict) and saved:
                    baseline.update(saved)
                    return True, baseline
                # Even before Step 10 is saved, pre-fill NTP/DNS from management
                mgmt = data.get("management") or {}
                if mgmt.get("ntp_servers"):
                    baseline["ntp_servers"] = list(mgmt["ntp_servers"])
                if mgmt.get("dns_servers"):
                    baseline["dns_servers"] = list(mgmt["dns_servers"])
    except Exception:
        pass
    return False, baseline


def workshop_status() -> dict[str, Any]:
    """Live readiness snapshot for Quick Start Steps 1–10."""
    nb_count, nb_devices = _count_fabric_devices()
    baseline_saved, baseline_cfg = _read_saved_standard_baseline()

    topo_path = CLAB_DIR / TOPOLOGY_FILENAME
    topo_exists = topo_path.exists()
    topo_bytes = topo_path.stat().st_size if topo_exists else 0

    cfg_files: list[str] = []
    if CONFIGS_DIR.exists():
        cfg_files = [
            f"{dev}.cfg" for dev in FABRIC_DEVICES if (CONFIGS_DIR / f"{dev}.cfg").exists()
        ]

    lab = labs.lab_status()
    lab_nodes = lab.get("nodes") or []
    ceos_running = sum(
        1 for n in lab_nodes if n.get("name") in FABRIC_DEVICES and n.get("up")
    )

    state = _load_workshop_state()
    lab_up = ceos_running == len(FABRIC_DEVICES)
    push_done = lab_up and bool((state.get("push") or {}).get("ok"))
    verify_done = lab_up and bool((state.get("verify") or {}).get("ok"))
    validate_done = lab_up and bool((state.get("validate") or {}).get("ok"))
    standard_config_done = lab_up and bool((state.get("standard_config") or {}).get("ok"))

    steps = [
        {
            "number": 1,
            "key": "clean",
            "title": "Clean Slate (Start Over)",
            "done": nb_count == 0 and not topo_exists and not cfg_files and ceos_running == 0,
            "summary": (
                "Blank slate — ready to import"
                if nb_count == 0 and not topo_exists and not cfg_files and ceos_running == 0
                else f"{nb_count}/6 in NetBox · {len(cfg_files)}/6 configs · {ceos_running}/6 switches up"
            ),
        },
        {
            "number": 2,
            "key": "netbox",
            "title": "Start NetBox",
            "done": True,
            "summary": "NetBox is running and healthy (you are using it now)",
        },
        {
            "number": 3,
            "key": "import",
            "title": "Import Network Data into NetBox",
            "done": nb_count == len(FABRIC_DEVICES),
            "summary": f"{nb_count} of {len(FABRIC_DEVICES)} fabric devices in NetBox",
        },
        {
            "number": 4,
            "key": "topology",
            "title": "Generate ContainerLab Topology",
            "done": topo_exists and topo_bytes > 0,
            "summary": (
                f"{TOPOLOGY_FILENAME} ready ({topo_bytes} bytes)"
                if topo_exists
                else f"{TOPOLOGY_FILENAME} not generated yet"
            ),
        },
        {
            "number": 5,
            "key": "configs",
            "title": "Generate Device Configurations",
            "done": len(cfg_files) == len(FABRIC_DEVICES),
            "summary": f"{len(cfg_files)} of {len(FABRIC_DEVICES)} rendered configs ready",
        },
        {
            "number": 6,
            "key": "deploy",
            "title": "Deploy the ContainerLab Fabric",
            "done": ceos_running == len(FABRIC_DEVICES),
            "summary": f"{ceos_running} of {len(FABRIC_DEVICES)} cEOS switches running",
        },
        {
            "number": 7,
            "key": "push",
            "title": "Push Full Configurations",
            "done": push_done,
            "summary": (
                f"Full configs pushed ({(state.get('push') or {}).get('at', 'done')})"
                if push_done
                else (
                    "Ready to push full configs to all 6 switches"
                    if lab_up and len(cfg_files) == len(FABRIC_DEVICES)
                    else "Generate configs and deploy switches first"
                )
            ),
        },
        {
            "number": 8,
            "key": "verify",
            "title": "Verify Fabric State (BGP, MLAG, VXLAN, EVPN, Pings)",
            "done": verify_done,
            "summary": (
                f"All live fabric checks passed ({(state.get('verify') or {}).get('at', 'done')})"
                if verify_done
                else "Run live checks directly against all 6 switches over eAPI"
            ),
        },
        {
            "number": 9,
            "key": "validate",
            "title": "Run Full Validation Suite (test_all.yml)",
            "done": validate_done,
            "summary": (
                f"Full validation suite passed ({(state.get('validate') or {}).get('at', 'done')})"
                if validate_done
                else "Run all 6 Ansible validation categories across the fabric"
            ),
        },
        {
            "number": 10,
            "key": "standard_config",
            "title": "Apply Standard Fabric Baseline (Syslog, SNMP, NTP, DNS, LLDP, Hardening)",
            "done": standard_config_done or baseline_saved,
            "summary": (
                "Standard baseline applied to switches & saved in NetBox"
                if standard_config_done
                else (
                    "Standard baseline saved in NetBox fabric-global"
                    if baseline_saved
                    else "Apply production syslog, SNMP, NTP, DNS, domain, LLDP, errdisable & SSH hardening"
                )
            ),
        },
    ]

    return {
        "ok": True,
        "headline": f"Workshop: {nb_count}/6 in NetBox · {len(cfg_files)}/6 configs · {ceos_running}/6 switches up",
        "netbox_count": nb_count,
        "netbox_devices": nb_devices,
        "topology_exists": topo_exists,
        "topology_file": TOPOLOGY_FILENAME,
        "config_files": cfg_files,
        "ceos_running": ceos_running,
        "fabric_devices": list(FABRIC_DEVICES),
        "lab": lab,
        "steps": steps,
        "push_done": push_done,
        "verify_done": verify_done,
        "validate_done": validate_done,
        "standard_config_done": standard_config_done,
        "standard_baseline_saved": baseline_saved,
        "standard_baseline": {
            **baseline_cfg,
            "ntp_servers_csv": ", ".join(baseline_cfg.get("ntp_servers") or []),
            "dns_servers_csv": ", ".join(baseline_cfg.get("dns_servers") or []),
        },
    }


# --------------------------------------------------------------------------- #
# Safe read-only file viewers
# --------------------------------------------------------------------------- #


def read_topology() -> dict[str, Any]:
    """Read the generated ContainerLab topology YAML for inline viewing."""
    path = CLAB_DIR / TOPOLOGY_FILENAME
    if not path.exists():
        return {
            "ok": False,
            "headline": f"{TOPOLOGY_FILENAME} has not been generated yet",
            "detail": "Run Step 4 (Generate ContainerLab Topology) first.",
            "content": "",
            "filename": TOPOLOGY_FILENAME,
        }
    text = path.read_text(encoding="utf-8")
    return {
        "ok": True,
        "headline": f"{TOPOLOGY_FILENAME} ({len(text.splitlines())} lines)",
        "detail": "",
        "content": text,
        "filename": TOPOLOGY_FILENAME,
    }


def read_config(device: str) -> dict[str, Any]:
    """Read one generated device `.cfg` file (allowlisted device names only)."""
    if device not in FABRIC_DEVICES:
        return {
            "ok": False,
            "headline": f"Unknown fabric device {device!r}",
            "detail": f"Allowed devices: {', '.join(FABRIC_DEVICES)}",
            "device": device,
            "content": "",
        }
    path = CONFIGS_DIR / f"{device}.cfg"
    if not path.exists():
        return {
            "ok": False,
            "headline": f"No generated config for {device}",
            "detail": "Run Step 5 (Generate Device Configurations) first.",
            "device": device,
            "content": "",
        }
    text = live_config.redact(path.read_text(encoding="utf-8"))
    return {
        "ok": True,
        "headline": f"{device}.cfg ({len(text.splitlines())} lines)",
        "detail": "",
        "device": device,
        "content": text,
    }


# --------------------------------------------------------------------------- #
# Day-2 config diff (live running-config vs. generated output/configs/<dev>.cfg)
# --------------------------------------------------------------------------- #


def config_diffs() -> dict[str, Any]:
    """Compare each switch's live running config against its generated config file in parallel."""
    from concurrent.futures import ThreadPoolExecutor

    def _diff_one(dev: str) -> dict[str, Any]:
        cfg_path = CONFIGS_DIR / f"{dev}.cfg"
        if not cfg_path.exists():
            return {
                "device": dev,
                "ok": False,
                "changed": False,
                "error": f"No generated config ({dev}.cfg)",
                "diff": "",
                "added": 0,
                "removed": 0,
            }

        live = live_config.running_config(dev)
        if not live.get("ok"):
            return {
                "device": dev,
                "ok": False,
                "changed": False,
                "error": live.get("detail") or live.get("headline") or "unreachable",
                "diff": "",
                "added": 0,
                "removed": 0,
            }

        running_text = live.get("config") or ""
        intended_text = live_config.redact(cfg_path.read_text(encoding="utf-8"))

        diff_lines = list(
            difflib.unified_diff(
                running_text.splitlines(),
                intended_text.splitlines(),
                fromfile=f"running:{dev}",
                tofile=f"generated:{dev}.cfg",
                lineterm="",
                n=2,
            )
        )
        added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
        return {
            "device": dev,
            "ok": True,
            "changed": bool(diff_lines),
            "error": "",
            "diff": "\n".join(diff_lines),
            "added": added,
            "removed": removed,
        }

    with ThreadPoolExecutor(max_workers=len(FABRIC_DEVICES)) as pool:
        entries = list(pool.map(_diff_one, FABRIC_DEVICES))

    changed_devices = sum(1 for e in entries if e["ok"] and e["changed"])
    errors = sum(1 for e in entries if not e["ok"])

    if errors == len(FABRIC_DEVICES):
        headline = "Could not diff any switch"
    elif changed_devices == 0 and errors == 0:
        headline = "All 6 switches match generated configs"
    else:
        headline = f"{changed_devices} switch(es) differ from generated configs"
        if errors:
            headline += f" · {errors} error(s)"

    return {
        "ok": errors < len(FABRIC_DEVICES),
        "headline": headline,
        "detail": "",
        "devices": entries,
        "changed_count": changed_devices,
        "error_count": errors,
    }


# --------------------------------------------------------------------------- #
# Step 8 — Live fabric verification over eAPI
# --------------------------------------------------------------------------- #


def parse_bgp_summary(text: str) -> dict[str, Any]:
    """Parse `show ip bgp summary` or `show bgp evpn summary` text into peer counts."""
    peers: list[dict[str, str]] = []
    for line in text.splitlines():
        match = re.search(
            r"(\d+\.\d+\.\d+\.\d+)\s+\d+\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\S+\s+(\S+)",
            line,
        )
        if match:
            peers.append({"neighbor": match.group(1), "asn": match.group(2), "state": match.group(3)})
    established = sum(
        1
        for p in peers
        if p["state"].lower() in ("estab", "established") or p["state"].isdigit()
    )
    return {"total": len(peers), "established": established, "peers": peers}


def parse_mlag_status(text: str) -> dict[str, Any]:
    """Parse `show mlag` text into state, negotiation, and peer-link fields."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            k = key.strip().lower()
            v = val.strip()
            if k in ("state", "negotiation status", "peer-link status", "local-int status", "domain-id"):
                fields[k] = v
    active = fields.get("state", "").lower() == "active"
    return {"active": active, "fields": fields}


def parse_ping_success(text: str) -> bool:
    """Return True when EOS `ping` output reports 0% packet loss and >0 received."""
    match = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+received", text)
    if not match:
        return False
    sent, recvd = int(match.group(1)), int(match.group(2))
    return sent > 0 and recvd > 0


def verify_fabric() -> dict[str, Any]:
    """Quick Start Step 8: live BGP, MLAG, VXLAN, EVPN, VLAN/VRF CORE, and fabric ping checks."""
    checks: list[dict[str, Any]] = []
    failures = 0

    def _run_check(title: str, device: str, cmds: list[str], evaluator: Any) -> None:
        nonlocal failures
        try:
            outputs = eapi_exec._eapi(device, ["enable", *cmds], timeout=45)
            body = "\n".join(outputs[1:]).strip()
            ok, summary = evaluator(body)
        except Exception as exc:
            ok = False
            summary = f"{type(exc).__name__}: {exc}"
            body = ""
        if not ok:
            failures += 1
        checks.append(
            {
                "title": title,
                "device": device,
                "commands": cmds,
                "ok": ok,
                "summary": summary,
                "output": body,
            }
        )

    # 1. Spine BGP summaries (expect 4 established leaf peers each)
    for spine in SPINES:
        def _eval_bgp(text: str) -> tuple[bool, str]:
            parsed = parse_bgp_summary(text)
            ok = parsed["established"] >= 4
            return ok, f"{parsed['established']}/{parsed['total']} BGP peers established"

        _run_check(f"BGP Summary ({spine})", spine, ["show ip bgp summary"], _eval_bgp)

    # 2. Leaf MLAG status (expect Active on all 4 leaves)
    for leaf in LEAVES:
        def _eval_mlag(text: str) -> tuple[bool, str]:
            parsed = parse_mlag_status(text)
            state = parsed["fields"].get("state", "unknown")
            peer = parsed["fields"].get("peer-link status", "unknown")
            return parsed["active"], f"state: {state}, peer-link: {peer}"

        _run_check(f"MLAG Status ({leaf})", leaf, ["show mlag"], _eval_mlag)

    # 3. VXLAN & EVPN on leaf-01
    def _eval_vxlan(text: str) -> tuple[bool, str]:
        up = "Vxlan1 is up" in text
        return up, "Vxlan1 is up" if up else "Vxlan1 not up"

    _run_check(
        "VXLAN & VNI Table (leaf-01)",
        "leaf-01",
        ["show interfaces Vxlan1", "show vxlan vni"],
        _eval_vxlan,
    )

    def _eval_evpn(text: str) -> tuple[bool, str]:
        parsed = parse_bgp_summary(text)
        ok = parsed["established"] >= 2
        return ok, f"{parsed['established']}/{parsed['total']} EVPN peers established"

    _run_check("EVPN BGP Summary (leaf-01)", "leaf-01", ["show bgp evpn summary"], _eval_evpn)

    # 4. Tenant VLANs & CORE VRF routes on leaf-01
    def _eval_vlans(text: str) -> tuple[bool, str]:
        has_vlan = "100" in text or "SERVERS" in text
        return has_vlan, "Tenant VLANs and VRF CORE present" if has_vlan else "Expected tenant VLANs not found"

    _run_check(
        "Tenant VLANs & VRF CORE (leaf-01)",
        "leaf-01",
        ["show vlan", "show ip route vrf CORE"],
        _eval_vlans,
    )

    # 5. Cross-fabric loopback pings
    ping_matrix = [
        ("leaf-01", "10.255.0.1", "10.255.0.11", "leaf-01 → spine-01"),
        ("leaf-01", "10.255.0.2", "10.255.0.11", "leaf-01 → spine-02"),
        ("leaf-03", "10.255.0.1", "10.255.0.13", "leaf-03 → spine-01"),
        ("leaf-03", "10.255.0.2", "10.255.0.13", "leaf-03 → spine-02"),
    ]
    for src_dev, dst_ip, src_ip, label in ping_matrix:
        cmd = f"ping {dst_ip} source {src_ip} repeat 2"

        def _eval_ping(text: str, _label: str = label) -> tuple[bool, str]:
            ok = parse_ping_success(text)
            return ok, f"{_label}: reachable" if ok else f"{_label}: ping failed"

        _run_check(f"Fabric Ping ({label})", src_dev, [cmd], _eval_ping)

    passed = len(checks) - failures
    _record_step_done("verify", failures == 0)
    return {
        "ok": failures == 0,
        "headline": (
            f"All {passed} live fabric checks passed"
            if failures == 0
            else f"{passed} of {len(checks)} live fabric checks passed ({failures} failed)"
        ),
        "detail": "",
        "passed": passed,
        "failed": failures,
        "total": len(checks),
        "checks": checks,
    }


# --------------------------------------------------------------------------- #
# Action wrappers (Steps 1, 3, 4, 5, 6, 7, 9 + Day 2 backup/rollback)
# --------------------------------------------------------------------------- #


def clean_slate(*, clear_netbox: bool = True) -> dict[str, Any]:
    """Quick Start Step 1: destroy lab, delete generated files, and optionally clear NetBox fabric."""
    runner_res = _runner_post("/workshop/clean")
    nb_res: dict[str, Any] = {"ok": True, "deleted_devices": [], "deleted_cables": 0, "deleted_ips": 0, "deleted_contexts": []}
    if clear_netbox:
        nb_res = clean_netbox_fabric()
    _reset_workshop_state(include_standard=clear_netbox)

    ok = bool(runner_res.get("ok")) and bool(nb_res.get("ok"))
    nb_summary = (
        f"NetBox cleared ({len(nb_res['deleted_devices'])} devices, "
        f"{nb_res['deleted_cables']} cables, {nb_res['deleted_ips']} IPs, "
        f"{len(nb_res['deleted_contexts'])} config contexts)"
        if clear_netbox
        else "NetBox data kept"
    )
    return {
        "ok": ok,
        "headline": "Clean slate complete — ready to start from scratch" if ok else "Clean slate completed with warnings",
        "detail": nb_summary,
        "output": runner_res.get("output") or "",
        "netbox": nb_res,
    }


def import_netbox() -> dict[str, Any]:
    """Quick Start Step 3: run `scripts/full_import.py` via lab-runner and assign config templates."""
    before, _ = _count_fabric_devices()
    res = _runner_post("/workshop/import")
    after, _ = _count_fabric_devices()

    # Re-assign NetBox config template if installer exists inside the NetBox container.
    template_note = ""
    installer = Path("/opt/netbox/templates/install.py")
    if res.get("ok") and installer.exists():
        try:
            out, code = devices_admin._run(
                ["python", "/opt/netbox/netbox/manage.py", "shell", "-c", f"exec(open('{installer}').read())"],
                timeout=180,
            )
            if code == 0:
                for line in out.splitlines():
                    if "Assigned to" in line:
                        template_note = line.strip()
        except Exception as exc:
            template_note = f"template assignment skipped: {exc}"

    ok = bool(res.get("ok"))
    return {
        "ok": ok,
        "headline": (
            f"Imported fabric into NetBox — {after} device(s) (was {before})"
            if ok
            else "NetBox import failed"
        ),
        "detail": template_note or res.get("error") or "",
        "output": res.get("output") or "",
        "before": before,
        "after": after,
    }


def generate_topology() -> dict[str, Any]:
    """Quick Start Step 4: generate `clab/topology-docker.clab.yml` from NetBox."""
    res = _runner_post("/workshop/topology")
    ok = bool(res.get("ok"))
    return {
        "ok": ok,
        "headline": (
            f"Generated {TOPOLOGY_FILENAME} from NetBox"
            if ok
            else "Topology generation failed"
        ),
        "detail": res.get("error") or "",
        "output": res.get("output") or "",
    }


def generate_configs() -> dict[str, Any]:
    """Quick Start Step 5 / Day 2: generate all 6 device configs via Ansible."""
    res = _runner_post("/workshop/generate-configs")
    ok = bool(res.get("ok"))
    files = (
        [f"{d}.cfg" for d in FABRIC_DEVICES if (CONFIGS_DIR / f"{d}.cfg").exists()]
        if CONFIGS_DIR.exists()
        else []
    )
    return {
        "ok": ok,
        "headline": (
            f"Generated {len(files)} device configuration(s)"
            if ok
            else "Configuration generation failed"
        ),
        "detail": res.get("error") or "",
        "output": res.get("output") or "",
        "configs": files,
    }


def deploy_workshop_lab() -> dict[str, Any]:
    """Quick Start Step 6: deploy the generated `topology-docker.clab.yml`."""
    if not (CLAB_DIR / TOPOLOGY_FILENAME).exists():
        return {
            "ok": False,
            "headline": f"Cannot deploy yet — {TOPOLOGY_FILENAME} is missing",
            "detail": "Run Step 4 (Generate ContainerLab Topology) first.",
            "output": "",
        }
    missing_cfgs = [
        f"{dev}.cfg" for dev in FABRIC_DEVICES if not (CONFIGS_DIR / f"{dev}.cfg").exists()
    ]
    if missing_cfgs:
        return {
            "ok": False,
            "headline": f"Cannot deploy yet — {len(missing_cfgs)} startup-config file(s) missing",
            "detail": (
                f"Missing {', '.join(missing_cfgs)} in output/configs/. "
                "Run Step 5 (Generate Device Configurations) before deploying the lab."
            ),
            "output": "",
        }

    res = _runner_post("/workshop/deploy")
    ok = bool(res.get("ok"))
    return {
        "ok": ok,
        "headline": (
            "ContainerLab fabric deployed — allow ~60s for cEOS switches to finish booting"
            if ok
            else "ContainerLab deploy failed"
        ),
        "detail": res.get("error") or "",
        "output": res.get("output") or "",
    }


def destroy_workshop_lab() -> dict[str, Any]:
    """Destroy the generated `topology-docker.clab.yml`."""
    res = _runner_post("/workshop/destroy")
    ok = bool(res.get("ok"))
    if ok:
        _reset_workshop_state(include_standard=False)
    return {
        "ok": ok,
        "headline": "ContainerLab fabric destroyed" if ok else "ContainerLab destroy failed",
        "detail": res.get("error") or "",
        "output": res.get("output") or "",
    }


def push_full_configs() -> dict[str, Any]:
    """Quick Start Step 7 / Day 2: push full rendered configs to all 6 switches."""
    res = _runner_post("/workshop/push-configs")
    ok = bool(res.get("ok"))
    _record_step_done("push", ok)
    succeeded = res.get("succeeded", 0)
    failed = res.get("failed", 0)
    return {
        "ok": ok,
        "headline": (
            f"Full configurations pushed to all {succeeded} switches"
            if ok
            else f"Config push finished with {failed} failure(s) ({succeeded} succeeded)"
        ),
        "detail": res.get("error") or "",
        "output": res.get("output") or "",
        "devices": res.get("devices") or [],
    }


def backup_configs() -> dict[str, Any]:
    """Day 2: save running-config to flash:backup.cfg on all 6 switches."""
    res = _runner_post("/workshop/backup")
    ok = bool(res.get("ok"))
    return {
        "ok": ok,
        "headline": (
            "Saved flash:backup.cfg on all 6 switches"
            if ok
            else "Backup finished with failures"
        ),
        "detail": res.get("error") or "",
        "output": res.get("output") or "",
        "devices": res.get("devices") or [],
    }


def rollback_configs() -> dict[str, Any]:
    """Day 2: restore flash:backup.cfg via configure replace on all 6 switches."""
    res = _runner_post("/workshop/rollback")
    ok = bool(res.get("ok"))
    return {
        "ok": ok,
        "headline": (
            "Rolled back all 6 switches to flash:backup.cfg"
            if ok
            else "Rollback finished with failures"
        ),
        "detail": res.get("error") or "",
        "output": res.get("output") or "",
        "devices": res.get("devices") or [],
    }


def run_validation(*, quick: bool = False) -> dict[str, Any]:
    """Quick Start Step 9 / Day 2: run `playbooks/test_all.yml` (full or `--tags quick`)."""
    path = "/workshop/validate-quick" if quick else "/workshop/validate"
    res = _runner_post(path)
    ok = bool(res.get("ok"))
    if not quick:
        _record_step_done("validate", ok)
    label = "Quick validation" if quick else "Full validation suite"
    return {
        "ok": ok,
        "headline": f"{label} passed" if ok else f"{label} reported failures",
        "detail": res.get("error") or "",
        "output": res.get("output") or "",
        "quick": quick,
    }


# --------------------------------------------------------------------------- #
# Step 10 — Standard Fabric Baseline Config (Syslog, SNMP, NTP, DNS, LLDP, Hardening)
# --------------------------------------------------------------------------- #


def _clean_single_line(val: Any, default: str, *, max_len: int = 120) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(val if val is not None else "")).strip()
    if not text:
        return default
    return text[:max_len]


def _validate_ipv4_list(raw: Any, default: list[str], *, allow_empty: bool = False) -> list[str]:
    import ipaddress

    if isinstance(raw, (list, tuple)):
        parts = [str(x).strip() for x in raw if str(x).strip()]
    else:
        parts = [p.strip() for p in re.split(r"[,\s]+", str(raw or "")) if p.strip()]
    if not parts:
        return [] if allow_empty else list(default)
    valid: list[str] = []
    for item in parts:
        try:
            ipaddress.IPv4Address(item)
        except ValueError as exc:
            raise WorkshopError(f"Invalid IPv4 address: {item!r}") from exc
        if item not in valid:
            valid.append(item)
    return valid


def _validate_int(raw: Any, default: int, *, low: int, high: int, label: str) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        val = int(str(raw).strip())
    except ValueError as exc:
        raise WorkshopError(f"{label} must be an integer (got {raw!r})") from exc
    if not (low <= val <= high):
        raise WorkshopError(f"{label} must be between {low} and {high} (got {val})")
    return val


def normalize_standard_baseline(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate and merge user-supplied Step 10 baseline inputs with architect defaults."""
    p = dict(params or {})
    d = DEFAULT_STANDARD_BASELINE

    syslog_hosts = _validate_ipv4_list(p.get("syslog_host"), [d["syslog_host"]])
    trap_raw = p.get("snmp_trap_host")
    trap_list = (
        _validate_ipv4_list(trap_raw, [], allow_empty=True)
        if trap_raw is not None
        else [d["snmp_trap_host"]]
    )

    ntp_servers = _validate_ipv4_list(
        p.get("ntp_servers") if p.get("ntp_servers") is not None else p.get("ntp_servers_csv"),
        list(d["ntp_servers"]),
    )
    dns_servers = _validate_ipv4_list(
        p.get("dns_servers") if p.get("dns_servers") is not None else p.get("dns_servers_csv"),
        list(d["dns_servers"]),
    )

    domain_name = _clean_single_line(p.get("domain_name"), d["domain_name"], max_len=64)
    if not re.match(r"^[A-Za-z0-9.-]+$", domain_name):
        raise WorkshopError(f"Invalid DNS domain name: {domain_name!r}")

    timezone = _clean_single_line(p.get("timezone"), d["timezone"], max_len=32)
    if not re.match(r"^[A-Za-z0-9/_+-]+$", timezone):
        raise WorkshopError(f"Invalid timezone: {timezone!r}")

    community = _clean_single_line(p.get("snmp_community"), d["snmp_community"], max_len=64)
    if " " in community:
        raise WorkshopError("SNMP community string cannot contain spaces")

    return {
        "mgmt_vrf": d["mgmt_vrf"],
        "syslog_host": syslog_hosts[0],
        "syslog_port": _validate_int(p.get("syslog_port"), d["syslog_port"], low=1, high=65535, label="Syslog port"),
        "syslog_buffer_bytes": _validate_int(
            p.get("syslog_buffer_bytes"), d["syslog_buffer_bytes"], low=4096, high=1048576, label="Syslog buffer"
        ),
        "snmp_community": community,
        "snmp_contact": _clean_single_line(p.get("snmp_contact"), d["snmp_contact"], max_len=80),
        "snmp_location": _clean_single_line(p.get("snmp_location"), d["snmp_location"], max_len=80),
        "snmp_trap_host": trap_list[0] if trap_list else "",
        "ntp_servers": ntp_servers,
        "dns_servers": dns_servers,
        "domain_name": domain_name,
        "timezone": timezone,
        "lldp_timer": _validate_int(p.get("lldp_timer"), d["lldp_timer"], low=5, high=254, label="LLDP timer"),
        "lldp_hold_time": _validate_int(
            p.get("lldp_hold_time"), d["lldp_hold_time"], low=10, high=65535, label="LLDP hold-time"
        ),
        "errdisable_interval": _validate_int(
            p.get("errdisable_interval"), d["errdisable_interval"], low=30, high=86400, label="Errdisable interval"
        ),
        "ssh_idle_timeout": _validate_int(
            p.get("ssh_idle_timeout"), d["ssh_idle_timeout"], low=1, high=1440, label="SSH idle timeout"
        ),
        "ssh_connection_limit": _validate_int(
            p.get("ssh_connection_limit"), d["ssh_connection_limit"], low=1, high=100, label="SSH connection limit"
        ),
        "console_idle_timeout": _validate_int(
            p.get("console_idle_timeout"), d["console_idle_timeout"], low=1, high=1440, label="Console idle timeout"
        ),
        "banner_login": str(p.get("banner_login") or d["banner_login"]).strip(),
    }


def build_standard_baseline_commands(cfg: dict[str, Any]) -> list[str]:
    """Build the ordered Arista EOS CLI lines for the Step 10 standard fabric baseline."""
    vrf = cfg.get("mgmt_vrf") or "MGMT"
    cmds: list[str] = [
        # Time & DNS
        f"clock timezone {cfg['timezone']}",
        f"dns domain {cfg['domain_name']}",
        f"ip domain lookup vrf {vrf} source-interface Management0",
    ]
    for dns in cfg.get("dns_servers") or []:
        cmds.append(f"ip name-server vrf {vrf} {dns}")
    cmds.append(f"ntp local-interface vrf {vrf} Management0")
    for idx, ntp in enumerate(cfg.get("ntp_servers") or []):
        suffix = " prefer iburst" if idx == 0 else " iburst"
        cmds.append(f"ntp server vrf {vrf} {ntp}{suffix}")

    # Syslog / Logging
    cmds.extend(
        [
            f"logging buffered {cfg['syslog_buffer_bytes']} informational",
            "logging console warnings",
            "logging format timestamp high-resolution",
            f"logging vrf {vrf} host {cfg['syslog_host']} {cfg['syslog_port']}",
            f"logging vrf {vrf} source-interface Management0",
        ]
    )

    # SNMP
    cmds.extend(
        [
            f"snmp-server contact {cfg['snmp_contact']}",
            f"snmp-server location {cfg['snmp_location']}",
            f"snmp-server community {cfg['snmp_community']} ro",
            f"snmp-server vrf {vrf}",
        ]
    )
    if cfg.get("snmp_trap_host"):
        cmds.append(
            f"snmp-server host {cfg['snmp_trap_host']} vrf {vrf} version 2c {cfg['snmp_community']}"
        )
    cmds.append("snmp-server enable traps")

    # LLDP & L2 Protection
    cmds.extend(
        [
            "lldp run",
            f"lldp timer {cfg['lldp_timer']}",
            f"lldp hold-time {cfg['lldp_hold_time']}",
            "errdisable recovery cause bpduguard",
            f"errdisable recovery interval {cfg['errdisable_interval']}",
        ]
    )

    # Management plane hardening (SSH & Console)
    cmds.extend(
        [
            "management ssh",
            f"   idle-timeout {cfg['ssh_idle_timeout']}",
            f"   connection limit {cfg['ssh_connection_limit']}",
            "   login timeout 60",
            "exit",
            "management console",
            f"   idle-timeout {cfg['console_idle_timeout']}",
            "exit",
        ]
    )
    return cmds


def _missing_baseline_lines(running_cfg: str, cfg: dict[str, Any]) -> list[str]:
    """Compare a switch's running-config against the standard baseline and return missing commands.

    EOS omits default values (`clock timezone UTC`, `lldp timer 30`, `lldp hold-time 120`,
    `errdisable recovery interval 300`) from `show running-config`, so those are only flagged
    when configured to a non-default value.
    """
    vrf = cfg.get("mgmt_vrf") or "MGMT"
    missing: list[str] = []

    def _check(needle: str, cmd: str) -> None:
        if needle not in running_cfg:
            missing.append(cmd)

    if str(cfg["timezone"]).upper() != "UTC":
        _check(f"clock timezone {cfg['timezone']}", f"clock timezone {cfg['timezone']}")
    _check(f"dns domain {cfg['domain_name']}", f"dns domain {cfg['domain_name']}")
    _check(
        f"ip domain lookup vrf {vrf} source-interface Management0",
        f"ip domain lookup vrf {vrf} source-interface Management0",
    )
    for dns in cfg.get("dns_servers") or []:
        _check(f"ip name-server vrf {vrf} {dns}", f"ip name-server vrf {vrf} {dns}")
    _check(
        f"ntp local-interface vrf {vrf} Management0",
        f"ntp local-interface vrf {vrf} Management0",
    )
    for idx, ntp in enumerate(cfg.get("ntp_servers") or []):
        if f"ntp server vrf {vrf} {ntp}" not in running_cfg:
            suffix = " prefer iburst" if idx == 0 else " iburst"
            missing.append(f"ntp server vrf {vrf} {ntp}{suffix}")

    _check(
        f"logging buffered {cfg['syslog_buffer_bytes']}",
        f"logging buffered {cfg['syslog_buffer_bytes']} informational",
    )
    _check("logging console warnings", "logging console warnings")
    _check("logging format timestamp high-resolution", "logging format timestamp high-resolution")
    _check(
        f"logging vrf {vrf} host {cfg['syslog_host']}",
        f"logging vrf {vrf} host {cfg['syslog_host']} {cfg['syslog_port']}",
    )
    _check(
        f"logging vrf {vrf} source-interface Management0",
        f"logging vrf {vrf} source-interface Management0",
    )

    _check(f"snmp-server contact {cfg['snmp_contact']}", f"snmp-server contact {cfg['snmp_contact']}")
    _check(f"snmp-server location {cfg['snmp_location']}", f"snmp-server location {cfg['snmp_location']}")
    _check(
        f"snmp-server community {cfg['snmp_community']} ro",
        f"snmp-server community {cfg['snmp_community']} ro",
    )
    _check(f"snmp-server vrf {vrf}", f"snmp-server vrf {vrf}")
    if cfg.get("snmp_trap_host"):
        _check(
            f"snmp-server host {cfg['snmp_trap_host']} vrf {vrf}",
            f"snmp-server host {cfg['snmp_trap_host']} vrf {vrf} version 2c {cfg['snmp_community']}",
        )
    _check("snmp-server enable traps", "snmp-server enable traps")

    if int(cfg["lldp_timer"]) != 30:
        _check(f"lldp timer {cfg['lldp_timer']}", f"lldp timer {cfg['lldp_timer']}")
    if int(cfg["lldp_hold_time"]) != 120:
        _check(f"lldp hold-time {cfg['lldp_hold_time']}", f"lldp hold-time {cfg['lldp_hold_time']}")
    _check("errdisable recovery cause bpduguard", "errdisable recovery cause bpduguard")
    if int(cfg["errdisable_interval"]) != 300:
        _check(
            f"errdisable recovery interval {cfg['errdisable_interval']}",
            f"errdisable recovery interval {cfg['errdisable_interval']}",
        )

    ssh_missing: list[str] = []
    if f"idle-timeout {cfg['ssh_idle_timeout']}" not in running_cfg:
        ssh_missing.append(f"   idle-timeout {cfg['ssh_idle_timeout']}")
    if f"connection limit {cfg['ssh_connection_limit']}" not in running_cfg:
        ssh_missing.append(f"   connection limit {cfg['ssh_connection_limit']}")
    if ssh_missing:
        missing.extend(["management ssh", *ssh_missing, "exit"])

    if f"idle-timeout {cfg['console_idle_timeout']}" not in running_cfg:
        missing.extend(
            ["management console", f"   idle-timeout {cfg['console_idle_timeout']}", "exit"]
        )

    return missing


def save_standard_baseline_to_netbox(cfg: dict[str, Any]) -> dict[str, Any]:
    """Persist `standard_baseline` (and sync NTP/DNS) into NetBox `fabric-global` config context."""
    import requests

    base = conf.netbox_url()
    headers = _nb_headers()
    try:
        r = requests.get(
            f"{base}/api/extras/config-contexts/?name=fabric-global",
            headers=headers,
            timeout=15,
        )
        ctxs = (r.json() or {}).get("results") if r.status_code == 200 else []
        if not ctxs:
            return {
                "ok": False,
                "note": "Config context 'fabric-global' not found in NetBox (run Step 3 Import first)",
            }
        ctx = ctxs[0]
        data = dict(ctx.get("data") or {})
        mgmt = dict(data.get("management") or {})
        mgmt["ntp_servers"] = list(cfg["ntp_servers"])
        mgmt["dns_servers"] = list(cfg["dns_servers"])
        data["management"] = mgmt
        data["standard_baseline"] = dict(cfg)

        pr = requests.patch(
            f"{base}/api/extras/config-contexts/{ctx['id']}/",
            headers=headers,
            json={"data": data},
            timeout=15,
        )
        if pr.status_code not in (200, 201):
            return {"ok": False, "note": f"NetBox returned HTTP {pr.status_code} updating fabric-global"}
        return {
            "ok": True,
            "note": "Updated NetBox `fabric-global` config context (`standard_baseline` + `management` NTP/DNS)",
        }
    except requests.RequestException as exc:
        return {"ok": False, "note": f"Could not reach NetBox API: {exc}"}


def preview_standard_baseline(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read-only preview of Step 10 standard fabric baseline against all 6 live switches in parallel."""
    from concurrent.futures import ThreadPoolExecutor

    cfg = normalize_standard_baseline(params)
    full_cmds = build_standard_baseline_commands(cfg)

    def _preview_one(dev: str) -> dict[str, Any]:
        try:
            out = eapi_exec._eapi(dev, ["enable", "show running-config"], timeout=30)
            running_cfg = out[1] if len(out) > 1 else ""
        except Exception as exc:
            return {
                "device": dev,
                "ok": False,
                "changed": False,
                "error": str(exc),
                "diff": "",
                "added": 0,
                "removed": 0,
            }
        missing = _missing_baseline_lines(running_cfg, cfg)
        return {
            "device": dev,
            "ok": True,
            "changed": bool(missing),
            "error": "",
            "diff": "\n".join(f"+ {line}" for line in missing),
            "added": len(missing),
            "removed": 0,
        }

    with ThreadPoolExecutor(max_workers=len(FABRIC_DEVICES)) as pool:
        devices = list(pool.map(_preview_one, FABRIC_DEVICES))

    changed_count = sum(1 for d in devices if d["ok"] and d["changed"])
    errors = sum(1 for d in devices if not d["ok"])

    headline = (
        f"Preview: {changed_count} of {len(FABRIC_DEVICES)} switch(es) need standard baseline commands"
        if changed_count
        else "All 6 switches already match the standard fabric baseline"
    )
    return {
        "ok": errors < len(FABRIC_DEVICES),
        "headline": headline,
        "detail": (
            f"Syslog {cfg['syslog_host']}:{cfg['syslog_port']} · SNMP {cfg['snmp_community']} ({cfg['snmp_location']}) "
            f"· NTP {', '.join(cfg['ntp_servers'])} · DNS {', '.join(cfg['dns_servers'])} · Domain {cfg['domain_name']} "
            f"· LLDP {cfg['lldp_timer']}s/{cfg['lldp_hold_time']}s · Errdisable {cfg['errdisable_interval']}s · SSH/Console hardening"
        ),
        "devices": devices,
        "output": "\n".join(full_cmds),
        "baseline": cfg,
    }


def apply_standard_baseline(
    params: dict[str, Any] | None = None,
    *,
    netbox_only: bool = False,
) -> dict[str, Any]:
    """Step 10: save standard fabric baseline to NetBox `fabric-global` and optionally push to all 6 switches."""
    cfg = normalize_standard_baseline(params)
    full_cmds = build_standard_baseline_commands(cfg)

    nb_res = save_standard_baseline_to_netbox(cfg)
    if not nb_res.get("ok"):
        return {
            "ok": False,
            "headline": "Failed to save standard baseline to NetBox",
            "detail": nb_res.get("note") or "",
            "output": "\n".join(full_cmds),
            "devices": [],
        }

    if netbox_only:
        return {
            "ok": True,
            "netbox_only": True,
            "headline": "Saved Standard Fabric Baseline to NetBox `fabric-global` — switches not pushed yet",
            "detail": (
                f"{nb_res['note']}. Open 'Day-two actions → Detect & Remediate Config Drift' "
                "(scope: All 6 fabric switches) to inspect the diff and remediate."
            ),
            "output": "\n".join(full_cmds),
            "devices": [],
        }

    # Build eAPI command list (including multi-line login banner via eAPI input object)
    from concurrent.futures import ThreadPoolExecutor

    eapi_cmds: list[Any] = ["enable", "configure", *full_cmds]
    if cfg.get("banner_login"):
        eapi_cmds.append({"cmd": "banner login", "input": cfg["banner_login"] + "\nEOF\n"})
    eapi_cmds.extend(["end", "write memory"])

    def _apply_one(dev: str) -> dict[str, Any]:
        try:
            eapi_exec._eapi(dev, eapi_cmds, timeout=90)
            return {
                "device": dev,
                "ok": True,
                "error": f"Applied {len(full_cmds)} baseline lines + login banner & saved",
            }
        except Exception as exc:
            return {"device": dev, "ok": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=len(FABRIC_DEVICES)) as pool:
        device_results = list(pool.map(_apply_one, FABRIC_DEVICES))

    succeeded = sum(1 for r in device_results if r["ok"])
    failed = len(device_results) - succeeded

    ok = failed == 0
    _record_step_done("standard_config", ok)
    return {
        "ok": ok,
        "headline": (
            f"Standard fabric baseline applied to all {succeeded} switches & saved to NetBox"
            if ok
            else f"Standard baseline finished with {failed} failure(s) ({succeeded} succeeded)"
        ),
        "detail": nb_res["note"],
        "output": "\n".join(full_cmds),
        "devices": device_results,
    }


__all__ = [
    "CLAB_DIR",
    "CONFIGS_DIR",
    "DEFAULT_STANDARD_BASELINE",
    "FABRIC_DEVICES",
    "TOPOLOGY_FILENAME",
    "WorkshopError",
    "apply_standard_baseline",
    "backup_configs",
    "build_standard_baseline_commands",
    "clean_netbox_fabric",
    "clean_slate",
    "config_diffs",
    "deploy_workshop_lab",
    "destroy_workshop_lab",
    "generate_configs",
    "generate_topology",
    "import_netbox",
    "normalize_standard_baseline",
    "parse_bgp_summary",
    "parse_mlag_status",
    "parse_ping_success",
    "preview_standard_baseline",
    "push_full_configs",
    "read_config",
    "read_topology",
    "rollback_configs",
    "run_validation",
    "save_standard_baseline_to_netbox",
    "verify_fabric",
    "workshop_status",
]
