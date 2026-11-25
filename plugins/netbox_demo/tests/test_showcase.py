"""Pure unit tests for the Source-of-Record showcase validators and layout tables."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "netbox_demo"))

from netbox_demo import design, showcase  # noqa: E402


def test_every_fabric_switch_has_unique_rack_slot():
    slots = set()
    for dev in design.ALL_DEVICES:
        assert dev in showcase.SWITCH_RACK_POSITIONS
        slot = showcase.SWITCH_RACK_POSITIONS[dev]
        assert slot not in slots
        slots.add(slot)


def test_servers_do_not_collide_with_switch_or_pdu_positions():
    occupied: dict[str, set[int]] = {}
    for rack, _, _ in showcase.RACKS:
        occupied[rack] = {1, 2}  # U1-U2 reserved for PDUs
    for rack, pos in showcase.SWITCH_RACK_POSITIONS.values():
        occupied[rack].add(pos)
    for srv in showcase.SERVERS:
        for u in (srv.position, srv.position + 1):
            assert u not in occupied[srv.rack], f"Collision in {srv.rack} at U{u}"
            occupied[srv.rack].add(u)


def test_topology_coordinates_cover_all_devices_circuits_and_power():
    for dev in design.ALL_DEVICES:
        assert dev in showcase.TOPOLOGY_DEVICE_COORDS
    for srv in showcase.SERVERS:
        assert srv.name in showcase.TOPOLOGY_DEVICE_COORDS
    for circ in showcase.CIRCUITS:
        assert circ.cid in showcase.TOPOLOGY_CIRCUIT_COORDS
    for feed_name, panel_name, *_ in showcase.POWER_LAYOUT:
        assert feed_name in showcase.TOPOLOGY_FEED_COORDS
        assert panel_name in showcase.TOPOLOGY_PANEL_COORDS


def test_validate_circuit_input_accepts_valid_and_rejects_bad():
    res = showcase.validate_circuit_input(
        cid="cid-att-10g-003",
        provider="AT&T Business",
        commit_mbps="10000",
        device="demo-spine-02",
        interface="Ethernet6",
    )
    assert res["cid"] == "CID-ATT-10G-003"
    assert res["commit_kbps"] == 10_000_000

    with pytest.raises(ValueError):
        showcase.validate_circuit_input(
            cid="bad id!", provider="AT&T", commit_mbps=1000, device="demo-spine-01", interface="Ethernet5"
        )
    with pytest.raises(ValueError):
        showcase.validate_circuit_input(
            cid="CID-OK-1", provider="AT&T", commit_mbps=1000, device="not-a-switch", interface="Ethernet5"
        )


def test_validate_power_feed_input():
    res = showcase.validate_power_feed_input(
        name="feed-a01-c",
        panel="PANEL-A (Utility A)",
        rack="RACK-A01",
        feed_type="redundant",
        voltage="208",
        amperage="60",
    )
    assert res["name"] == "FEED-A01-C"
    assert res["voltage"] == 208
    assert res["amperage"] == 60

    with pytest.raises(ValueError):
        showcase.validate_power_feed_input(
            name="FEED-BAD", panel="PANEL-A", rack="RACK-A01", feed_type="tertiary", voltage=208, amperage=30
        )


def test_validate_rack_device_input_prefixes_and_bounds():
    res = showcase.validate_rack_device_input(
        name="fw-01",
        role="Firewall",
        rack="RACK-A01",
        position="12",
        u_height="2",
        uplink_leaf="demo-leaf-01",
        uplink_port="Ethernet8",
    )
    assert res["name"] == "demo-fw-01"
    assert res["position"] == 12

    with pytest.raises(ValueError):
        showcase.validate_rack_device_input(
            name="demo-fw-02", role="Firewall", rack="RACK-A01", position=1, u_height=2
        )
