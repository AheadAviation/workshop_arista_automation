"""Alerts, DHCP scopes and wireless clients: the mock's reads, the checks, the correlation. No NetBox needed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from plants_demo import alerts, change, dashboard, l1, meraki_mock
from plants_demo.meraki_client import MerakiClient, MerakiError


@pytest.fixture
def fresh(plan: dict) -> dict:
    return meraki_mock.build_world(plan)


@pytest.fixture
def mock(fresh: dict):
    server = meraki_mock.start(fresh)
    client = MerakiClient(server.base_url, meraki_mock.DEFAULT_KEY, sleep=lambda _s: None)
    yield fresh, client
    meraki_mock.stop(server)


def by_name(world: dict, name: str) -> dict:
    return next(d for d in world["devices"] if d["name"] == name)


def mx_of(world: dict, site: str) -> dict:
    return by_name(world, f"{site}-fw01")


# ------------------------------------------------------------ mock: clients
def test_an_access_point_reports_its_clients(mock, plan: dict):
    world, client = mock
    found = client.device_clients(by_name(world, "us-plt2-ap01")["serial"])
    assert len(found) == plan["meraki_mock"]["clients"]["us-plt2-ap01"]
    assert {"mac", "ip", "vlan", "usage"} <= set(found[0])


def test_an_offline_access_point_has_no_clients(mock, plan: dict):
    world, client = mock
    down = next(name for name, status in plan["meraki_mock"]["status"].items() if status == "offline" and "-ap" in name)
    assert client.device_clients(by_name(world, down)["serial"]) == []


def test_only_access_points_report_wireless_clients(mock):
    world, client = mock
    with pytest.raises(MerakiError, match="404"):
        client.device_clients(by_name(world, "us-plt2-sw01")["serial"])


def test_every_access_point_has_a_plausible_client_count(fresh: dict):
    counts = fresh["clients"]
    assert counts and all(0 < n < 100 for n in counts.values())
    assert set(counts) == {d["name"] for d in fresh["devices"] if d["productType"] == "wireless"}


# --------------------------------------------------------------- mock: DHCP
def test_the_mx_reports_each_dhcp_scope_with_used_and_free(mock, plan: dict):
    world, client = mock
    scopes = {s["vlanId"]: s for s in client.dhcp_subnets(mx_of(world, "us-plt2")["serial"])}
    cfg = plan["meraki_mock"]["dhcp"]
    assert set(scopes) == set(cfg["vlans"])
    assert scopes[100]["usedCount"] == cfg["used"]["us-plt2"][100]
    assert all(s["usedCount"] + s["freeCount"] == cfg["pool"] for s in scopes.values())
    assert scopes[100]["subnet"] == "10.2.100.0/24"


def test_a_site_whose_server_does_dhcp_has_no_mx_scopes(mock):
    world, client = mock
    assert client.dhcp_subnets(mx_of(world, "us-hq")["serial"]) == []


def test_an_office_site_has_no_ot_scope(fresh: dict, plan: dict):
    office = next(s for s in plan["sites"] if s["kind"] != "plant" and s["code"] not in plan["meraki_mock"]["dhcp"]["relay"])
    vlans = {s["vlanId"] for s in fresh["dhcp"][meraki_mock.network_id(office["code"])]}
    assert vlans and 100 not in vlans


def test_dhcp_scopes_come_only_from_an_mx(mock):
    world, client = mock
    with pytest.raises(MerakiError, match="404"):
        client.dhcp_subnets(by_name(world, "us-plt2-ap01")["serial"])


def test_a_dhcp_override_for_an_unknown_site_is_refused(plan: dict):
    broken = json.loads(json.dumps(plan))
    broken["meraki_mock"]["dhcp"]["used"]["nowhere"] = {10: 1}
    with pytest.raises(ValueError, match="nowhere"):
        meraki_mock.build_world(broken)


@pytest.mark.parametrize(
    ("used", "free", "expected"),
    [(10, 90, (10, "ok")), (80, 20, (80, "filling")), (149, 3, (98, "full")), (0, 0, (100, "full"))],
)
def test_scope_levels(used: int, free: int, expected: tuple[int, str]):
    assert l1.scope_level(used, free, 80, 95) == expected


# ------------------------------------------------------- dashboard state
def test_an_access_point_with_no_clients_survives_between_commands(plan: dict, tmp_path: Path):
    state = tmp_path / "state.json"
    with dashboard.Dashboard(plan, state) as dash:
        dash.inject_clients("us-plt2-ap03", 0)
    with dashboard.Dashboard(plan, state) as dash:
        assert dash.client_counts(["us-plt2-ap03", "us-plt2-sw01"]) == {"us-plt2-ap03": 0}
        dash.inject_clients("us-plt2-ap03", None)
    assert not state.exists(), "back to normal leaves nothing to save"


def test_only_access_points_can_lose_their_clients(plan: dict, tmp_path: Path):
    with dashboard.Dashboard(plan, tmp_path / "s.json") as dash, pytest.raises(LookupError):
        dash.inject_clients("us-plt2-sw01", 0)


# ------------------------------------------------------- the change checks
def record(**over) -> dict:
    base = {
        "to_version": "wireless-31-1",
        "scope": [{"name": "ap01", "firmware": "w30", "status": "online", "lan_ip": ""}],
        "downstream": [],
        "clients_baseline": {"ap01": 10, "ap02": 8, "ap03": 0},
    }
    return {**base, **over}


AFTER = {"ap01": {"firmware": "wireless-31-1", "status": "online", "lan_ip": ""}}


def check(checks: list[dict], check_id: str) -> dict:
    return next(c for c in checks if c["id"] == check_id)


def test_clients_coming_back_passes():
    got = check(change.evaluate(record(), AFTER, {}, {"ap01": 9, "ap02": 8, "ap03": 0}), "clients-back")
    assert got["status"] == "pass" and "17 client(s)" in got["detail"]


def test_an_access_point_nobody_can_join_fails_the_change():
    checks = change.evaluate(record(), AFTER, {}, {"ap01": 10, "ap02": 0, "ap03": 0})
    assert check(checks, "clients-back")["status"] == "fail"
    assert "ap02 had 8" in check(checks, "clients-back")["detail"]
    assert change.verdict_of(checks) == "FAIL"


def test_half_the_clients_missing_is_a_warning():
    got = check(change.evaluate(record(), AFTER, {}, {"ap01": 4, "ap02": 8}), "clients-back")
    assert got["status"] == "warn" and "ap01 10 -> 4" in got["detail"]


def test_an_access_point_that_had_no_clients_is_not_blamed():
    got = check(change.evaluate(record(), AFTER, {}, {"ap01": 10, "ap02": 8}), "clients-back")
    assert got["status"] == "pass"


def test_no_client_check_without_a_baseline_or_a_reading():
    assert "clients-back" not in {c["id"] for c in change.evaluate(record(), AFTER, {})}
    assert "clients-back" not in {c["id"] for c in change.evaluate(record(clients_baseline={}), AFTER, {}, {})}


def unmanaged(name: str, *owners: str) -> dict:
    return {
        "name": name,
        "managed": False,
        "baseline_status": "",
        "owner": ", ".join(f"{o} (Owner)" for o in owners) or "nobody recorded",
        "owner_names": list(owners),
    }


def test_owners_who_confirmed_close_the_check():
    rec = record(
        downstream=[unmanaged("mes01", "Raj Patel"), unmanaged("dc01", "Nora Fields")],
        confirmations=[{"name": "Raj Patel"}, {"name": "Nora Fields"}],
    )
    got = check(change.evaluate(rec, AFTER, {}), "owner-confirms")
    assert got["status"] == "pass" and "confirmed by Raj Patel: mes01" in got["detail"]


def test_an_owner_still_to_confirm_keeps_it_open():
    rec = record(downstream=[unmanaged("mes01", "Raj Patel"), unmanaged("dc01", "Nora Fields")], confirmations=[{"name": "Raj Patel"}])
    got = check(change.evaluate(rec, AFTER, {}), "owner-confirms")
    assert got["status"] == "pending" and "ask Nora Fields (Owner): dc01" in got["detail"]


def test_a_device_nobody_owns_can_never_be_confirmed():
    rec = record(downstream=[unmanaged("legacy01")], confirmations=[{"name": "Raj Patel"}])
    got = check(change.evaluate(rec, AFTER, {}), "owner-confirms")
    assert got["status"] == "pending" and "nobody owns legacy01" in got["detail"]


def test_one_owner_confirming_covers_a_device_with_two_owners():
    rec = record(downstream=[unmanaged("hmi01", "Carla Mendez", "Raj Patel")], confirmations=[{"name": "Raj Patel"}])
    assert check(change.evaluate(rec, AFTER, {}), "owner-confirms")["status"] == "pass"


def test_records_from_before_owner_names_still_work():
    old = {"name": "mes01", "managed": False, "baseline_status": "", "owner": "Raj Patel (Owner)"}
    rec = record(downstream=[old], confirmations=[{"name": "Raj Patel"}])
    assert check(change.evaluate(rec, AFTER, {}), "owner-confirms")["status"] == "pass"


# ------------------------------------------------------------------ alerts
def write_change(folder: Path, change_id: str, applied: datetime, site: str, scope: list[str], behind: list[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    rec = {
        "id": change_id,
        "summary": f"summary of {change_id}",
        "state": "verified",
        "site": {"slug": site},
        "applied": {"at": applied.isoformat(timespec="seconds")},
        "scope": [{"name": n} for n in scope],
        "downstream": [{"name": n} for n in behind],
    }
    (folder / f"{change_id}.json").write_text(json.dumps(rec), encoding="utf-8")


def test_recent_changes_say_how_they_touched_the_device(tmp_path: Path):
    now = datetime.now(timezone.utc)
    write_change(tmp_path, "CHG-1", now - timedelta(hours=1), "us-plt2", ["ap03"], [])
    write_change(tmp_path, "CHG-2", now - timedelta(hours=2), "us-plt2", ["sw01"], ["ap03"])
    write_change(tmp_path, "CHG-3", now - timedelta(hours=3), "us-plt2", ["fw01"], [])
    write_change(tmp_path, "CHG-4", now - timedelta(hours=1), "us-hq", ["sw02"], [])
    write_change(tmp_path, "CHG-5", now - timedelta(days=5), "us-plt2", ["ap03"], [])
    (tmp_path / "CHG-1-evidence.json").write_text("{}", encoding="utf-8")
    found = {c["id"]: c["how"] for c in alerts.recent_changes("ap03", "us-plt2", now - timedelta(hours=72), tmp_path)}
    assert found == {
        "CHG-1": "changed this device",
        "CHG-2": "changed what this device sits behind",
        "CHG-3": "changed something at the same site",
    }


def test_a_change_that_was_only_planned_is_not_a_suspect(tmp_path: Path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "CHG-9.json").write_text(json.dumps({"id": "CHG-9", "scope": [{"name": "ap03"}], "site": {"slug": "x"}}), encoding="utf-8")
    assert alerts.recent_changes("ap03", "x", datetime.now(timezone.utc) - timedelta(days=1), tmp_path) == []


def test_earlier_incidents_on_the_same_device_are_counted(tmp_path: Path):
    now = datetime.now(timezone.utc)
    for number, (device, age) in enumerate([("ap03", 1), ("ap03", 40), ("ap01", 1)], 1):
        inc = {
            "id": f"INC-{number}",
            "opened": (now - timedelta(days=age)).isoformat(),
            "device": {"name": device},
            "alert": {"type": "APs went down", "occurred": "", "data": {}},
        }
        alerts.save_incident({**inc, **_blank_incident()}, tmp_path)
    found = alerts.earlier_incidents("ap03", now - timedelta(days=30), tmp_path)
    assert [e["id"] for e in found] == ["INC-1"]


def _blank_incident() -> dict:
    return {
        "in_netbox": False,
        "priority": "P3",
        "site": {"name": "", "slug": ""},
        "changes": [],
        "earlier": [],
        "affected": [],
        "notify": [],
        "steps": [],
        "assign_to": "",
        "escalate_to": "",
        "likely_cause": "",
    }


@pytest.mark.parametrize(
    ("device", "behind", "expected"),
    [("low", [], "P4"), ("high", [], "P2"), ("low", ["critical"], "P1"), ("", [], "P3"), ("medium", ["low", "high"], "P2")],
)
def test_priority_is_the_worst_of_the_device_and_what_it_took_down(device: str, behind: list[str], expected: str):
    assert alerts.priority_of(device, [{"criticality": c} for c in behind]) == expected


def test_a_webhook_names_the_device_the_way_meraki_would(fresh: dict):
    body = alerts.webhook(fresh, by_name(fresh, "us-plt2-ap03"), "APs went down", "critical", {})
    assert body["deviceSerial"] == by_name(fresh, "us-plt2-ap03")["serial"]
    assert body["networkName"] == "Greenville Plant 2"
    assert body["alertData"]["simulated"] is True
    assert body["organizationId"] == fresh["org"]["id"]


def test_ids_count_up_within_a_day(tmp_path: Path):
    day = datetime(2026, 10, 20, tzinfo=timezone.utc)
    first = alerts.new_id(day, tmp_path)
    alerts.save_incident({"id": first, "opened": day.isoformat(), "device": {"name": "x"}, "alert": {"type": "t", "occurred": "", "data": {}}, **_blank_incident()}, tmp_path)
    assert (first, alerts.new_id(day, tmp_path)) == ("INC-20261020-001", "INC-20261020-002")


def test_a_device_netbox_does_not_know_still_gets_a_ticket_saying_so(tmp_path: Path):
    inc = {
        "id": "INC-1",
        "opened": "2026-10-20T10:00:00+00:00",
        "device": {"name": "GVL-PACK-SW01", "serial": "Q2KD", "model": "MS120-24P"},
        "alert": {"type": "Switches went down", "occurred": "", "data": {"simulated": True}},
        **_blank_incident(),
        "steps": ["Add GVL-PACK-SW01 to NetBox"],
    }
    text = alerts.render_ticket(inc)
    assert "not in NetBox" in text and "Add GVL-PACK-SW01 to NetBox" in text
    assert "simulated" not in text
