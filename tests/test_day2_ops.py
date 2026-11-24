"""Unit tests for `plugins/netbox_ops/netbox_ops/day2_ops.py` and `_actions.html` (INIT-005/SPEC-002..004)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
PKG = REPO_ROOT / "plugins" / "netbox_ops" / "netbox_ops"
TEMPLATES = PKG / "templates" / "netbox_ops"


def _load_day2_module(monkeypatch):
    nb_pkg = types.ModuleType("netbox")
    nb_plugins = types.ModuleType("netbox.plugins")
    nb_plugins.get_plugin_config = lambda plugin, key, default=None: default  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "netbox", nb_pkg)
    monkeypatch.setitem(sys.modules, "netbox.plugins", nb_plugins)

    pkg = types.ModuleType("netbox_ops")
    pkg.__path__ = [str(PKG)]
    monkeypatch.setitem(sys.modules, "netbox_ops", pkg)

    for name in ("conf", "eapi_exec", "day2_ops"):
        full = f"netbox_ops.{name}"
        spec = importlib.util.spec_from_file_location(full, PKG / f"{name}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, full, mod)
        setattr(pkg, name, mod)
        spec.loader.exec_module(mod)

    return sys.modules["netbox_ops.day2_ops"]


def test_mlag_portchannel_builds_real_mtu_and_mlag_commands(monkeypatch):
    day2 = _load_day2_module(monkeypatch)
    from workbench import operations_resolve

    resolved = operations_resolve.resolve(
        operation_key="provision_mlag_portchannel",
        values={
            "pair": "pair-1",
            "po_id": "20",
            "member_interface": "Ethernet6",
            "vlan": "110",
            "mtu": "9214",
            "description": "STORAGE-01",
        },
        fetch_vlans=lambda: [{"vid": 110, "name": "STORAGE", "tenant": "CORE"}],
    )
    nb_changes, per_dev = day2.build_device_commands(resolved)
    assert nb_changes
    assert set(per_dev.keys()) == {"leaf-01", "leaf-02"}
    cmds = "\n".join(per_dev["leaf-01"])
    assert "interface Port-Channel20" in cmds
    assert "mtu 9214" in cmds
    assert "mlag 20" in cmds
    assert "channel-group 20 mode active" in cmds
    assert "switchport trunk allowed vlan add 110" in cmds


def test_plan_and_apply_enforces_approver_and_writes_netbox_first(monkeypatch, tmp_path):
    day2 = _load_day2_module(monkeypatch)
    from workbench import operations_resolve

    monkeypatch.setenv("WORKBENCH_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(
        operations_resolve,
        "_fetch_vlans_live",
        lambda: [{"vid": 100, "name": "SERVERS", "tenant": "CORE"}],
    )

    eapi_calls: list[tuple[str, list[str]]] = []

    def fake_eapi(device, commands, timeout=90.0):
        eapi_calls.append((device, commands))
        return ["", "ok"]

    monkeypatch.setattr(day2.eapi_exec, "_eapi", fake_eapi)

    nb_called = []
    monkeypatch.setattr(
        day2,
        "_apply_netbox_update",
        lambda resolved: (nb_called.append(resolved.operation.key), {"ok": True, "notes": ["Updated NetBox"]})[1],
    )

    preview = day2.plan(
        "update_fabric_services",
        {"service_type": "ntp", "action": "add", "server_ip": "172.30.30.10"},
    )
    assert preview["ok"] is True
    assert preview["needs_change_count"] == 6
    assert nb_called == []  # Preview must NOT mutate NetBox

    # Missing approver refused
    refused = day2.apply(preview["change_id"], "")
    assert refused["ok"] is False
    assert nb_called == []

    # Approved apply updates NetBox first, then pushes to all 6 switches and writes audit
    applied = day2.apply(preview["change_id"], "Brian Nelson")
    assert applied["ok"] is True
    assert nb_called == ["update_fabric_services"]
    assert (tmp_path / "audit.jsonl").exists()


def test_interface_drift_detects_disabled_interface_and_description_mtu(monkeypatch):
    day2 = _load_day2_module(monkeypatch)

    running_cfg = """
