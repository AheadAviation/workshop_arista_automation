"""Tests for the Day 2 planner and drift detector (INIT-001/SPEC-005).

The drift detector is the part a live demo depends on and the part most likely to
rot silently, so it is tested against a realistic EOS `show running-config` both
matching and diverging from intent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "netbox_demo"))

from netbox_demo import day2, design  # noqa: E402


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


class TestAddVlanValidation:
    def test_accepts_valid_input(self):
        plan = day2.plan_add_vlan({"vlan_id": 300, "name": "tenant_a_web", "svi": "10.100.300.1/24"})
        assert plan.values["vlan_id"] == 300
        assert plan.values["vni"] == design.vni_for_vlan(300)

    @pytest.mark.parametrize(
        "values,message",
        [
            ({"vlan_id": 1, "name": "X", "svi": "10.0.0.1/24"}, "between 2 and 4093"),
            ({"vlan_id": 4094, "name": "X", "svi": "10.0.0.1/24"}, "reserved for the MLAG"),
            ({"vlan_id": "abc", "name": "X", "svi": "10.0.0.1/24"}, "must be a number"),
            ({"vlan_id": 300, "name": "", "svi": "10.0.0.1/24"}, "name is required"),
            ({"vlan_id": 300, "name": "bad name!", "svi": "10.0.0.1/24"}, "may contain"),
            ({"vlan_id": 300, "name": "OK", "svi": "not-an-ip"}, "must look like"),
            ({"vlan_id": 300, "name": "OK", "svi": "10.0.0.1/24", "mtu": 100}, "between 1500"),
        ],
    )
    def test_rejects_bad_input(self, values, message):
        with pytest.raises(ValueError, match=message):
            day2.plan_add_vlan(values)

    def test_warns_when_mtu_exceeds_the_typical_9000(self):
        plan = day2.plan_add_vlan(
            {"vlan_id": 300, "name": "OK", "svi": "10.0.0.1/24", "mtu": design.UNDERLAY_MTU}
        )
        assert any("MTU" in warning for warning in plan.warnings)

    def test_no_warning_at_the_normal_tenant_mtu(self):
        plan = day2.plan_add_vlan(
            {"vlan_id": 300, "name": "OK", "svi": "10.0.0.1/24", "mtu": 9000}
        )
        assert plan.warnings == []

    def test_commands_touch_every_leaf_and_no_spine(self):
        plan = day2.plan_add_vlan({"vlan_id": 300, "name": "WEB", "svi": "10.100.300.1/24"})
        assert set(plan.eos_commands) == set(design.LEAVES)
        for spine in design.SPINES:
            assert spine not in plan.eos_commands


class TestRemoveVlanValidation:
    def test_refuses_seeded_vlans(self):
        with pytest.raises(ValueError, match="seeded fabric"):
            day2.plan_remove_vlan({"vlan_id": design.SEED_VLANS[0].vlan_id})

    def test_refuses_mlag_peer_vlan(self):
        with pytest.raises(ValueError, match="MLAG peer link"):
            day2.plan_remove_vlan({"vlan_id": 4094})

    def test_removes_a_tenant_vlan(self):
        plan = day2.plan_remove_vlan({"vlan_id": 300})
        assert set(plan.eos_commands) == set(design.LEAVES)
        assert any("no vlan 300" in c for c in plan.eos_commands["demo-leaf-01"])


class TestServerPortValidation:
    def test_accepts_a_leaf_port(self):
        plan = day2.plan_set_server_port(
            {"device": "demo-leaf-01", "vlan_id": 100, "description": "app-01"}
        )
        cmds = plan.eos_commands["demo-leaf-01"]
        assert "interface Ethernet6" in cmds
        assert any("switchport access vlan 100" in cmd for cmd in cmds)
        assert any("description app-01" in cmd for cmd in cmds)

    def test_rejects_a_spine(self):
        with pytest.raises(ValueError, match="device must be one of"):
            day2.plan_set_server_port({"device": "demo-spine-01", "vlan_id": 100})

    def test_rejects_an_arbitrary_port(self):
        with pytest.raises(ValueError, match="only Ethernet6"):
            day2.plan_set_server_port(
                {"device": "demo-leaf-01", "vlan_id": 100, "port": "Ethernet1"}
            )

    def test_disabled_port_renders_shutdown_and_warns(self):
        plan = day2.plan_set_server_port(
            {"device": "demo-leaf-01", "vlan_id": 100, "enabled": False}
        )
        assert "   shutdown" in plan.eos_commands["demo-leaf-01"]
        assert plan.warnings

    def test_string_false_is_honoured(self):
        plan = day2.plan_set_server_port(
            {"device": "demo-leaf-01", "vlan_id": 100, "enabled": "false"}
        )
        assert plan.values["enabled"] is False


def test_unknown_operation_rejected():
    with pytest.raises(ValueError, match="Unknown operation"):
        day2.plan("delete_everything")


# --------------------------------------------------------------------------- #
# Drift detection
# --------------------------------------------------------------------------- #

MATCHING_RUNNING_CONFIG = """!
hostname demo-leaf-01
!
vlan 100
   name SERVERS
!
vlan 4094
   name MLAG-PEER-LINK
!
interface Ethernet6
   description Server-Port
   switchport access vlan 100
   mtu 9000
   no shutdown
!
interface Vxlan1
   vxlan vlan 100 vni 10100
