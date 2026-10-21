"""The mock Meraki API and the client that reads it."""

from __future__ import annotations

import copy

import pytest
import requests
from plants_demo import meraki_mock
from plants_demo.meraki_client import MerakiClient, MerakiError
from plants_demo.populate import expand_site


def no_wait(_seconds: float) -> None:
    """Stand in for time.sleep so rate-limit tests are instant."""


def client_for(server: meraki_mock.MockMeraki, **kwargs) -> MerakiClient:
    kwargs.setdefault("sleep", no_wait)
    return MerakiClient(server.base_url, meraki_mock.DEFAULT_KEY, **kwargs)


# ---------------------------------------------------------------- the world
def test_world_has_the_expected_shape(plan, world):
    assert len(world["networks"]) == len(plan["sites"]) == 11
    assert len(world["devices"]) == len(world["availabilities"])
    serials = [d["serial"] for d in world["devices"]]
    assert len(serials) == len(set(serials)), "serials must be unique"
    addresses = [d["lanIp"] for d in world["devices"] if d["lanIp"]]
    assert len(addresses) == len(set(addresses)), "LAN IPs must be unique"
    assert {d["productType"] for d in world["devices"]} == {
        "appliance",
        "switch",
        "wireless",
        "camera",
    }


def test_world_covers_every_meraki_device_except_the_removed_one(plan, world):
    names = {d["name"] for d in world["devices"]}
    expected = {
        dev.name
        for site in plan["sites"]
        for dev in expand_site(plan, site)
        if plan["device_types"][dev.type_key]["manufacturer"] == "cisco-meraki"
    }
    removed = set(plan["meraki_mock"]["removed"])
    assert expected - removed <= names
    assert not removed & names
    assert {item["name"] for item in plan["meraki_mock"]["extra"]} <= names


def test_world_is_deterministic(plan, world):
    again = meraki_mock.build_world(plan)
    assert again == world


@pytest.mark.parametrize(
    "section, value",
    [
        ("status", {"no-such-device": "offline"}),
        ("lan_ip", {"no-such-device": "10.0.0.1"}),
        ("network", {"no-such-device": "us-hq"}),
        ("model", {"no-such-device": "MS120-24P"}),
        ("removed", ["no-such-device"]),
    ],
)
def test_a_typo_in_a_scenario_is_an_error_not_a_silent_no_op(plan, section, value):
    broken = copy.deepcopy(plan)
    broken["meraki_mock"][section] = value
    with pytest.raises(ValueError, match="no-such-device"):
        meraki_mock.build_world(broken)


def test_a_typo_in_the_firmware_scenario_is_an_error(plan):
    broken = copy.deepcopy(plan)
    broken["meraki_mock"]["firmware"]["behind"]["no-such-device"] = "switch-1"
    with pytest.raises(ValueError, match="no-such-device"):
        meraki_mock.build_world(broken)


# ------------------------------------------------------------------- server
def test_requests_without_the_key_are_rejected(server):
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"X-Cisco-Meraki-API-Key": "wrong"}):
        response = requests.get(f"{server.base_url}/organizations", headers=headers, timeout=5)
        assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": f"Bearer {meraki_mock.DEFAULT_KEY}"},
        {"X-Cisco-Meraki-API-Key": meraki_mock.DEFAULT_KEY},  # the older header style
    ],
)
def test_both_credential_styles_work(server, headers):
    response = requests.get(f"{server.base_url}/organizations", headers=headers, timeout=5)
    assert response.status_code == 200
    assert response.json()[0]["id"] == "999001"


def test_unknown_paths_and_orgs_are_404(server):
    headers = {"Authorization": f"Bearer {meraki_mock.DEFAULT_KEY}"}
    for path in ("/nothing", "/organizations/1/devices", "/organizations/999001/nothing"):
        assert requests.get(server.base_url + path, headers=headers, timeout=5).status_code == 404


@pytest.mark.parametrize("per_page", [0, 1, 2, "abc", 100000])
def test_per_page_outside_the_allowed_range_is_400(server, per_page):
    response = requests.get(
        f"{server.base_url}/organizations/999001/devices",
        params={"perPage": per_page},
        headers={"Authorization": f"Bearer {meraki_mock.DEFAULT_KEY}"},
        timeout=5,
    )
    assert response.status_code == 400


# ------------------------------------------------------------------- client
def test_client_follows_pagination_to_the_end(server, world):
    client = client_for(server)
    devices = client.devices("999001")
    assert [d["serial"] for d in devices] == [d["serial"] for d in world["devices"]]
    assert client.calls == -(-len(world["devices"]) // 10)  # one call per page of 10
    assert client.calls > 1


def test_client_reads_every_collection(server, world):
    client = client_for(server)
    assert len(client.organizations()) == 1
    assert len(client.networks("999001")) == 11
    assert {a["status"] for a in client.availabilities("999001")} == {
        "online",
        "offline",
        "dormant",
        "alerting",
    }


def test_the_client_gets_a_next_link_only_while_there_is_more(server):
    headers = {"Authorization": f"Bearer {meraki_mock.DEFAULT_KEY}"}
    first = requests.get(
        f"{server.base_url}/organizations/999001/devices", headers=headers, timeout=5
    )
    assert "next" in first.links and "first" in first.links
    last = requests.get(first.links["next"]["url"], headers=headers, timeout=5)
    while "next" in last.links:
        last = requests.get(last.links["next"]["url"], headers=headers, timeout=5)
    assert "next" not in last.links


def test_a_wrong_key_gives_a_plain_explanation(server):
    client = MerakiClient(server.base_url, "wrong", sleep=no_wait)
    with pytest.raises(MerakiError, match="rejected the API key"):
        client.organizations()


def test_rate_limiting_is_retried_and_the_result_is_still_complete(world):
    server = meraki_mock.start(world, page_cap=10, rate_limit_every=3)
    waits: list[float] = []
    try:
        client = MerakiClient(
            server.base_url, meraki_mock.DEFAULT_KEY, sleep=lambda s: waits.append(s)
        )
        devices = client.devices("999001")
    finally:
        meraki_mock.stop(server)
    assert len(devices) == len(world["devices"])
    assert client.retries > 0 and server.rate_limited == client.retries
    assert waits and all(wait == 1.0 for wait in waits), "honours Retry-After"


def test_endless_rate_limiting_gives_up_with_a_clear_error(world):
    server = meraki_mock.start(world, rate_limit_every=1)
    try:
        client = MerakiClient(
            server.base_url, meraki_mock.DEFAULT_KEY, max_retries=2, sleep=no_wait
        )
        with pytest.raises(MerakiError, match="rate limited"):
            client.organizations()
        assert client.calls == 3  # the first try and two retries
    finally:
        meraki_mock.stop(server)


def test_an_unreachable_server_is_a_clear_error():
    client = MerakiClient("http://127.0.0.1:9", "key", timeout=1, sleep=no_wait)
    with pytest.raises(MerakiError, match="Cannot reach"):
        client.organizations()


def test_a_pagination_loop_is_detected():
    class Page:
        status_code = 200
        headers: dict = {}
        links = {"next": {"url": "http://example.invalid/again"}}

        def json(self):
            return [{"id": 1}]

    client = MerakiClient("http://example.invalid", "key", sleep=no_wait)
    client.session.get = lambda *_args, **_kwargs: Page()
    with pytest.raises(MerakiError, match="Pagination loop"):
        client.get_all("/things")
