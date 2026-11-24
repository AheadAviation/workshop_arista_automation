"""Unit tests for `plugins/netbox_ops/netbox_ops/workshop.py` and UI wiring (INIT-004/SPEC-002..004)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
PKG = REPO_ROOT / "plugins" / "netbox_ops" / "netbox_ops"
TEMPLATES = PKG / "templates" / "netbox_ops"


def _load_workshop_module(monkeypatch):
    """Load `netbox_ops.workshop` without requiring NetBox/Django installed on the host."""
    nb_pkg = types.ModuleType("netbox")
    nb_plugins = types.ModuleType("netbox.plugins")
    nb_plugins.get_plugin_config = lambda plugin, key, default=None: default  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "netbox", nb_pkg)
    monkeypatch.setitem(sys.modules, "netbox.plugins", nb_plugins)

    pkg = types.ModuleType("netbox_ops")
    pkg.__path__ = [str(PKG)]
    monkeypatch.setitem(sys.modules, "netbox_ops", pkg)

    for name in ("conf", "eapi_exec", "live_config", "labs", "devices_admin", "workshop"):
        full = f"netbox_ops.{name}"
        spec = importlib.util.spec_from_file_location(full, PKG / f"{name}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, full, mod)
        setattr(pkg, name, mod)
        spec.loader.exec_module(mod)

    return sys.modules["netbox_ops.workshop"]


def test_parsers_bgp_mlag_ping(monkeypatch):
    ws = _load_workshop_module(monkeypatch)

    bgp_text = """
BGP summary information for VRF default
Router identifier 10.255.0.1, local AS number 65000
  Description              Neighbor   V AS           MsgRcvd   MsgSent  InQ OutQ  Up/Down State   PfxRcd PfxAcc
  leaf-01                  10.255.0.11 4 65001             42        43    0    0 00:05:10 Estab   12     12
                           10.255.0.12 4 65001             42        43    0    0 00:05:10 Estab   12     12
                           10.255.0.13 4 65002             42        43    0    0 00:05:10 Estab   12     12
                           10.255.0.14 4 65002             42        43    0    0 00:05:10 Active  0      0
"""
    parsed_bgp = ws.parse_bgp_summary(bgp_text)
    assert parsed_bgp["total"] == 4
    assert parsed_bgp["established"] == 3

    mlag_text = """
