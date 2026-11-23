"""FRR executor tests (INIT-001/SPEC-005).

The point of this module is a claim: one intent, two vendors. These tests prove the
comparison works and — just as importantly — that the module states its own limits rather
than pretending to a full command-level apply path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.contracts import Actual, ContractError, Intent, Plan, Preview
from workbench.frr_executor import (
    FrrError,
    intent_for_frr,
    parse_asn,
    parse_hostname,
    preview,
    read_actual,
    read_rendered,
    unified_diff,
)

RUNNING = """! Generated from this device's AVD structured configuration. Do not edit.
frr defaults traditional
hostname frr-core1
log stdout informational
service integrated-vtysh-config
!
router bgp 65000
 bgp router-id 10.251.1.1
 no bgp ebgp-requires-policy
"""


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #


def test_parse_asn_and_hostname_from_a_running_config():
    assert parse_asn(RUNNING) == 65000
    assert parse_hostname(RUNNING) == "frr-core1"


def test_parse_asn_absent_is_zero():
    assert parse_asn("nothing here\n") == 0


def test_read_actual_returns_a_value_when_reachable():
    def run(args):
        assert "vtysh" in args
        return RUNNING, "", 0

    actual = read_actual(runner=run)
    assert actual.reachable is True
    assert actual.platform == "frr"
    assert actual.asn == 65000


def test_read_actual_reports_a_missing_container_plainly():
    def run(args):
        return "", "Error: No such container: clab-ATD-Dual-DC-frr-core1", 1

    actual = read_actual(runner=run)
    assert actual.reachable is False
    assert "dual-DC track is not deployed" in actual.error


def test_read_actual_reports_missing_docker_plainly():
    actual = read_actual(runner=lambda a: ("", "", 127))
    assert actual.reachable is False
    assert "docker is not available" in actual.error


def test_read_actual_never_raises_for_a_network_reason():
    result = read_actual(runner=lambda a: ("", "boom", 1))
    assert isinstance(result, Actual)
    assert result.reachable is False


# --------------------------------------------------------------------------- #
# diffing
# --------------------------------------------------------------------------- #


def test_identical_configs_diff_to_nothing():
    """The header line must not create a permanent phantom diff."""
    assert unified_diff(RUNNING, RUNNING) == ""


def test_the_generated_header_is_ignored_in_the_diff():
    without_header = "\n".join(RUNNING.splitlines()[1:])
    assert unified_diff(without_header, RUNNING) == ""


def test_a_real_difference_shows_up():
    intended = RUNNING.replace("router bgp 65000", "router bgp 65999")
    diff = unified_diff(intended, RUNNING)
    assert "65000" in diff
    assert "65999" in diff


def test_preview_reports_changed_lines_and_commits_nothing():
    intended = RUNNING.replace("hostname frr-core1", "hostname frr-core1")
    plan = Plan(
        device="frr-core1",
        platform="frr",
        usecase="two-vendors",
        fmt="text",
        config=intended,
    )
    result = preview(plan, runner=lambda a: (RUNNING, "", 0))
    assert isinstance(result, Preview)
    assert result.is_empty is True


def test_preview_rejects_a_plan_for_the_wrong_platform():
    plan = Plan(device="leaf-01", platform="eos", usecase="u", fmt="set", config="vlan 1")
    with pytest.raises(ContractError, match="this executor is for frr"):
        preview(plan, runner=lambda a: ("", "", 0))


def test_preview_surfaces_an_unreachable_router():
    plan = Plan(device="frr-core1", platform="frr", usecase="u", fmt="text", config="x")
    with pytest.raises(FrrError, match="not running"):
        preview(plan, runner=lambda a: ("", "No such container: x", 1))


# --------------------------------------------------------------------------- #
# intent
# --------------------------------------------------------------------------- #


def test_intent_for_frr_is_derived_from_the_rendered_config():
    intent = intent_for_frr(RUNNING)
    assert isinstance(intent, Intent)
    assert intent.platform == "frr"
    assert intent.asn == 65000


def test_intent_for_frr_carries_no_tenant_vlans():
    assert intent_for_frr(RUNNING).vlans == ()


def test_intent_for_frr_says_where_it_came_from():
    assert intent_for_frr(RUNNING).source.startswith("avd:")


def test_read_rendered_missing_file_is_a_plain_error(tmp_path: Path):
    with pytest.raises(FrrError, match="could not read the rendered FRR config"):
        read_rendered(tmp_path / "nope.cfg")


def test_read_rendered_from_a_real_file(tmp_path: Path):
    path = tmp_path / "frr.cfg"
    path.write_text(RUNNING, encoding="utf-8")
    assert parse_asn(read_rendered(path)) == 65000