interface Ethernet5
   description STALE-ARRAY
   mtu 9214
   switchport mode trunk
   switchport trunk allowed vlan 100
   channel-group 10 mode active
!
interface Ethernet6
   description OLD-HOST
   mtu 1500
   switchport access vlan 100
!
interface Ethernet7
   shutdown
!
"""
    nb_ifaces = [
        {"name": "eth5", "enabled": True, "description": "", "mtu": None, "mode": "", "lag": None},
        {"name": "eth6", "enabled": False, "description": "SHOWCASE-HOST-01", "mtu": 9214, "mode": "access", "untagged_vlan": 100},
        {"name": "eth7", "enabled": True, "description": "", "mtu": None, "mode": ""},
        # Protected uplink eth1 should be ignored
        {"name": "eth1", "enabled": False, "description": "UPLINK", "mtu": 9214},
    ]
    cmds = day2._interface_drift_commands("leaf-01", running_cfg, nb_ifaces)
    assert cmds == [
        "interface Ethernet5",
        "   no description",
        "   no mtu",
        "   no channel-group",
        "   no switchport mode",
        "   no switchport trunk allowed vlan",
        "interface Ethernet6",
        "   shutdown",
        "   description SHOWCASE-HOST-01",
        "   mtu 9214",
        "interface Ethernet7",
        "   no shutdown",
    ]


def test_vxlan_svi_and_services_drift_detection(monkeypatch):
    day2 = _load_day2_module(monkeypatch)

    running_cfg = """
interface Vxlan1
   vxlan source-interface Loopback1
   vxlan vlan 100 vni 100
!
ntp server vrf MGMT 172.30.30.1
"""
    tenant_core = {
        "tenants": [
            {
                "name": "CORE",
                "l2_segments": [
                    {"vlan_number": 100, "vlan_name": "SERVERS"},
                    {"vlan_number": 165, "vlan_name": "DRIFT_VLAN", "vlan_ip": "10.100.165.1/24", "mtu": 9000},
                ],
            }
        ]
    }
    fabric_global = {"management": {"ntp_servers": ["172.30.30.1", "172.30.30.55"], "dns_servers": ["172.30.30.53"]}}

    vxlan_cmds = day2._vxlan_and_svi_drift_commands(running_cfg, tenant_core)
    assert "interface Vxlan1" in vxlan_cmds
    assert "   vxlan vlan 165 vni 10165" in vxlan_cmds
    assert "interface Vlan165" in vxlan_cmds
    assert "   ip address virtual 10.100.165.1/24" in vxlan_cmds

    svc_cmds = day2._services_drift_commands(running_cfg, fabric_global)
    assert svc_cmds == [
        "ntp server vrf MGMT 172.30.30.55",
        "ip name-server vrf MGMT 172.30.30.53",
    ]


def test_netbox_only_apply_updates_netbox_without_touching_switches(monkeypatch, tmp_path):
    day2 = _load_day2_module(monkeypatch)
    from workbench import operations_resolve

    monkeypatch.setenv("WORKBENCH_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(operations_resolve, "_fetch_vlans_live", lambda: [])

    eapi_calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(day2.eapi_exec, "_eapi", lambda dev, cmds, timeout=90.0: (eapi_calls.append((dev, cmds)), ["", "ok"])[1])

    nb_called = []
    monkeypatch.setattr(
        day2,
        "_apply_netbox_update",
        lambda resolved: (nb_called.append(resolved.operation.key), {"ok": True, "notes": ["Created IPAM VLAN 165"]})[1],
    )

    prev = day2.plan("provision_vlan", {"vlan_id": "165", "vlan_name": "DRIFT_VLAN", "svi_ip": "10.100.165.1/24", "mtu": "9000"})
    eapi_calls.clear()

    res = day2.apply(prev["change_id"], "Brian Nelson", netbox_only=True)
    assert res["ok"] is True
    assert res["netbox_only"] is True
    assert nb_called == ["provision_vlan"]
    assert eapi_calls == []  # Switches NOT touched yet!


def test_actions_template_has_no_leaked_jinja_brace_comment():
    src = (TEMPLATES / "_actions.html").read_text(encoding="utf-8")
    assert "{#" not in src
    assert "#}" not in src
    assert "Provision" not in src or "Day-two operations" in src