!
mlag configuration
   domain-id NETBOX-DEMO-1
   peer-address 169.254.1.2
!
snmp-server community FABRIC-RO ro
logging host 172.31.30.1
ntp server vrf MGMT 172.31.30.1
lldp run
!
end
"""

INTENT_CONFIG = """!
hostname demo-leaf-01
!
vlan 100
   name SERVERS
!
interface Ethernet6
   description Server-Port
   switchport access vlan 100
   mtu 9000
   no shutdown
!
interface Vxlan1
   vxlan vlan 100 vni 10100
!
mlag configuration
   domain-id NETBOX-DEMO-1
   peer-address 169.254.1.2
!
snmp-server community FABRIC-RO ro
logging host 172.31.30.1
ntp server vrf MGMT 172.31.30.1
lldp run
!
end
"""


def test_no_drift_when_configs_agree():
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, MATCHING_RUNNING_CONFIG)
    assert drift == []


def test_rogue_vlan_is_detected():
    running = MATCHING_RUNNING_CONFIG.replace(
        "vlan 4094", "vlan 666\n   name ROGUE_DRIFT\n!\nvlan 4094"
    )
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, running)
    assert any(d.category == "vlan" and "666" in d.detail for d in drift)
    # And it is remediation-ready.
    rogue = next(d for d in drift if "666" in d.detail)
    assert "no vlan 666" in rogue.remediation


def test_missing_vlan_is_detected():
    running = MATCHING_RUNNING_CONFIG.replace("vlan 100\n   name SERVERS\n!\n", "")
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, running)
    assert any(d.category == "vlan" and "missing" in d.detail for d in drift)


def test_renamed_vlan_is_detected():
    running = MATCHING_RUNNING_CONFIG.replace("name SERVERS", "name SERVERS_RENAMED")
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, running)
    assert any("SERVERS_RENAMED" in d.detail for d in drift)


def test_unexpected_shutdown_is_detected():
    running = MATCHING_RUNNING_CONFIG.replace("   no shutdown", "   shutdown")
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, running)
    assert any(d.category == "interface" and "shut down" in d.detail for d in drift)


def test_description_change_is_detected():
    running = MATCHING_RUNNING_CONFIG.replace(
        "description Server-Port", "description UNAUTHORIZED_MANUAL_CHANGE"
    )
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, running)
    assert any("UNAUTHORIZED_MANUAL_CHANGE" in d.detail for d in drift)


def test_mtu_mismatch_is_detected():
    running = MATCHING_RUNNING_CONFIG.replace("   mtu 9000", "   mtu 1500")
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, running)
    assert any("MTU" in d.detail for d in drift)


def test_access_vlan_change_is_detected():
    running = MATCHING_RUNNING_CONFIG.replace(
        "switchport access vlan 100", "switchport access vlan 666"
    )
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, running)
    assert any("VLAN 666" in d.detail for d in drift)


def test_missing_vxlan_mapping_is_detected():
    running = MATCHING_RUNNING_CONFIG.replace("   vxlan vlan 100 vni 10100\n", "")
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, running)
    assert any(d.category == "vxlan" for d in drift)


def test_wrong_vni_is_detected():
    running = MATCHING_RUNNING_CONFIG.replace("vni 10100", "vni 99999")
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, running)
    assert any("99999" in d.detail for d in drift)


def test_mlag_peer_address_change_is_detected():
    running = MATCHING_RUNNING_CONFIG.replace("peer-address 169.254.1.2", "peer-address 169.254.1.9")
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, running)
    assert any(d.category == "mlag" for d in drift)


def test_missing_service_baseline_is_detected():
    running = (
        MATCHING_RUNNING_CONFIG.replace("snmp-server community FABRIC-RO ro\n", "")
        .replace("logging host 172.31.30.1\n", "")
        .replace("ntp server vrf MGMT 172.31.30.1\n", "")
        .replace("lldp run\n", "")
    )
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, running)
    categories = {d.category for d in drift}
    assert "services" in categories
    details = " ".join(d.detail for d in drift)
    for expected in ("SNMP", "Syslog", "NTP", "LLDP"):
        assert expected in details


def test_drift_ignores_state_the_plugin_does_not_own():
    """A switch legitimately holds state NetBox does not model."""
    running = MATCHING_RUNNING_CONFIG.replace(
        "interface Ethernet6",
        "interface Ethernet1\n   description uplink\n   no shutdown\n!\ninterface Ethernet6",
    )
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, running)
    assert drift == []


def test_empty_running_config_reports_missing_intent():
    drift = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, "!\nend\n")
    assert len(drift) >= 3
    assert {d.category for d in drift} >= {"vlan", "interface", "services"}


def test_summarize_drift():
    running = MATCHING_RUNNING_CONFIG.replace("   no shutdown", "   shutdown")
    items = day2.detect_drift("demo-leaf-01", INTENT_CONFIG, running)
    summary = day2.summarize_drift(items)
    assert summary["total"] == len(items)
    assert summary["devices"] == ["demo-leaf-01"]
    assert sum(summary["by_category"].values()) == summary["total"]


def test_preview_is_readable():
    plan = day2.plan_add_vlan({"vlan_id": 300, "name": "WEB", "svi": "10.100.300.1/24"})
    text = plan.preview()
    assert "Add VLAN 300" in text
    assert "NetBox (Source of Truth) will change" in text
    assert "demo-leaf-01:" in text
