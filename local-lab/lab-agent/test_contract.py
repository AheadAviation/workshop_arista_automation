"""Tests for the local lab agent contract and action building.

These run with no Docker, no ContainerLab and no switches: they cover the
security-relevant surface (auth, allowlists, command validation) and the pure
renderers, which is what can silently regress.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import contract  # noqa: E402
import agent  # noqa: E402


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #


def test_bearer_token_accepted():
    assert contract.verify_bearer_token("Bearer s3cret", "s3cret") is True
    assert contract.verify_bearer_token("bearer s3cret", "s3cret") is True


@pytest.mark.parametrize(
    "header",
    [None, "", "s3cret", "Basic s3cret", "Bearer", "Bearer wrong", "Bearer wrong s3cret"],
)
def test_bearer_token_rejected(header):
    assert contract.verify_bearer_token(header, "s3cret") is False


def test_empty_expected_token_never_authorizes():
    """A missing server-side token must fail closed, not accept anything."""
    assert contract.verify_bearer_token("Bearer anything", "") is False


# --------------------------------------------------------------------------- #
# Device allowlist
# --------------------------------------------------------------------------- #


def test_all_fabric_devices_valid():
    for dev in contract.FABRIC_DEVICES:
        assert contract.validate_device(dev) == dev


@pytest.mark.parametrize("dev", ["spine-01", "leaf-01", "", "  ", "demo-leaf-09", "../../etc/passwd"])
def test_unknown_devices_rejected(dev):
    """Bare names belong to another lab; only the prefixed demo names are ours."""
    with pytest.raises(ValueError):
        contract.validate_device(dev)


def test_device_prefix_avoids_collision_with_other_labs():
    assert all(d.startswith("demo-") for d in contract.FABRIC_DEVICES)
    assert "leaf-01" not in contract.FABRIC_DEVICES


def test_mgmt_subnet_does_not_overlap_workshop_lab():
    import ipaddress

    demo = ipaddress.ip_network(contract.MGMT_SUBNET)
    workshop = ipaddress.ip_network("172.30.30.0/24")
    assert not demo.overlaps(workshop)


def test_every_device_has_a_unique_mgmt_ip():
    ips = list(contract.DEVICE_MGMT_IPS.values())
    assert len(ips) == len(set(ips)) == len(contract.FABRIC_DEVICES)


# --------------------------------------------------------------------------- #
# eAPI command validation
# --------------------------------------------------------------------------- #


def test_show_mode_allows_show_and_ping():
    assert contract.validate_eapi_commands(["show version"], "show") == ["show version"]
    assert contract.validate_eapi_commands(["ping 10.0.0.1"], "show") == ["ping 10.0.0.1"]


@pytest.mark.parametrize("cmd", ["configure terminal", "reload now", "bash", "delete flash:x", "copy x y"])
def test_show_mode_rejects_config_and_destructive(cmd):
    with pytest.raises(ValueError):
        contract.validate_eapi_commands([cmd], "show")


@pytest.mark.parametrize("cmd", ["bash", "reload", "write erase", "erase startup-config", "format flash:"])
def test_config_mode_rejects_destructive(cmd):
    with pytest.raises(ValueError):
        contract.validate_eapi_commands([cmd], "config")


def test_config_mode_allows_interface_changes():
    cmds = ["interface Ethernet6", "description Server-Port", "no shutdown"]
    assert contract.validate_eapi_commands(cmds, "config") == cmds


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        contract.validate_eapi_commands(["show version"], "exec")


def test_empty_command_list_rejected():
    with pytest.raises(ValueError):
        contract.validate_eapi_commands([], "show")
    with pytest.raises(ValueError):
        contract.validate_eapi_commands(["", "   "], "show")
    with pytest.raises(ValueError):
        contract.validate_eapi_commands("show version", "show")


# --------------------------------------------------------------------------- #
# Bare config + topology rendering
# --------------------------------------------------------------------------- #


def test_bare_config_has_reachability_but_no_fabric_intent():
    cfg = agent.render_bare_startup_config("demo-leaf-01")
    assert "hostname demo-leaf-01" in cfg
    assert "ip address 172.31.30.21/24" in cfg
    assert "management api http-commands" in cfg
    assert "snmp-server community" in cfg
    assert "lldp run" in cfg
    # No fabric intent: discovery is supposed to discover it, not read it here.
    assert "router bgp" not in cfg
    assert "vxlan" not in cfg.lower().replace("management api", "")


def test_topology_has_six_nodes_and_twelve_links():
    topo = contract.render_topology_yaml()
    for dev in contract.FABRIC_DEVICES:
        assert f"    {dev}:" in topo
    assert topo.count("- endpoints:") == len(contract.FABRIC_LINKS) == 12
    assert f"name: {contract.TOPOLOGY_NAME}" in topo


def test_topology_links_reference_prefixed_devices():
    topo = contract.render_topology_yaml()
    assert '"spine-01:eth1"' not in topo
    assert '"demo-spine-01:eth1"' in topo


# --------------------------------------------------------------------------- #
# Rogue drift scenarios
# --------------------------------------------------------------------------- #


def test_scenarios_are_reversible():
    """Every injected command must have a plausible counterpart so a demo can undo itself."""
    assert contract.ROGUE_DRIFT_SCENARIOS
    for key, scenario in contract.ROGUE_DRIFT_SCENARIOS.items():
        assert scenario["inject"], key
        assert scenario["remediate"], key
        assert any(c.startswith("no ") for c in scenario["remediate"]), (
            f"{key} remediation removes nothing"
        )


def test_rogue_drift_targets_a_leaf():
    assert contract.ROGUE_DRIFT_DEVICE in contract.LEAVES


# --------------------------------------------------------------------------- #
# Action error paths (no Docker required)
# --------------------------------------------------------------------------- #


def test_push_configs_rejects_empty_payload():
    with pytest.raises(ValueError):
        agent.action_push_configs({})
    with pytest.raises(ValueError):
        agent.action_push_configs({"configs": {}})


def test_push_configs_rejects_disallowed_device_before_touching_switches():
    with pytest.raises(ValueError):
        agent.action_push_configs({"configs": {"leaf-01": "hostname leaf-01\n"}})


def test_rogue_drift_rejects_unknown_scenario():
    with pytest.raises(ValueError):
        agent.action_rogue_drift({"scenario": "not-a-real-one"}, remediate=False)


def test_eapi_action_validates_before_calling_switch():
    with pytest.raises(ValueError):
        agent.action_eapi({"device": "leaf-01", "commands": ["show version"], "mode": "show"})
    with pytest.raises(ValueError):
        agent.action_eapi({"device": "demo-leaf-01", "commands": ["reload"], "mode": "config"})
