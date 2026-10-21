"""Change workflow, level-1 actions, and the mock dashboard's write side. No NetBox needed."""

from __future__ import annotations

import ipaddress
from datetime import datetime
from pathlib import Path

import pytest
from plants_demo import change, dashboard, l1, meraki_mock
from plants_demo.meraki_client import MerakiClient, MerakiError


@pytest.fixture
def fresh(plan: dict) -> dict:
    """A world nobody else has touched (the shared one must stay pristine)."""
    return meraki_mock.build_world(plan)


@pytest.fixture
def mock(fresh: dict):
    server = meraki_mock.start(fresh)
    client = MerakiClient(server.base_url, meraki_mock.DEFAULT_KEY, sleep=lambda _s: None)
    yield fresh, client
    meraki_mock.stop(server)


def by_name(world: dict, name: str) -> dict:
    return next(d for d in world["devices"] if d["name"] == name)


# -------------------------------------------------------------- change windows
def at(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M")


# 2026-10-25 is a Sunday
def test_a_weekly_window_opens_only_on_its_day():
    window = change.parse_window("Sun 01:00-05:00 ET")
    assert change.in_window(window, at("2026-10-25 01:00"))
    assert change.in_window(window, at("2026-10-25 04:59"))
    assert not change.in_window(window, at("2026-10-25 05:00"))
    assert not change.in_window(window, at("2026-10-26 02:00"))


def test_a_daily_window_opens_every_day():
    window = change.parse_window("Daily 20:00-23:00 CT")
    assert change.in_window(window, at("2026-10-20 21:00"))
    assert not change.in_window(window, at("2026-10-20 19:59"))


def test_an_overnight_window_runs_into_the_next_morning():
    window = change.parse_window("Sat 22:00-02:00 MT")
    assert change.in_window(window, at("2026-10-24 23:30"))  # Saturday night
    assert change.in_window(window, at("2026-10-25 01:30"))  # Sunday small hours
    assert not change.in_window(window, at("2026-10-25 02:00"))
    assert not change.in_window(window, at("2026-10-24 21:59"))
    assert not change.in_window(window, at("2026-10-26 01:30"))  # Monday


def test_the_next_opening_is_after_now_and_on_the_right_day():
    window = change.parse_window("Sun 01:00-05:00 ET")
    assert change.next_window(window, at("2026-10-20 10:00")) == at("2026-10-25 01:00")
    assert change.next_window(window, at("2026-10-25 02:00")) == at("2026-11-01 01:00")
    daily = change.parse_window("Daily 20:00-23:00 CT")
    assert change.next_window(daily, at("2026-10-20 21:00")) == at("2026-10-21 20:00")


def test_every_window_in_the_sample_data_can_be_read(plan: dict):
    for site in plan["sites"]:
        text = site.get("change_window")
        if text:
            assert change.parse_window(text) is not None, f"{site['code']}: {text}"


def test_a_site_with_no_window_is_told_to_negotiate():
    verdict = change.window_verdict("", at("2026-10-20 10:00"))
    assert verdict["verdict"] == "none" and "negotiate" in verdict["detail"]
    assert change.window_verdict("sometime Tuesday", at("2026-10-20 10:00"))["verdict"] == "none"


def test_the_verdict_names_the_next_opening_when_it_is_closed():
    verdict = change.window_verdict("Sun 01:00-05:00 ET", at("2026-10-20 10:00"))
    assert verdict["verdict"] == "outside" and "2026-10-25 01:00" in verdict["detail"]
    assert change.window_verdict("Sun 01:00-05:00 ET", at("2026-10-25 02:00"))["verdict"] == "inside"


def test_a_typed_time_is_read_in_the_sites_zone_and_a_bad_one_is_explained():
    moment = change.site_clock("America/New_York", "2026-10-25 01:30")
    assert moment.hour == 1 and moment.tzinfo is not None
    with pytest.raises(change.ChangeError, match="YYYY-MM-DD HH:MM"):
        change.site_clock("America/New_York", "next sunday")


# ------------------------------------------------------------------ the checks
def record(**over) -> dict:
    base = {
        "to_version": "switch-16-8",
        "scope": [
            {"name": "sw01", "firmware": "switch-15", "status": "online", "lan_ip": "10.1.99.11"},
            {"name": "sw02", "firmware": "switch-15", "status": "online", "lan_ip": "10.1.99.12"},
        ],
        "downstream": [
            {"name": "ap01", "managed": True, "baseline_status": "online", "owner": "Tom"},
            {"name": "ap02", "managed": True, "baseline_status": "offline", "owner": "Tom"},
            {"name": "mes01", "managed": False, "baseline_status": "", "owner": "Raj"},
        ],
    }
    return {**base, **over}


def good_after() -> dict:
    return {
        "sw01": {"firmware": "switch-16-8", "status": "online", "lan_ip": "10.1.99.11"},
        "sw02": {"firmware": "switch-16-8", "status": "online", "lan_ip": "10.1.99.12"},
        "ap01": {"firmware": "w", "status": "online", "lan_ip": ""},
        "ap02": {"firmware": "w", "status": "offline", "lan_ip": ""},
    }


def results(checks: list[dict]) -> dict[str, str]:
    return {c["id"]: c["status"] for c in checks}


def test_a_clean_change_passes_and_asks_the_owner_about_what_it_cannot_see():
    checks = change.evaluate(record(), good_after(), {"sw01": "10.1.99.11", "sw02": "10.1.99.12"})
    got = results(checks)
    assert change.verdict_of(checks) == "PASS"
    assert got["firmware"] == got["scope-online"] == got["ip-matches-netbox"] == "pass"
    assert got["downstream-online"] == "warn", "ap02 was already offline: noted, not blamed"
    assert got["owner-confirms"] == "pending"
    assert "Raj: mes01" in next(c for c in checks if c["id"] == "owner-confirms")["detail"]


def test_a_device_that_was_up_and_is_now_down_fails_the_change():
    after = good_after()
    after["ap01"]["status"] = "offline"
    checks = change.evaluate(record(), after, {})
    assert results(checks)["downstream-online"] == "fail"
    assert change.verdict_of(checks) == "FAIL"
    assert "ap01 was online before and is offline now" in checks[2]["detail"]


def test_a_device_still_on_the_old_firmware_fails_the_change():
    after = good_after()
    after["sw02"]["firmware"] = "switch-15"
    checks = change.evaluate(record(), after, {})
    assert results(checks)["firmware"] == "fail"
    assert "sw02 is on switch-15" in checks[0]["detail"]


def test_a_device_that_vanished_from_the_dashboard_fails_the_change():
    after = good_after()
    del after["sw01"]
    assert change.verdict_of(change.evaluate(record(), after, {})) == "FAIL"


def test_an_ip_that_netbox_disagrees_with_is_a_warning_not_a_failure():
    checks = change.evaluate(record(), good_after(), {"sw01": "10.1.99.99"})
    assert results(checks)["ip-matches-netbox"] == "warn"
    assert change.verdict_of(checks) == "PASS"


def test_a_device_that_was_already_down_does_not_fail_the_change():
    rec = record()
    rec["scope"][0]["status"] = "offline"
    after = good_after()
    after["sw01"]["status"] = "offline"
    assert results(change.evaluate(rec, after, {}))["scope-online"] == "warn"


def test_the_owner_check_is_left_out_when_the_dashboard_sees_everything():
    rec = record(downstream=[])
    assert "owner-confirms" not in results(change.evaluate(rec, good_after(), {}))
    assert "downstream-online" not in results(change.evaluate(rec, good_after(), {}))


# ----------------------------------------------------------------- change ids
def test_change_ids_count_up_within_a_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(change, "CHANGES_DIR", tmp_path)
    today = at("2026-10-20 10:00")
    assert change.new_id(today) == "CHG-20261020-001"
    (tmp_path / "CHG-20261020-001.json").write_text("{}")
    assert change.new_id(today) == "CHG-20261020-002"
    assert change.new_id(at("2026-10-21 10:00")) == "CHG-20261021-001"


def test_an_unknown_change_is_explained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(change, "CHANGES_DIR", tmp_path)
    with pytest.raises(change.ChangeError, match="change plan"):
        change.load_record("CHG-20990101-001")


def test_only_a_recorded_approver_can_approve():
    rec = {"approvals_required": [{"name": "Priya Nair", "label": "Priya Nair (Senior Network Engineer)", "for": []}]}
    assert change._find_approver(rec, "Priya Nair")
    assert change._find_approver(rec, "priya")
    assert change._find_approver(rec, "Dana Ortiz") is None


# --------------------------------------------------------- saved dashboard state
def test_a_fresh_dashboard_has_nothing_to_save(plan: dict):
    assert dashboard.diff_state(meraki_mock.build_world(plan), meraki_mock.build_world(plan)) == {}


def test_changes_survive_between_commands(plan: dict, tmp_path: Path):
    path = tmp_path / "state.json"
    with dashboard.Dashboard(plan, path) as dash:
        serial = by_name(dash.world, "us-hq-sw02")["serial"]
        dash.client.update_switch_port(serial, "12", vlan=100)
        dash.client.upgrade_firmware(by_name(dash.world, "us-hq-sw02")["networkId"], "switch", "switch-99")
        dash.inject_status("us-hq-ap01", "offline")
    assert path.exists()

    with dashboard.Dashboard(plan, path) as dash:  # a later command
        assert dash.client.switch_port(serial, "12")["vlan"] == 100
        assert dash.snapshot()["us-hq-sw02"]["firmware"] == "switch-99"
        assert dash.snapshot()["us-hq-ap01"]["status"] == "offline"
        assert dash.snapshot()["us-plt2-sw01"]["firmware"] != "switch-99", "other sites untouched"

    assert dashboard.reset_state(path) and not path.exists()
    assert not dashboard.reset_state(path)
    with dashboard.Dashboard(plan, path) as dash:
        assert dash.client.switch_port(serial, "12")["vlan"] == meraki_mock.DEFAULT_PORT_VLAN


def test_the_sync_sees_what_a_change_did(plan: dict, tmp_path: Path):
    path = tmp_path / "state.json"
    with dashboard.Dashboard(plan, path) as dash:
        dash.client.upgrade_firmware(by_name(dash.world, "us-hq-sw02")["networkId"], "switch", "switch-99")
    assert by_name(dashboard.load_world(plan, path), "us-hq-sw02")["firmware"] == "switch-99"


def test_a_state_file_naming_devices_that_are_gone_is_ignored(plan: dict):
    world = dashboard.apply_state(
        meraki_mock.build_world(plan),
        {"firmware": {"no-such-device": "x"}, "status": {"nobody": "offline"}, "ports": {"Q2XX-0000-0000": {"1": {"vlan": 5}}}},
    )
    assert world["devices"]


# ------------------------------------------------------ the mock's write endpoints
def test_a_switch_port_starts_in_the_default_vlan_and_can_be_moved(mock):
    world, client = mock
    serial = by_name(world, "us-plt2-sw01")["serial"]
    assert client.switch_port(serial, "12")["vlan"] == meraki_mock.DEFAULT_PORT_VLAN
    moved = client.update_switch_port(serial, "12", vlan=100, name="Time clock")
    assert moved["vlan"] == 100 and moved["name"] == "Time clock"
    assert client.switch_port(serial, "12")["vlan"] == 100
    assert client.switch_port(serial, "13")["vlan"] == meraki_mock.DEFAULT_PORT_VLAN


def test_a_model_name_gives_the_port_count():
    assert meraki_mock.port_count("MS120-24P") == 24
    assert meraki_mock.port_count("MS225-48LP") == 48
    assert meraki_mock.port_count("MR46") == 0


@pytest.mark.parametrize("fields", [{"vlan": 0}, {"vlan": 4095}, {"vlan": "10"}, {"vlan": True}, {"type": "bridge"}, {"enabled": "yes"}])
def test_a_bad_port_value_is_refused(mock, fields):
    world, client = mock
    serial = by_name(world, "us-plt2-sw01")["serial"]
    with pytest.raises(MerakiError, match="HTTP 400"):
        client.update_switch_port(serial, "1", **fields)
    assert client.switch_port(serial, "1")["vlan"] == meraki_mock.DEFAULT_PORT_VLAN


def test_ports_exist_only_on_switches_that_exist(mock):
    world, client = mock
    with pytest.raises(MerakiError, match="HTTP 404"):
        client.switch_port(by_name(world, "us-plt2-ap01")["serial"], "1")  # an access point
    with pytest.raises(MerakiError, match="HTTP 404"):
        client.switch_port("Q2XX-0000-0000", "1")
    with pytest.raises(MerakiError, match="HTTP 404"):
        client.switch_port(by_name(world, "us-plt2-sw01")["serial"], "99")


def test_a_firmware_upgrade_reaches_only_that_product_in_that_network(mock):
    world, client = mock
    network = by_name(world, "us-plt2-sw01")["networkId"]
    before = {d["name"]: d["firmware"] for d in world["devices"]}
    client.upgrade_firmware(network, "switch", "switch-99")
    for device in world["devices"]:
        hit = device["networkId"] == network and device["productType"] == "switch"
        assert (device["firmware"] == "switch-99") == hit, device["name"]
        if not hit:
            assert device["firmware"] == before[device["name"]]


def test_a_bad_firmware_request_is_refused(mock):
    world, client = mock
    network = by_name(world, "us-plt2-sw01")["networkId"]
    with pytest.raises(MerakiError, match="HTTP 400"):
        client._write(f"/networks/{network}/firmwareUpgrades", {})
    with pytest.raises(MerakiError, match="HTTP 400"):
        client._write(f"/networks/{network}/firmwareUpgrades", {"products": {"toaster": {}}})
    with pytest.raises(MerakiError, match="HTTP 404"):
        client.upgrade_firmware("L_000", "switch", "switch-99")


def test_a_wrong_key_cannot_write(fresh):
    server = meraki_mock.start(fresh)
    try:
        client = MerakiClient(server.base_url, "wrong", sleep=lambda _s: None)
        with pytest.raises(MerakiError, match="rejected the API key"):
            client.update_switch_port(by_name(fresh, "us-plt2-sw01")["serial"], "1", vlan=20)
    finally:
        meraki_mock.stop(server)


def test_writes_are_retried_when_rate_limited(fresh):
    server = meraki_mock.start(fresh, rate_limit_every=2)
    try:
        client = MerakiClient(server.base_url, meraki_mock.DEFAULT_KEY, sleep=lambda _s: None)
        serial = by_name(fresh, "us-plt2-sw01")["serial"]
        for vlan in (20, 30, 40):
            assert client.update_switch_port(serial, "1", vlan=vlan)["vlan"] == vlan
        assert client.retries > 0
    finally:
        meraki_mock.stop(server)


# ----------------------------------------------------------------- level 1
def test_the_next_static_address_skips_the_gateway_and_what_is_taken():
    network = ipaddress.ip_network("10.2.100.0/24")
    assert l1.next_static(network, set()) == "10.2.100.100"
    assert l1.next_static(network, {"10.2.100.100", "10.2.100.101"}) == "10.2.100.102"
    assert l1.next_static(network, {f"10.2.100.{n}" for n in range(100, 200)}) is None


def test_the_static_range_stays_clear_of_what_the_sample_hands_out(plan: dict):
    from plants_demo.populate import IP_BASE

    assert max(IP_BASE.values()) + 60 < l1.STATIC_RANGE[0], "room for many devices per kind"


@pytest.mark.parametrize(("text", "expected"), [("12", "12"), ("Port 12", "12"), ("port7", "7")])
def test_port_names_map_to_meraki_port_ids(text: str, expected: str):
    assert l1.port_number(text) == expected


@pytest.mark.parametrize("text", ["Uplink 25", "Management", "ge-0/0/1", ""])
def test_only_access_ports_can_be_named(text: str):
    with pytest.raises(l1.L1Error, match="not an access port"):
        l1.port_number(text)
