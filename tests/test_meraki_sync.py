"""The sync's decisions: what it writes, what it only reports, and what it must never touch."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
from plants_demo.meraki_sync import STATUS_MAP, Outcome, compute
from plants_demo.populate import IP_BASE, expand_site, meraki_serial

SERIAL = "Q2AA-AAAA-AAAA"


# ---------------------------------------------------------- tiny snapshots
def netbox_device(**overrides: Any) -> dict[str, Any]:
    device = {
        "id": 1,
        "name": "one-sw01",
        "serial": SERIAL,
        "meraki_serial": SERIAL,
        "site": "one",
        "model": "MS120-24P",
        "status": "active",
        "firmware": "switch-16-8",
        "source_system": "meraki-api",
        "ip": "10.1.99.11",
    }
    return {**device, **overrides}


def netbox(devices: list[dict[str, Any]], network_id: str = "N1") -> dict[str, Any]:
    return {
        "sites": [{"slug": "one", "name": "Site One", "network_id": network_id}],
        "devices": devices,
        "names": {d["name"].lower() for d in devices},
        "ips": {d["ip"] for d in devices if d["ip"]},
        "models": {"ms120-24p", "mr46", "mx68"},
    }


def dash_device(**overrides: Any) -> dict[str, Any]:
    device = {
        "name": "one-sw01",
        "serial": SERIAL,
        "mac": "aa:bb:cc:00:00:01",
        "model": "MS120-24P",
        "networkId": "N1",
        "productType": "switch",
        "lanIp": "10.1.99.11",
        "firmware": "switch-16-8",
    }
    return {**device, **overrides}


def dashboard(devices: list[dict[str, Any]], status: str = "online", networks=None):
    return {
        "networks": networks or [{"id": "N1", "name": "Site One"}],
        "devices": devices,
        "availabilities": [{"serial": d["serial"], "status": status} for d in devices],
    }


def kinds(out: Outcome) -> Counter:
    return Counter(f.kind for f in out.findings)


def actions(out: Outcome, kind: str) -> list:
    return [a for a in out.actions if a.kind == kind]


# ----------------------------------------------------------------- matching
def test_a_device_that_already_agrees_needs_nothing():
    out = compute(dashboard([dash_device()]), netbox([netbox_device()]))
    assert out.actions == [] and out.findings == [] and out.matched == 1


def test_devices_are_matched_by_serial_not_by_name():
    out = compute(
        dashboard([dash_device(name="renamed-in-dashboard")]),
        netbox([netbox_device()]),
    )
    assert out.matched == 1 and not actions(out, "create-device")


def test_matching_ignores_case_and_can_use_the_meraki_serial_field():
    device = netbox_device(serial="", meraki_serial=SERIAL.lower())
    out = compute(dashboard([dash_device()]), netbox([device]))
    assert out.matched == 1
    (update,) = actions(out, "update-device")
    assert update.payload["fields"] == {"serial": SERIAL}  # the standard field gets filled in


# ------------------------------------------------------------------ writes
def test_it_writes_the_observed_facts_to_a_device_it_has_not_synced_before():
    device = netbox_device(meraki_serial="", firmware="", source_system="spreadsheet")
    out = compute(dashboard([dash_device()]), netbox([device]))
    (update,) = actions(out, "update-device")
    assert update.payload["fields"] == {}
    assert update.payload["custom_fields"] == {
        "meraki_serial": SERIAL,
        "firmware_version": "switch-16-8",
        "source_system": "meraki-api",
    }


def test_a_device_with_no_serial_is_recognised_by_name_and_given_one():
    device = netbox_device(serial="", meraki_serial="")
    out = compute(dashboard([dash_device(name="ONE-SW01")]), netbox([device]))
    assert out.matched == 1 and not actions(out, "create-device")
    (update,) = actions(out, "update-device")
    assert update.payload["fields"] == {"serial": SERIAL}
    assert update.payload["custom_fields"]["meraki_serial"] == SERIAL


def test_a_device_that_has_a_different_serial_is_not_claimed_by_name():
    device = netbox_device(serial="Q2ZZ-ZZZZ-ZZZZ", meraki_serial="")
    out = compute(dashboard([dash_device()]), netbox([device]))
    assert out.matched == 0, "a name match must not override a different serial"
    assert kinds(out)["name-conflict"] == 1 and kinds(out)["missing-from-meraki"] == 1


def test_one_serial_less_device_is_only_claimed_once():
    device = netbox_device(serial="", meraki_serial="")
    twin = dash_device(serial="Q2CC-CCCC-CCCC", lanIp="10.1.99.77")  # same name, other serial
    out = compute(dashboard([dash_device(), twin]), netbox([device]))
    assert out.matched == 1
    assert kinds(out)["name-conflict"] == 1


@pytest.mark.parametrize("seen", ["online", "alerting"])
def test_a_device_reported_up_is_made_active_again(seen):
    out = compute(dashboard([dash_device()], seen), netbox([netbox_device(status="offline")]))
    (update,) = actions(out, "update-device")
    assert update.payload["fields"] == {"status": "active"}


@pytest.mark.parametrize("seen", ["offline", "dormant"])
def test_a_device_reported_down_is_marked_offline_and_flagged(seen):
    out = compute(dashboard([dash_device()], seen), netbox([netbox_device()]))
    (update,) = actions(out, "update-device")
    assert update.payload["fields"] == {"status": "offline"}
    assert kinds(out) == {"device-offline": 1}


@pytest.mark.parametrize(
    "lifecycle", ["planned", "staged", "decommissioning", "failed", "inventory"]
)
def test_a_lifecycle_status_is_never_overwritten(lifecycle):
    out = compute(dashboard([dash_device()], "offline"), netbox([netbox_device(status=lifecycle)]))
    assert out.actions == []  # nothing to write...
    assert kinds(out) == {"device-offline": 1}  # ...but still reported


def test_alerting_is_reported_without_changing_status():
    out = compute(dashboard([dash_device()], "alerting"), netbox([netbox_device()]))
    assert out.actions == []
    assert kinds(out) == {"device-alerting": 1}


def test_a_blank_dashboard_firmware_never_erases_what_netbox_has():
    out = compute(dashboard([dash_device(firmware="")]), netbox([netbox_device()]))
    assert out.actions == []


# -------------------------------------------------------- reported, not fixed
def test_differences_in_model_ip_and_site_are_only_reported():
    nb = netbox([netbox_device(model="MR46", ip="10.1.99.99", site="elsewhere")])
    out = compute(dashboard([dash_device()]), nb)
    assert kinds(out) == {"model-drift": 1, "ip-drift": 1, "site-drift": 1}
    assert out.actions == []


def test_a_device_with_no_ip_in_netbox_is_an_ip_difference():
    out = compute(dashboard([dash_device()]), netbox([netbox_device(ip=None)]))
    assert kinds(out) == {"ip-drift": 1}


def test_a_device_missing_from_the_dashboard_is_reported_and_left_alone():
    other = netbox_device(id=2, name="one-sw02", serial="Q2BB-BBBB-BBBB", meraki_serial="")
    out = compute(dashboard([dash_device()]), netbox([netbox_device(), other]))
    assert kinds(out) == {"missing-from-meraki": 1}
    assert out.actions == []


# ----------------------------------------------------------------- firmware
def test_firmware_that_most_devices_do_not_run_is_flagged():
    devices = [
        dash_device(serial=f"Q2AA-AAAA-000{i}", name=f"d{i}", firmware="v2" if i else "v1")
        for i in range(4)
    ]
    out = compute(dashboard(devices), netbox([]))
    drift = [f for f in out.findings if f.kind == "firmware-drift"]
    assert [f.subject for f in drift] == ["d0"]
    assert out.firmware == {"MS120-24P": {"v2": 3, "v1": 1}}


def test_firmware_is_compared_within_a_model_not_across_models():
    devices = [
        dash_device(serial="Q2AA-AAAA-0001", name="sw", firmware="switch-1"),
        dash_device(
            serial="Q2AA-AAAA-0002", name="ap", model="MR46", productType="wireless", firmware="w-1"
        ),
    ]
    out = compute(dashboard(devices), netbox([]))
    assert "firmware-drift" not in kinds(out)


# ----------------------------------------------------------- site / network
def test_a_network_is_linked_to_the_site_with_the_same_name():
    out = compute(dashboard([dash_device()]), netbox([netbox_device()], network_id=""))
    (link,) = actions(out, "link-site")
    assert link.payload == {"slug": "one", "network_id": "N1"}


def test_matching_the_name_ignores_case_and_punctuation():
    out = compute(
        dashboard([], networks=[{"id": "N1", "name": "site-one"}]),
        netbox([], network_id=""),
    )
    assert len(actions(out, "link-site")) == 1


def test_a_site_already_linked_to_another_network_is_left_alone():
    out = compute(dashboard([dash_device()]), netbox([netbox_device()], network_id="OTHER"))
    assert actions(out, "link-site") == []
    assert kinds(out)["network-id-mismatch"] == 1


def test_a_network_with_no_matching_site_is_reported():
    out = compute(dashboard([], networks=[{"id": "N9", "name": "Somewhere Else"}]), netbox([]))
    assert kinds(out) == {"unknown-network": 1}


# ----------------------------------------------------------------- creating
def new_device(**overrides: Any) -> dict[str, Any]:
    return dash_device(name="NEW-SW09", serial="Q2CC-CCCC-CCCC", lanIp="10.1.99.60", **overrides)


def test_a_new_supported_device_in_a_known_site_is_created():
    out = compute(dashboard([new_device()]), netbox([]))
    (create,) = actions(out, "create-device")
    assert create.payload["name"] == "new-sw09"  # tidied to the lab's lower-case convention
    assert create.payload["original_name"] == "NEW-SW09"
    assert create.payload["site"] == "one"
    assert create.payload["lan_ip"] == "10.1.99.60"
    assert create.payload["status"] == "active"


def test_a_new_device_reported_offline_is_created_offline():
    out = compute(dashboard([new_device()], "offline"), netbox([]))
    assert actions(out, "create-device")[0].payload["status"] == STATUS_MAP["offline"]


def test_nothing_is_created_for_a_network_netbox_does_not_know():
    device = new_device(networkId="N9")
    out = compute(dashboard([device]), netbox([]))
    assert actions(out, "create-device") == []
    assert kinds(out) == {"unknown-network": 1}


def test_nothing_is_created_when_the_site_is_linked_to_a_different_network():
    out = compute(dashboard([new_device()]), netbox([], network_id="OTHER"))
    assert actions(out, "create-device") == []


def test_a_model_without_a_device_type_is_reported_not_created():
    camera = new_device(model="MV12W", productType="camera")
    out = compute(dashboard([camera]), netbox([]))
    assert actions(out, "create-device") == []
    assert kinds(out) == {"unsupported-model": 1}


def test_a_name_already_used_by_another_device_is_not_overwritten():
    clash = netbox_device(id=7, name="new-sw09", serial="Q2ZZ-ZZZZ-ZZZZ", meraki_serial="")
    out = compute(dashboard([dash_device(), new_device()]), netbox([netbox_device(), clash]))
    assert actions(out, "create-device") == []
    assert kinds(out)["name-conflict"] == 1


def test_an_address_already_in_use_is_not_duplicated():
    reuses_address = dash_device(name="NEW-SW09", serial="Q2CC-CCCC-CCCC", lanIp="10.1.99.11")
    out = compute(dashboard([dash_device(), reuses_address]), netbox([netbox_device()]))
    (create,) = actions(out, "create-device")
    assert create.payload["lan_ip"] is None
    assert kinds(out) == {"ip-conflict": 1}


def test_two_new_devices_cannot_claim_the_same_address():
    first = new_device()
    second = dash_device(name="NEW-SW10", serial="Q2DD-DDDD-DDDD", lanIp="10.1.99.60")
    out = compute(dashboard([first, second]), netbox([]))
    addresses = sorted(str(a.payload["lan_ip"]) for a in actions(out, "create-device"))
    assert addresses == ["10.1.99.60", "None"]


def test_two_new_devices_cannot_claim_the_same_name():
    second = dash_device(name="new-sw09", serial="Q2DD-DDDD-DDDD", lanIp="10.1.99.61")
    out = compute(dashboard([new_device(), second]), netbox([]))
    assert len(actions(out, "create-device")) == 1
    assert kinds(out) == {"name-conflict": 1}


# --------------------------------------------- the whole sample, end to end
def sample_netbox(plan: dict) -> dict[str, Any]:
    """NetBox as the 'plants' stage leaves it: sample Meraki devices only, nothing synced yet."""
    sites, devices = [], []
    for site in plan["sites"]:
        sites.append({"slug": site["code"], "name": site["name"], "network_id": ""})
        counters: dict[tuple[int, str], int] = {}
        for dev in expand_site(plan, site):
            slot = counters.get((dev.vid, dev.kind), 0)
            counters[(dev.vid, dev.kind)] = slot + 1
            dtype = plan["device_types"][dev.type_key]
            if dtype["manufacturer"] != "cisco-meraki":
                continue
            devices.append(
                {
                    "id": len(devices) + 1,
                    "name": dev.name,
                    "serial": meraki_serial(dev.name),
                    "meraki_serial": meraki_serial(dev.name),
                    "site": site["code"],
                    "model": dtype["model"],
                    "status": "active",
                    "firmware": "",
                    "source_system": "spreadsheet",
                    "ip": f"10.{site['index']}.{dev.vid}.{IP_BASE[dev.kind] + slot}",
                }
            )
    return {
        "sites": sites,
        "devices": devices,
        "names": {d["name"].lower() for d in devices},
        "ips": {d["ip"] for d in devices},
        "models": {
            t["model"].lower()
            for t in plan["device_types"].values()
            if t["manufacturer"] == "cisco-meraki"
        },
    }


def test_the_sample_dashboard_tells_the_story_the_demo_relies_on(plan, world):
    out = compute(
        {
            "networks": world["networks"],
            "devices": world["devices"],
            "availabilities": world["availabilities"],
        },
        sample_netbox(plan),
    )
    found = kinds(out)
    assert found["device-offline"] == 3  # two offline, one dormant
    assert found["device-alerting"] == 1
    assert found["firmware-drift"] == 3
    assert found["ip-drift"] == 1
    assert found["model-drift"] == 1
    assert found["site-drift"] == 1
    assert found["missing-from-meraki"] == 1
    assert found["unsupported-model"] == 1  # the camera
    assert len(actions(out, "link-site")) == 11
    assert len(actions(out, "create-device")) == 4
    # every device NetBox has gets its firmware and Meraki source stamped on it
    assert out.matched == 58
    assert len(actions(out, "update-device")) == out.matched


def apply_to(snapshot: dict[str, Any], out: Outcome) -> None:
    """Pretend the actions were written, to prove a second pass finds nothing left to do."""
    by_id = {d["id"]: d for d in snapshot["devices"]}
    by_slug = {s["slug"]: s for s in snapshot["sites"]}
    for action in out.actions:
        if action.kind == "link-site":
            by_slug[action.payload["slug"]]["network_id"] = action.payload["network_id"]
        elif action.kind == "update-device":
            device = by_id[action.payload["id"]]
            for key, value in action.payload["fields"].items():
                device[key] = value
            custom = action.payload["custom_fields"]
            for key in ("meraki_serial", "source_system"):
                if key in custom:
                    device[key] = custom[key]
            if "firmware_version" in custom:
                device["firmware"] = custom["firmware_version"]
        elif action.kind == "create-device":
            p = action.payload
            snapshot["devices"].append(
                {
                    "id": 1000 + len(snapshot["devices"]),
                    "name": p["name"],
                    "serial": p["serial"],
                    "meraki_serial": p["serial"],
                    "site": p["site"],
                    "model": p["model"],
                    "status": p["status"],
                    "firmware": p["firmware"],
                    "source_system": "meraki-api",
                    "ip": p["lan_ip"],
                }
            )


def test_running_the_sync_twice_changes_nothing_the_second_time(plan, world):
    dashboard_now = {
        "networks": world["networks"],
        "devices": world["devices"],
        "availabilities": world["availabilities"],
    }
    snapshot = sample_netbox(plan)
    first = compute(dashboard_now, snapshot)
    assert first.actions
    apply_to(snapshot, first)
    snapshot["names"] = {d["name"].lower() for d in snapshot["devices"]}
    snapshot["ips"] = {d["ip"] for d in snapshot["devices"] if d["ip"]}
    second = compute(dashboard_now, snapshot)
    assert second.actions == []
    # the report is still there: the dashboard still says these things are wrong
    assert kinds(second)["device-offline"] == 3
