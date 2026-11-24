"""Intended-state reader tests (INIT-001/SPEC-002).

A fake NetBox and the repo's real AVD group_vars, so both readers are exercised without
anything running.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.contracts import Intent
from workbench.intent_source import (
    BASE_VLANS,
    DEMO_TRUNKS,
    FRR_INTENT_FILE,
    IntentSourceError,
    NEW_VLAN,
    add_vlan_intent,
    read_fabric_intent,
    read_frr_intent,
)


def fake_netbox(devices: list[dict]) -> object:
    """A getter returning one device list for the device endpoint."""

    def get(path: str) -> dict:
        assert "/api/dcim/devices/" in path
        return {"count": len(devices), "next": None, "previous": None, "results": devices}

    return get


def device(name: str, asn: int = 65001, role: str = "leaf") -> dict:
    return {
        "name": name,
        "role": {"slug": role, "name": role.title()},
        "custom_fields": {"bgp_asn": asn, "router_id": f"10.255.0.{name[-2:]}"},
    }


# --------------------------------------------------------------------------- #
# ac-1 — the intended state comes out of NetBox, deterministically
# --------------------------------------------------------------------------- #


def test_reads_every_device_in_the_fabric():
    intended = read_fabric_intent(fake_netbox([device("leaf-01"), device("spine-01")]))
    assert set(intended) == {"leaf-01", "spine-01"}
    assert all(isinstance(i, Intent) for i in intended.values())


def test_intent_carries_the_asn_from_netbox():
    intended = read_fabric_intent(fake_netbox([device("leaf-01", asn=65001)]))
    assert intended["leaf-01"].asn == 65001


def test_intent_carries_the_fabric_source_so_the_page_can_say_where_it_came_from():
    """With nothing modelled in IPAM, the source says so — it does not pretend to be IPAM.

    The page shows this string, so a fallback that claimed `netbox:ipam` would be the tool
    lying about where its truth came from, which is precisely the sin this initiative exists
    to remove.
    """
    intended = read_fabric_intent(fake_netbox([device("leaf-01")]))
    source = intended["leaf-01"].source
    assert source.startswith("netbox:dc1-fabric")
    assert "declared overlay" in source, f"the fallback must announce itself, got {source!r}"


def test_reading_twice_gives_identical_results():
    get = fake_netbox([device("leaf-01"), device("leaf-02")])
    first = read_fabric_intent(get)
    second = read_fabric_intent(get)
    assert {k: v.to_dict() for k, v in first.items()} == {k: v.to_dict() for k, v in second.items()}


def test_trunks_are_the_demo_overlay_and_carry_the_tenant_vlans():
    intended = read_fabric_intent(fake_netbox([device("leaf-01")]))
    leaf = intended["leaf-01"]
    assert leaf.trunks == DEMO_TRUNKS["leaf-01"]
    for port in leaf.trunks:
        assert set(leaf.trunk_allowed[port]) == set(BASE_VLANS)


def test_a_spine_has_no_server_trunks():
    """The overlay is deliberately leaf-only; a spine is a fabric device."""
    intended = read_fabric_intent(fake_netbox([device("spine-01", role="spine")]))
    assert intended["spine-01"].trunks == ()


def test_empty_fabric_is_an_error_not_an_empty_healthy_result():
    with pytest.raises(IntentSourceError, match="no devices"):
        read_fabric_intent(fake_netbox([]))


def test_malformed_netbox_response_is_an_error():
    def get(path: str) -> dict:
        return {"detail": "not found"}

    with pytest.raises(IntentSourceError, match="no 'results' list"):
        read_fabric_intent(get)


def test_device_without_a_name_is_skipped_rather_than_producing_a_blank_card():
    intended = read_fabric_intent(fake_netbox([{"name": "", "custom_fields": {}}, device("leaf-01")]))
    assert set(intended) == {"leaf-01"}


# --------------------------------------------------------------------------- #
# the proposed change (the Philips VLAN)
# --------------------------------------------------------------------------- #


def test_add_vlan_intent_adds_the_vlan_to_every_trunk():
    intended = read_fabric_intent(fake_netbox([device("leaf-01")]))
    proposed = add_vlan_intent(intended, NEW_VLAN)
    leaf = proposed["leaf-01"]
    assert NEW_VLAN in leaf.vlans
    for port in leaf.trunks:
        assert NEW_VLAN in leaf.trunk_allowed[port]


def test_add_vlan_intent_is_pure():
    intended = read_fabric_intent(fake_netbox([device("leaf-01")]))
    add_vlan_intent(intended, NEW_VLAN)
    assert NEW_VLAN not in intended["leaf-01"].vlans


def test_add_vlan_intent_is_idempotent():
    intended = read_fabric_intent(fake_netbox([device("leaf-01")]))
    once = add_vlan_intent(intended, NEW_VLAN)
    twice = add_vlan_intent(once, NEW_VLAN)
    assert once["leaf-01"].vlans == twice["leaf-01"].vlans


def test_proposed_intent_is_a_superset_of_current():
    intended = read_fabric_intent(fake_netbox([device("leaf-01")]))
    proposed = add_vlan_intent(intended, NEW_VLAN)
    assert set(intended["leaf-01"].vlans) < set(proposed["leaf-01"].vlans)


# --------------------------------------------------------------------------- #
# the FRR track, read from its real AVD source
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not FRR_INTENT_FILE.exists(), reason="dual-DC AVD track not present")
def test_frr_intent_reads_the_real_avd_group_vars():
    intent = read_frr_intent()
    assert intent.device == "frr-core1"
    assert intent.platform == "frr"
    assert intent.asn == 65000
    assert intent.source.startswith("avd:")


@pytest.mark.skipif(not FRR_INTENT_FILE.exists(), reason="dual-DC AVD track not present")
def test_frr_intent_carries_no_tenant_vlans():
    """A route reflector has no server VLANs; claiming otherwise would be a lie on screen."""
    intent = read_frr_intent()
    assert intent.vlans == ()
    assert intent.trunks == ()


def test_frr_intent_missing_file_is_a_plain_error(tmp_path: Path):
    with pytest.raises(IntentSourceError, match="could not read the FRR intent"):
        read_frr_intent(tmp_path / "nope.yml")


def test_frr_intent_from_a_synthetic_file(tmp_path: Path):
    path = tmp_path / "CORE.yml"
    path.write_text(
        "frr_core:\n  defaults:\n    bgp_as: 65123\n    loopback_ipv4_pool: 10.251.9.0/24\n",
        encoding="utf-8",
    )
    intent = read_frr_intent(path)
    assert intent.asn == 65123
    assert intent.router_id == "10.251.9.0/24"


# --------------------------------------------------------------------------- #
# ac-7 — no new container, no new credential
# --------------------------------------------------------------------------- #


def test_netbox_token_travels_in_a_header_not_the_url(monkeypatch):
    """A token in a URL ends up in logs and exception text; a header does not."""
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"results": []}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResponse()

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    from workbench.intent_source import netbox_getter

    netbox_getter(url="http://nb.example", token="secret-token-123")("/api/dcim/devices/")
    assert "secret-token-123" not in captured["url"]
    assert captured["headers"]["Authorization"] == "Token secret-token-123"


def test_unreachable_netbox_is_a_plain_error(monkeypatch):
    import requests

    def fake_get(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr(requests, "get", fake_get)
    from workbench.intent_source import netbox_getter

    with pytest.raises(IntentSourceError, match="could not reach NetBox"):
        netbox_getter(url="http://nb.example", token="t")("/api/dcim/devices/")


# --------------------------------------------------------------------------- #
# IPAM takes precedence over the declared overlay
# --------------------------------------------------------------------------- #
# The point of the whole initiative: when NetBox has VLANs modelled, those VLANs — and the
# interfaces that carry them — are the intent. The tuple in Python is a bootstrap fallback and
# must not win once real data exists.


def test_intent_comes_from_ipam_when_ipam_has_vlans():
    def get(path):
        if path.startswith("/api/dcim/devices/"):
            return {"results": [{"name": "leaf-01", "role": {"slug": "leaf"}, "custom_fields": {}}]}
        if path.startswith("/api/ipam/vlans/"):
            return {"results": [{"vid": 100, "name": "SERVERS"}, {"vid": 150, "name": "PHILIPS"}]}
        if path.startswith("/api/dcim/interfaces/"):
            return {
                "results": [
                    {"name": "eth5", "mode": {"value": "tagged", "label": "Tagged"},
                     "tagged_vlans": [{"vid": 100}, {"vid": 150}]},
                ]
            }
        raise AssertionError(f"unexpected {path}")

    intended = read_fabric_intent(get)

    assert intended["leaf-01"].source == "netbox:ipam", "IPAM must win over the overlay"
    assert set(intended["leaf-01"].vlans) == {100, 150}
    assert intended["leaf-01"].trunks == ("Ethernet5",)


def test_ipam_intent_carries_only_what_ipam_says_not_the_whole_base_set():
    """With IPAM authoritative, a trunk carrying one VLAN must not report five."""
    def get(path):
        if path.startswith("/api/dcim/devices/"):
            return {"results": [{"name": "leaf-01", "role": {"slug": "leaf"}, "custom_fields": {}}]}
        if path.startswith("/api/ipam/vlans/"):
            return {"results": [{"vid": 100, "name": "SERVERS"}]}
        if path.startswith("/api/dcim/interfaces/"):
            return {
                "results": [
                    {"name": "eth5", "mode": {"value": "tagged", "label": "Tagged"},
                     "tagged_vlans": [{"vid": 100}]},
                ]
            }
        raise AssertionError(f"unexpected {path}")

    intended = read_fabric_intent(get)
    assert intended["leaf-01"].vlans == (100,)


def test_an_empty_ipam_falls_back_and_says_so():
    """A fresh install has nothing modelled; the fallback must announce itself, not pretend."""

    def get(path):
        if path.startswith("/api/dcim/devices/"):
            return {"results": [{"name": "leaf-01", "role": {"slug": "leaf"}, "custom_fields": {}}]}
        if path.startswith("/api/ipam/vlans/"):
            return {"results": []}
        if path.startswith("/api/dcim/interfaces/"):
            return {"results": []}
        raise AssertionError(f"unexpected {path}")

    intended = read_fabric_intent(get)
    assert "declared overlay" in intended["leaf-01"].source
    assert intended["leaf-01"].vlans == BASE_VLANS
