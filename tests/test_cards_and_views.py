"""The change-impact / level-1 graph logic, and the data behind the cards and saved filters."""

from __future__ import annotations

import string

import pytest
from plants_demo import cards, views


def graph(*links: tuple[int, int]) -> dict[int, dict[int, tuple[str, str]]]:
    """Build an adjacency map from (a, b) pairs; ports are named after the device ids."""
    adj: dict[int, dict[int, tuple[str, str]]] = {}
    for a, b in links:
        adj.setdefault(a, {})[b] = (f"p{a}-{b}", f"p{b}-{a}")
        adj.setdefault(b, {})[a] = (f"p{b}-{a}", f"p{a}-{b}")
    return adj


# A small plant: firewall 1 - core switch 2 - (access switch 3 - APs 5, 6) (AP 4) (server 7)
PLANT = graph((1, 2), (2, 3), (2, 4), (3, 5), (3, 6), (2, 7))


# ----------------------------------------------------------------- blast radius
def test_losing_the_core_switch_takes_everything_behind_it():
    assert cards.blast_radius(PLANT, {1}, 2) == {3, 4, 5, 6, 7}


def test_losing_an_access_switch_takes_only_its_own_devices():
    assert cards.blast_radius(PLANT, {1}, 3) == {5, 6}


def test_losing_a_leaf_device_takes_nothing_else():
    assert cards.blast_radius(PLANT, {1}, 5) == set()


def test_losing_the_only_firewall_takes_the_whole_site():
    assert cards.blast_radius(PLANT, {1}, 1) == {2, 3, 4, 5, 6, 7}


def test_a_second_firewall_keeps_the_site_up():
    ha = graph((1, 2), (8, 2), (2, 3))  # two firewalls, 1 and 8, on the same core switch
    assert cards.blast_radius(ha, {1, 8}, 1) == set()
    assert cards.blast_radius(ha, {1, 8}, 2) == {3}  # the switch itself is still a single point


def test_a_device_with_a_second_path_is_not_counted_as_lost():
    ring = graph((1, 2), (1, 3), (2, 4), (3, 4))  # 4 can reach the firewall via 2 or 3
    assert cards.blast_radius(ring, {1}, 2) == set()
    assert cards.blast_radius(ring, {1}, 3) == set()


def test_a_device_that_is_not_cabled_has_no_blast_radius():
    assert cards.blast_radius(PLANT, {1}, 99) == set()


def test_devices_that_never_reached_the_firewall_are_not_reported_as_lost():
    split = graph((1, 2), (3, 4))  # 3 and 4 were never connected to a firewall
    assert cards.blast_radius(split, {1}, 2) == set()


def test_with_no_firewall_at_all_nothing_is_computed():
    assert cards.blast_radius(PLANT, set(), 2) == set()


def test_a_cycle_does_not_loop_forever():
    loop = graph((1, 2), (2, 3), (3, 1))
    assert cards.reachable(loop, {1}) == {1, 2, 3}


# ----------------------------------------------------------------- path to root
def test_the_path_runs_from_the_device_up_to_the_firewall():
    assert cards.path_to_root(PLANT, {1}, 5) == [5, 3, 2, 1]


def test_the_firewall_is_its_own_path():
    assert cards.path_to_root(PLANT, {1}, 1) == [1]


def test_an_uncabled_device_has_no_path():
    assert cards.path_to_root(PLANT, {1}, 99) == []
    assert cards.path_to_root(graph((1, 2), (3, 4)), {1}, 4) == []


def test_the_path_takes_the_nearest_firewall():
    ha = graph((1, 2), (8, 2), (2, 3))
    assert cards.path_to_root(ha, {1, 8}, 3)[-1] in {1, 8}
    assert cards.path_to_root(ha, {1, 8}, 3)[:2] == [3, 2]


# ---------------------------------------------------------------------- text
def test_a_missing_placeholder_shows_a_question_mark_instead_of_crashing():
    assert cards._fill("{name} on {nothing}", {"name": "sw1"}) == "sw1 on ?"


def test_people_are_listed_with_their_titles():
    people = [cards.Person(1, "Ada Lovelace", "Engineer", "IT", "", "")]
    assert cards.names(people) == "Ada Lovelace (Engineer)"
    assert cards.names([]) == "nobody recorded"


def test_roles_map_to_a_kind_of_runbook():
    assert cards.ROLE_KIND["wireless-ap"] == "ap"
    assert cards.ROLE_KIND["firewall"] == "firewall"


# --------------------------------------------------------------- saved filters
@pytest.mark.parametrize(
    "matches, total, expected",
    [
        (18, 105, "ok"),
        (0, 105, "empty"),
        (105, 105, "ignored"),  # what NetBox does with a parameter it does not understand
        (1, 1, "ok"),  # a one-record table cannot tell the difference
    ],
)
def test_a_filter_that_matches_everything_is_recognised_as_ignored(matches, total, expected):
    assert views.verdict(matches, total) == expected


def test_every_saved_filter_is_well_formed(plan):
    items = plan["showcase"]["saved_filters"]
    slugs = [item["slug"] for item in items]
    assert len(slugs) == len(set(slugs)), "slugs must be unique"
    for item in items:
        assert item["slug"].startswith("plants-"), "so removal can never touch anyone else's"
        assert item["object_type"] in views.ENDPOINTS
        assert item["parameters"], f"{item['slug']} has no parameters, so it would show everything"
        assert item["name"] and item["description"]


def test_saved_filter_parameters_only_use_tags_and_values_the_data_file_defines(plan):
    tags = {t["slug"] for t in plan["tags"]}
    platforms = {p["slug"] for p in plan["platforms"]}
    manufacturers = {m["slug"] for m in plan["manufacturers"]}
    roles = {r["slug"] for r in plan["prefix_roles"]}
    for item in plan["showcase"]["saved_filters"]:
        params = item["parameters"]
        assert set(params.get("tag", [])) <= tags, item["slug"]
        assert set(params.get("platform", [])) <= platforms, item["slug"]
        assert set(params.get("manufacturer", [])) <= manufacturers, item["slug"]
        if item["object_type"] == "ipam.prefix":
            assert set(params.get("role", [])) <= roles, item["slug"]


# --------------------------------------------------------------------- runbooks
def placeholders(step: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(step) if name}


def test_every_runbook_step_only_uses_placeholders_the_card_provides(plan):
    for kind, steps in plan["showcase"]["runbooks"].items():
        for step in steps:
            unknown = placeholders(step) - cards.PLACEHOLDERS
            assert not unknown, f"runbook '{kind}' uses unknown placeholder(s) {unknown}: {step}"


def test_every_kind_of_device_has_a_runbook_and_checks(plan):
    for kind in set(cards.ROLE_KIND.values()) | {"generic"}:
        assert plan["showcase"]["runbooks"].get(kind), f"no runbook for {kind}"
        assert plan["showcase"]["post_change_checks"].get(kind), f"no checks for {kind}"


def test_every_runbook_renders_with_no_leftover_braces(plan):
    values = dict.fromkeys(cards.PLACEHOLDERS, "x")
    for steps in plan["showcase"]["runbooks"].values():
        for step in steps:
            assert "{" not in cards._fill(step, values)
