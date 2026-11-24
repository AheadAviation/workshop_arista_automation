"""Unit tests for the real-world Arista EVPN/VXLAN Day 2 catalogue & resolver (INIT-005/SPEC-001, SPEC-004)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from workbench import operations, operations_resolve  # noqa: E402


SAMPLE_VLANS = [
    {"vid": 100, "name": "SERVERS", "tenant": "CORE", "description": "Server VLAN"},
    {"vid": 110, "name": "STORAGE", "tenant": "CORE", "description": "Storage VLAN"},
]

SAMPLE_LEAVES = [{"name": d, "role": "leaf"} for d in operations_resolve.FABRIC_LEAVES]
SAMPLE_IFACES = {
    d: [{"value": f"Ethernet{n}", "label": f"Ethernet{n}"} for n in (5, 6, 7, 8)]
    for d in operations_resolve.FABRIC_LEAVES
}


def test_catalogue_contains_six_real_world_operations():
    keys = [op.key for op in operations.OPERATIONS]
    assert keys == [
        "provision_vlan",
        "configure_port",
        "provision_mlag_portchannel",
        "update_fabric_services",
        "retire_vlan",
        "remediate_drift",
    ]


def test_describe_all_populates_live_choices():
    data = operations_resolve.describe_all(
        fetch_vlans=lambda: SAMPLE_VLANS,
        fetch_leaves=lambda: SAMPLE_LEAVES,
        fetch_interfaces=lambda: SAMPLE_IFACES,
    )
    assert data["ok"] is True
    assert len(data["operations"]) == 6
    by_key = {op["key"]: op for op in data["operations"]}

    port_fields = {f["key"]: f for f in by_key["configure_port"]["fields"]}
    assert [c["value"] for c in port_fields["device"]["choices"]] == list(operations_resolve.FABRIC_LEAVES)
    assert [c["value"] for c in port_fields["vlan"]["choices"]] == ["100", "110"]
    assert "leaf-01" in port_fields["interface"]["choices_by_device"]


def test_resolve_provision_vlan_valid_and_invalid():
    res = operations_resolve.resolve(
        operation_key="provision_vlan",
        values={"vlan_id": "150", "vlan_name": "APP_TIER", "svi_ip": "10.100.150.1/24", "mtu": "9000"},
        fetch_vlans=lambda: SAMPLE_VLANS,
    )
    assert res.vlan_vid == 150
    assert res.values["vni"] == 10150
    assert res.devices == list(operations_resolve.FABRIC_LEAVES)
    assert "10.100.150.1/24" in res.sentence

    with pytest.raises(operations_resolve.ResolveError):
        operations_resolve.resolve(
            operation_key="provision_vlan",
            values={"vlan_id": "4094", "vlan_name": "BAD_MLAG", "svi_ip": "", "mtu": "9000"},
            fetch_vlans=lambda: SAMPLE_VLANS,
        )

    with pytest.raises(operations_resolve.ResolveError):
        operations_resolve.resolve(
            operation_key="provision_vlan",
            values={"vlan_id": "150", "vlan_name": "APP_TIER", "svi_ip": "not-an-ip", "mtu": "9000"},
            fetch_vlans=lambda: SAMPLE_VLANS,
        )


def test_resolve_configure_port_protects_uplinks_and_mlag_peer_links():
    res = operations_resolve.resolve(
        operation_key="configure_port",
        values={
            "device": "leaf-01",
            "interface": "Ethernet5",
            "mode": "access",
            "vlan": "100",
            "mtu": "9214",
            "admin_state": "up",
            "description": "ESXi-01 NIC0",
        },
        fetch_vlans=lambda: SAMPLE_VLANS,
    )
    assert res.devices == ["leaf-01"]
    assert res.values["mtu"] == 9214

    # Spine uplink Ethernet1 must be refused
    with pytest.raises(operations_resolve.ResolveError):
        operations_resolve.resolve(
            operation_key="configure_port",
            values={
                "device": "leaf-01",
                "interface": "Ethernet1",
                "mode": "access",
                "vlan": "100",
                "mtu": "1500",
                "admin_state": "up",
            },
            fetch_vlans=lambda: SAMPLE_VLANS,
        )


def test_resolve_mlag_portchannel_and_services_and_retire():
    mlag_res = operations_resolve.resolve(
        operation_key="provision_mlag_portchannel",
        values={
            "pair": "pair-1",
            "po_id": "10",
            "member_interface": "Ethernet5",
            "vlan": "110",
            "mtu": "9214",
            "description": "PURE-ARRAY-01",
        },
        fetch_vlans=lambda: SAMPLE_VLANS,
    )
    assert mlag_res.devices == ["leaf-01", "leaf-02"]
    assert mlag_res.values["po_id"] == 10
    assert mlag_res.values["mtu"] == 9214

    svc_res = operations_resolve.resolve(
        operation_key="update_fabric_services",
        values={"service_type": "ntp", "action": "add", "server_ip": "172.30.30.10"},
        fetch_vlans=lambda: SAMPLE_VLANS,
    )
    assert len(svc_res.devices) == 6
    assert "172.30.30.10" in svc_res.sentence

    retire_res = operations_resolve.resolve(
        operation_key="retire_vlan",
        values={"vlan": "100"},
        fetch_vlans=lambda: SAMPLE_VLANS,
    )
    assert retire_res.vlan_vid == 100
    assert retire_res.devices == list(operations_resolve.FABRIC_LEAVES)