MLAG Configuration:
domain-id                          :       MLAG-PAIR-1
state                              :            Active
negotiation status                 :         Connected
peer-link status                   :                Up
local-int status                   :                Up
"""
    parsed_mlag = ws.parse_mlag_status(mlag_text)
    assert parsed_mlag["active"] is True
    assert parsed_mlag["fields"]["peer-link status"] == "Up"

    assert ws.parse_ping_success("2 packets transmitted, 2 received, 0% packet loss") is True
    assert ws.parse_ping_success("2 packets transmitted, 0 received, 100% packet loss") is False


def test_read_config_enforces_allowlist_and_redacts_secrets(monkeypatch, tmp_path):
    ws = _load_workshop_module(monkeypatch)
    monkeypatch.setattr(ws, "CONFIGS_DIR", tmp_path)

    # Path traversal / unknown device refused
    bad = ws.read_config("../etc/passwd")
    assert bad["ok"] is False
    assert "Unknown fabric device" in bad["headline"]

    # Valid device redacted
    (tmp_path / "spine-01.cfg").write_text(
        "hostname spine-01\nusername admin privilege 15 secret sha512 SUPERSECRET\n",
        encoding="utf-8",
    )
    good = ws.read_config("spine-01")
    assert good["ok"] is True
    assert "SUPERSECRET" not in good["content"]
    assert "<redacted>" in good["content"]


def test_config_diffs_computes_unified_diff(monkeypatch, tmp_path):
    ws = _load_workshop_module(monkeypatch)
    monkeypatch.setattr(ws, "CONFIGS_DIR", tmp_path)

    for dev in ws.FABRIC_DEVICES:
        (tmp_path / f"{dev}.cfg").write_text(
            f"hostname {dev}\nvlan 100\nvlan 150\n", encoding="utf-8"
        )

    def fake_running(dev: str):
        if dev == "leaf-01":
            return {"ok": True, "config": f"hostname {dev}\nvlan 100\n"}
        return {"ok": True, "config": f"hostname {dev}\nvlan 100\nvlan 150\n"}

    monkeypatch.setattr(ws.live_config, "running_config", fake_running)

    res = ws.config_diffs()
    assert res["ok"] is True
    assert res["changed_count"] == 1
    by_dev = {d["device"]: d for d in res["devices"]}
    assert by_dev["leaf-01"]["changed"] is True
    assert "+vlan 150" in by_dev["leaf-01"]["diff"]
    assert by_dev["spine-01"]["changed"] is False


def test_workshop_status_reflects_live_files_and_containers(monkeypatch, tmp_path):
    ws = _load_workshop_module(monkeypatch)
    clab_dir = tmp_path / "clab"
    cfg_dir = tmp_path / "configs"
    clab_dir.mkdir()
    cfg_dir.mkdir()
    (clab_dir / ws.TOPOLOGY_FILENAME).write_text("name: netbox-arista-lab\n", encoding="utf-8")
    for dev in ws.FABRIC_DEVICES:
        (cfg_dir / f"{dev}.cfg").write_text(f"hostname {dev}\n", encoding="utf-8")

    monkeypatch.setattr(ws, "CLAB_DIR", clab_dir)
    monkeypatch.setattr(ws, "CONFIGS_DIR", cfg_dir)
    monkeypatch.setattr(ws, "_count_fabric_devices", lambda: (6, list(ws.FABRIC_DEVICES)))
    monkeypatch.setattr(
        ws.labs,
        "lab_status",
        lambda: {
            "ok": True,
            "nodes": [{"name": d, "up": True} for d in ws.FABRIC_DEVICES],
            "running": 6,
            "total": 6,
        },
    )

    st = ws.workshop_status()
    assert st["ok"] is True
    assert st["netbox_count"] == 6
    assert st["topology_exists"] is True
    assert len(st["config_files"]) == 6
    assert st["ceos_running"] == 6


def test_workshop_views_and_templates_security_wiring():
    views_src = (PKG / "views.py").read_text(encoding="utf-8")
    urls_src = (PKG / "urls.py").read_text(encoding="utf-8")
    nav_src = (PKG / "navigation.py").read_text(encoding="utf-8")
    tpl_src = (TEMPLATES / "_workshop.html").read_text(encoding="utf-8")

    # Nav & URL registration
    assert 'link="plugins:netbox_ops:workshop"' in nav_src
    for route_name in (
        "workshop_clean",
        "workshop_import",
        "workshop_topology_gen",
        "workshop_configs_gen",
        "workshop_deploy",
        "workshop_destroy",
        "workshop_push",
        "workshop_verify",
        "workshop_validate",
        "workshop_backup",
        "workshop_rollback",
        "workshop_diff",
        "workshop_standard_config",
        "workshop_topology_view",
        "workshop_config_view",
    ):
        assert f'name="{route_name}"' in urls_src

    # Mutating views require ChangeOperatorMixin + CSRF
    for cls_name in (
        "WorkshopCleanView",
        "WorkshopImportView",
        "WorkshopTopologyGenView",
        "WorkshopConfigsGenView",
        "WorkshopDeployView",
        "WorkshopDestroyView",
        "WorkshopPushView",
        "WorkshopValidateView",
        "WorkshopBackupView",
        "WorkshopRollbackView",
        "WorkshopStandardConfigView",
    ):
        assert f"class {cls_name}(ChangeOperatorMixin, View):" in views_src

    # Template includes CSRF tokens on every POST form
    assert tpl_src.count('method="post"') == tpl_src.count("{% csrf_token %}")


def test_standard_baseline_commands_and_validation(monkeypatch):
    ws = _load_workshop_module(monkeypatch)

    cfg = ws.normalize_standard_baseline(
        {
            "syslog_host": "172.30.30.10",
            "snmp_community": "CORP-RO",
            "snmp_contact": "netops@corp.local",
            "snmp_location": "DC1-RackA",
            "snmp_trap_host": "172.30.30.20",
            "ntp_servers": "172.30.30.1, 172.30.30.2",
            "dns_servers": "172.30.30.1",
            "domain_name": "corp.local",
            "timezone": "UTC",
        }
    )
    cmds = ws.build_standard_baseline_commands(cfg)
    joined = "\n".join(cmds)

    assert "clock timezone UTC" in joined
    assert "dns domain corp.local" in joined
    assert "logging vrf MGMT host 172.30.30.10 514" in joined
    assert "logging format timestamp high-resolution" in joined
    assert "snmp-server community CORP-RO ro" in joined
    assert "snmp-server host 172.30.30.20 vrf MGMT version 2c CORP-RO" in joined
    assert "snmp-server enable traps" in joined
    assert "ntp server vrf MGMT 172.30.30.1 prefer iburst" in joined
    assert "ntp server vrf MGMT 172.30.30.2 iburst" in joined
    assert "lldp run" in joined
    assert "errdisable recovery cause bpduguard" in joined
    assert "management ssh" in joined

    # Missing baseline check detects absent config and passes when present
    missing_before = ws._missing_baseline_lines("", cfg)
    assert any("snmp-server community CORP-RO ro" in ln for ln in missing_before)
    assert any("logging vrf MGMT host 172.30.30.10" in ln for ln in missing_before)
    assert ws._missing_baseline_lines(joined, cfg) == []

    # Rejects invalid IP or community with spaces
    with pytest.raises(ws.WorkshopError):
        ws.normalize_standard_baseline({"syslog_host": "not-an-ip"})
    with pytest.raises(ws.WorkshopError):
        ws.normalize_standard_baseline({"snmp_community": "bad community"})
