"""Known fixes, polling, circuits, the scan, the OT and estate reports. No NetBox needed."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from plants_demo import alerts, cards, circuits, dashboard, meraki_mock, monitor, ot, report, scan
from plants_demo.common import _same, interface_groups
from plants_demo.meraki_client import MerakiClient, MerakiError
from plants_demo.populate import expand_site

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def fresh(plan: dict) -> dict:
    return meraki_mock.build_world(plan)


@pytest.fixture
def mock(fresh: dict):
    server = meraki_mock.start(fresh)
    client = MerakiClient(server.base_url, meraki_mock.DEFAULT_KEY, sleep=lambda _s: None)
    yield fresh, client
    meraki_mock.stop(server)


def _site(plan: dict, code: str) -> dict:
    return next(s for s in plan["sites"] if s["code"] == code)


def _incident(inc_id: str, device: str, alert_type: str, age_days: float, fix: str = "", cid: str = "") -> dict:
    inc = {
        "id": inc_id,
        "opened": (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat(timespec="seconds"),
        "state": "resolved" if fix else "open",
        "device": {"name": device},
        "alert": {"type": alert_type, "occurred": "", "data": {}},
        "in_netbox": False,
        "priority": "P3",
        "site": {"name": "", "slug": ""},
        "changes": [],
        "earlier": [],
        "fixes": [],
        "affected": [],
        "notify": [],
        "steps": [],
        "assign_to": "",
        "escalate_to": "",
        "likely_cause": "",
        "circuit": (
            {"cid": cid, "provider": "?", "type": "", "commit_mbps": 0, "lands_on": "", "noc": "", "portal": "", "account": "", "sla": ""}
            if cid
            else None
        ),
    }
    if fix:
        inc["resolution"] = {"fix": fix, "by": "Sam", "at": "2026-10-01 10:00 UTC"}
    return inc


# ------------------------------------------------------------- the data file
def test_every_circuit_lands_on_a_real_firewall_port(plan: dict):
    providers = {p["slug"] for p in plan["providers"]}
    types = {t["slug"] for t in plan["circuit_types"]}
    for item in plan["circuits"]:
        site = _site(plan, item["site"])
        fw = next(d for d in expand_site(plan, site) if d.name == f"{item['site']}-fw01")
        ports = interface_groups(plan["device_types"][fw.type_key])["wan"]
        assert item["provider"] in providers and item["type"] in types, item["cid"]
        assert item["port"] in ports, item["cid"]
        assert item["role"] in ("primary", "backup"), item["cid"]


def test_every_site_has_one_primary_circuit(plan: dict):
    primaries = [c["site"] for c in plan["circuits"] if c["role"] == "primary"]
    assert sorted(primaries) == sorted(s["code"] for s in plan["sites"])


def test_every_provider_has_the_numbers_a_ticket_quotes(plan: dict):
    for item in plan["providers"]:
        assert item["noc"] and item["account"] and item["sla"], item["slug"]


def test_history_dependencies_and_monitoring_name_real_devices(plan: dict):
    names = {d.name for s in plan["sites"] for d in expand_site(plan, s)}
    cids = {c["cid"] for c in plan["circuits"]}
    for item in plan["incident_history"]:
        assert item["device"] in names and item["fix"], item["id"]
        assert not item.get("circuit") or item["circuit"] in cids, item["id"]
    for site in plan["sites"]:
        for end in site.get("endpoints", []):
            assert set(end.get("depends_on", [])) <= names, end["name"]
    assert set(plan["monitor_mock"]["unreachable"]) <= names
    scan.check_config(plan)


def test_an_endpoint_can_sit_on_another_vlan_and_name_its_dependencies(plan: dict):
    devices = {d.name: d for d in expand_site(plan, _site(plan, "us-plt2"))}
    assert devices["us-plt2-hmi01"].vid == 10
    assert devices["us-plt2-hmi01"].custom_fields["_depends_on"] == ["us-plt2-mes01"]
    hq = {d.name: d for d in expand_site(plan, _site(plan, "us-hq"))}
    assert hq["us-hq-hmi01"].vid == 100


def test_an_object_custom_field_compares_by_id():
    assert _same([{"id": 7, "name": "x"}, {"id": 3}], [3, 7])
    assert not _same([{"id": 7}], [8])


def test_a_vendor_runbook_wins_over_the_generic_one(plan: dict):
    assert cards.runbook_for(plan, "switch", "juniper") == plan["showcase"]["runbooks"]["switch-juniper"]
    assert cards.runbook_for(plan, "switch", "cisco-meraki") == plan["showcase"]["runbooks"]["switch"]
    assert cards.runbook_for(plan, "nonsense", "") == plan["showcase"]["runbooks"]["generic"]
    assert "Meraki dashboard cannot see" in plan["showcase"]["runbooks"]["switch-juniper"][0]


# ----------------------------------------------------- mock: uplink usage
def test_the_mock_knows_each_circuits_usage(fresh: dict, plan: dict):
    gvl = fresh["uplinks"][meraki_mock.network_id("us-plt2")]
    commit = next(c["commit_mbps"] for c in plan["circuits"] if c["site"] == "us-plt2")
    assert gvl["wan1"] == pytest.approx(commit * plan["meraki_mock"]["uplinks"]["us-plt2"] / 100)
    tul = fresh["uplinks"][meraki_mock.network_id("us-plt3")]
    assert tul["wan2"] < tul["wan1"]  # the backup idles


def test_a_typo_in_the_uplink_scenarios_is_refused(plan: dict):
    broken = {**plan, "meraki_mock": {**plan["meraki_mock"], "uplinks": {"no-such-site": 50}}}
    with pytest.raises(ValueError, match="no-such-site"):
        meraki_mock.build_world(broken)


def test_uplink_usage_history_over_http(mock):
    world, client = mock
    history = client.uplink_usage(meraki_mock.network_id("us-plt2"))
    assert len(history) == 2
    latest = history[-1]
    assert {"startTime", "endTime", "byInterface"} <= set(latest)
    wan1 = next(i for i in latest["byInterface"] if i["interface"] == "wan1")
    assert wan1["received"] > wan1["sent"] > 0
    with pytest.raises(MerakiError, match="404"):
        client.uplink_usage("L_does_not_exist")
    with pytest.raises(MerakiError, match="400"):
        client.uplink_usage(meraki_mock.network_id("us-plt2"), resolution=7)


def test_the_dashboard_turns_bytes_back_into_mbps(plan: dict, tmp_path: Path):
    with dashboard.Dashboard(plan, tmp_path / "state.json") as dash:
        usage = dash.uplink_mbps("us-plt2")
    assert usage["wan1"] == pytest.approx(500 * 0.86, rel=0.01)


# ------------------------------------------------------------- the poller
def test_marking_a_device_down_and_up_again(plan: dict, tmp_path: Path):
    state = tmp_path / "probe.json"
    monitor.mark("mx-plt1-sw01", True, state)
    assert "mx-plt1-sw01" in monitor.load_down(plan, state)
    monitor.mark("mx-plt1-sw01", False, state)
    assert not state.exists()
    assert monitor.load_down(plan, state) == set(plan["monitor_mock"]["unreachable"])


def test_a_probe_reports_up_down_or_cannot_poll():
    switch = SimpleNamespace(name="mx-plt1-sw01", primary_ip4=SimpleNamespace(address="10.7.99.11/24"))
    assert monitor.probe(switch, set())["status"] == "up"
    down = monitor.probe(switch, {"mx-plt1-sw01"})
    assert down["status"] == "down" and "0 received" in down["detail"]
    assert monitor.probe(SimpleNamespace(name="x", primary_ip4=None), set())["status"] == "no-ip"


def test_two_alerts_are_the_same_problem_only_on_the_same_vlan_or_uplink():
    a = {"deviceName": "fw", "alertType": alerts.DHCP_FULL, "alertData": {"vlan": 100}}
    b = {"deviceName": "fw", "alertType": alerts.DHCP_FULL, "alertData": {"vlan": 30}}
    inc = {"device": {"name": "fw"}, "alert": {"type": alerts.DHCP_FULL, "data": {"vlan": 100}}}
    assert alerts._key(a) == alerts._key(inc) != alerts._key(b)


@pytest.mark.parametrize(
    ("text", "kind"),
    [("DHCP scope nearly exhausted", "dhcp"), ("Uplink status changed", "uplink-down"), ("Uplink near capacity", "uplink-full"), ("APs went down", "device")],
)
def test_alert_kinds(text: str, kind: str):
    assert alerts.alert_kind(text) == kind


# ---------------------------------------------------------- known fixes
def test_the_ticket_history_is_dated_relative_to_now(plan: dict):
    seeded = alerts.seeded_history(plan, NOW)
    first = plan["incident_history"][0]
    entry = next(h for h in seeded if h["id"] == first["id"])
    assert entry["opened"].startswith((NOW - timedelta(days=first["days_ago"])).date().isoformat())
    assert entry["state"] == "resolved" and entry["fix"] == first["fix"]


def test_a_fix_is_quoted_only_for_the_same_problem_on_the_same_device(tmp_path: Path):
    alerts.save_incident(_incident("INC-1", "ap03", "APs went down", 40, fix="Re-enabled PoE"), tmp_path)
    alerts.save_incident(_incident("INC-2", "ap03", "APs went down", 2), tmp_path)
    alerts.save_incident(_incident("INC-3", "ap03", "Something else", 1, fix="Unrelated"), tmp_path)
    alerts.save_incident(_incident("INC-4", "ap01", "APs went down", 1, fix="Other AP"), tmp_path)
    fixes = alerts.known_fixes("ap03", "APs went down", {}, tmp_path)
    assert [f["id"] for f in fixes] == ["INC-1"]  # older than 30 days still counts as knowledge
    recent = alerts.earlier_incidents("ap03", datetime.now(timezone.utc) - timedelta(days=30), tmp_path, alert_type="APs went down")
    assert [e["id"] for e in recent] == ["INC-2"]


def test_the_sample_history_counts_as_earlier_incidents(plan: dict, tmp_path: Path):
    found = alerts.earlier_incidents("us-plt3-fw01", datetime.now(timezone.utc) - timedelta(days=30), tmp_path, plan, "Uplink status changed")
    assert len(found) == 3 and all(f["source"].startswith("ticket history") for f in found)
    assert found == sorted(found, key=lambda f: f["opened"], reverse=True)


def test_incidents_on_a_circuit_are_counted(plan: dict, tmp_path: Path):
    alerts.save_incident(_incident("INC-9", "us-plt3-fw01", "Uplink status changed", 1, cid="WIN-TUL-88213"), tmp_path)
    since = datetime.now(timezone.utc) - timedelta(days=30)
    assert len(alerts.circuit_incidents("WIN-TUL-88213", since, plan, tmp_path)) == 4
    assert alerts.circuit_incidents("NOPE", since, plan, tmp_path) == []


# ------------------------------------------------------------- circuits
def test_provider_details_come_from_the_comments():
    text = "NOC: +1-555-0182\n\nPortal: https://x.example\n\nAccount: WS-1\n\nSLA: 8 hour restore"
    assert circuits.provider_details(text) == {"noc": "+1-555-0182", "portal": "https://x.example", "account": "WS-1", "sla": "8 hour restore"}
    assert circuits.provider_details("") == {}


def test_finding_the_circuit_on_a_port():
    found = [{"port": "WAN1", "cid": "A"}, {"port": "WAN2", "cid": "B"}]
    assert circuits.on_port(found, "wan2")["cid"] == "B"
    assert circuits.on_port(found, "wan3") is None


CIRCUIT = {"cid": "WIN-1", "provider": "Windstream", "noc": "+1-555", "account": "WS-1", "sla": "8h", "commit_mbps": 500, "port": "WAN1"}
BACKUP = {"cid": "LTE-1", "provider": "AT&T", "commit_mbps": 50, "port": "WAN2"}


def test_a_circuit_outage_says_who_to_call_and_whether_the_backup_carries_the_site():
    cause, steps = alerts._circuit_story("uplink-down", {"uplink": "wan1"}, CIRCUIT, BACKUP, 3, "Joanne", "Tulsa")
    assert "WIN-1 from Windstream" in cause and "already gone down 3 times" in cause and "LTE-1" in cause
    assert steps[0].startswith("Call Windstream on +1-555") and "WS-1" in steps[0]
    assert any("WAN2" in s for s in steps)
    assert "4 outages" in steps[-1]


def test_without_a_backup_the_whole_site_is_down():
    cause, steps = alerts._circuit_story("uplink-down", {}, CIRCUIT, None, 0, "Joanne", "Tulsa")
    assert "whole site is offline" in cause and "already" not in cause
    assert not any("outages" in s for s in steps)


def test_a_full_circuit_suggests_an_upgrade_and_an_unknown_one_asks_who_provides_it():
    cause, steps = alerts._circuit_story("uplink-full", {"inUse": "86%"}, CIRCUIT, None, 0, "Tom", "Greenville")
    assert "86% of its 500 Mbps" in cause and any("quote for more than 500" in s for s in steps)
    cause, steps = alerts._circuit_story("uplink-down", {"uplink": "wan2"}, None, None, 0, "Tom", "Greenville")
    assert "No circuit is recorded on WAN2" in cause


def test_the_ticket_carries_the_circuit_the_fix_and_the_resolution():
    inc = {
        **_incident("INC-7", "us-plt3-fw01", "Uplink status changed", 0, fix="Provider fixed the fibre"),
        "circuit": {**CIRCUIT, "type": "Dedicated Internet", "lands_on": "WAN1 on us-plt3-fw01", "portal": "p", "incidents_30d": 3},
        "backup_circuit": {**BACKUP, "lands_on": "WAN2 on us-plt3-fw01"},
        "fixes": [{"id": "ZD-1", "opened": "2026-10-16T00:00:00", "by": "Sam", "fix": "Fibre cut"}],
    }
    text = alerts.render_ticket(inc)
    assert "## Circuit" in text and "call +1-555" in text and "3 earlier incident(s)" in text
    assert "Backup: LTE-1" in text
    assert "## What fixed it before" in text and "ZD-1 (2026-10-16, Sam): Fibre cut" in text
    assert "## Resolution" in text and "(RESOLVED)" in text
    assert "last fix: Fibre cut" in alerts.brief(inc)


# ----------------------------------------------------------------- the scan
KNOWN = {
    "10.2.99.2": {"id": 1, "ip": "10.2.99.2", "device": "us-plt2-fw01", "status": "active", "tags": [], "description": ""},
    "10.2.10.11": {"id": 2, "ip": "10.2.10.11", "device": "us-plt2-mes01", "status": "active", "tags": [], "description": ""},
    "10.2.10.99": {"id": 3, "ip": "10.2.10.99", "device": "", "status": "active", "tags": ["scan-found"], "description": "found"},
}


def _nets() -> list[dict]:
    import ipaddress

    return [
        {"network": ipaddress.ip_network("10.2.100.0/24"), "vlan": 100, "vlan_name": "OT", "role": "ot"},
        {"network": ipaddress.ip_network("10.2.10.0/24"), "vlan": 10, "vlan_name": "CORP", "role": "corporate"},
        {"network": ipaddress.ip_network("10.2.99.0/24"), "vlan": 99, "vlan_name": "MGMT", "role": "management"},
    ]


def test_the_mock_scan_answers_for_netbox_minus_silent_plus_extras(plan: dict):
    cfg = {"scan_mock": {"silent": ["us-plt2-mes01"], "extra": {"us-plt2": [{"ip": "10.2.100.57", "vendor": "Rockwell Automation", "ports": [44818]}]}}}
    answers = {a["ip"]: a for a in scan.mock_scan({**plan, **cfg}, "us-plt2", KNOWN)}
    assert set(answers) == {"10.2.99.2", "10.2.10.99", "10.2.100.57"}
    assert answers["10.2.100.57"]["vendor"] == "Rockwell Automation"


def test_the_comparison_sorts_unknown_silent_and_unidentified():
    answers = [
        {"ip": "10.2.99.2", "mac": "m", "vendor": "", "hostname": "us-plt2-fw01", "ports": []},
        {"ip": "10.2.10.99", "mac": "m", "vendor": "", "hostname": "", "ports": []},
        {"ip": "10.2.100.57", "mac": "m", "vendor": "Rockwell", "hostname": "", "ports": [44818]},
        {"ip": "192.168.1.1", "mac": "m", "vendor": "TP-Link", "hostname": "", "ports": []},
    ]
    result = scan.compare(answers, KNOWN, _nets())
    unknown = {u["ip"]: u for u in result["unknown"]}
    assert unknown["10.2.100.57"]["role"] == "ot" and unknown["10.2.100.57"]["vlan"] == 100
    assert unknown["192.168.1.1"]["vlan"] is None
    assert [s["device"] for s in result["silent"]] == ["us-plt2-mes01"]
    assert [u["ip"] for u in result["unidentified"]] == ["10.2.10.99"]
    assert result["matched"] == 1


def test_a_typo_in_the_scan_settings_is_refused(plan: dict):
    with pytest.raises(ValueError, match="no-such-device"):
        scan.check_config({**plan, "scan_mock": {"silent": ["no-such-device"]}})
    with pytest.raises(ValueError, match="no-such-site"):
        scan.check_config({**plan, "scan_mock": {"extra": {"no-such-site": []}}})


# ------------------------------------------------------------ the reports
def test_an_os_past_its_end_of_life():
    today = date(2026, 10, 20)
    assert ot._eol_passed("2025-10-14", today)
    assert not ot._eol_passed("2029-01-09", today)
    assert not ot._eol_passed(None, today)


def test_the_ot_report_lists_the_rules_to_write():
    result = {
        "site": "us-plt2", "name": "Greenville", "ot_networks": ["10.2.100.0/24"], "machines": [{}],
        "unknown": [{"ip": "10.2.100.57", "vendor": "Rockwell"}], "wrong_vlan": [{"device": "hmi01", "vlan": "10 CORP"}],
        "no_ip": [], "no_owner": [{"device": "hmi01"}], "unsupported_os": [], "retire_conflicts": [],
        "flows": [{"from": "hmi01", "from_ip": "10.2.10.12", "to": "mes01", "to_ip": "10.2.10.11", "to_where": "VLAN 10 CORP", "to_application": "MES", "ask": "Raj Patel"}],
        "blockers": 3, "ready": False, "talk_to": ["Raj Patel"],
    }
    text = ot.to_markdown([result], date(2026, 10, 20))
    assert "| hmi01 10.2.10.12 | mes01 10.2.10.11 | VLAN 10 CORP | MES | Raj Patel |" in text
    assert "**Unknown on OT:** 10.2.100.57" in text and "**Wrong VLAN:** hmi01" in text
    assert "3 to fix" in text


def test_deltas_against_last_week():
    assert report.delta(5, 3) == " (+2)"
    assert report.delta(3, 5) == " (-2)"
    assert report.delta(3, 3) == "" and report.delta(3, None) == ""


def test_the_last_snapshot_is_the_comparison(tmp_path: Path):
    assert report.previous_snapshot(tmp_path) is None
    (tmp_path / "estate-20261013-090000.json").write_text(json.dumps({"generated": "old"}), encoding="utf-8")
    (tmp_path / "estate-20261020-090000.json").write_text(json.dumps({"generated": "new"}), encoding="utf-8")
    assert report.previous_snapshot(tmp_path)["generated"] == "new"


def test_attention_puts_open_incidents_first():
    found = {
        "open": [{"id": "INC-1", "priority": "P1", "alert": "APs went down", "device": "ap03", "owner": "Carla"}],
        "repeats": [{"device": "fw01", "site": "us-plt3", "alert": "Uplink status changed", "count": 4}],
        "hot": ["DHCP: us-plt2 VLAN 100 is 98% used (3 left)"],
        "down": [{"device": "sw01", "site": "mx-plt1", "why": "poller: does not answer"}],
        "unowned_important": ["hmi01"],
        "eol_platforms": {"Windows XP": 4},
    }
    items = report.attention(found)
    assert items[0].startswith("Open P1: INC-1") and "4 times in 30 days" in items[1]
    assert items[-1] == "Old OS still running: Windows XP x4"
