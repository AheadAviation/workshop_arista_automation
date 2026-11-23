"""Contract tests for INIT-001/SPEC-001.

One test per acceptance criterion at minimum, named so a reader can match the
criterion to the test without reading the implementation.

No lab, no NetBox, no network: this is the piece of the demo that proves itself
anywhere.
"""

from __future__ import annotations

import json

import pytest

from workbench.contracts import (
    AUTONOMY_LEVELS,
    EVENT_KINDS,
    PLATFORMS,
    Actual,
    AuditEntry,
    ContractError,
    Drift,
    Intent,
    Plan,
    Preview,
    Skip,
    may_act_unattended,
    new_id,
    utc_now,
)


# --------------------------------------------------------------------------- #
# ac-1 — construct from valid input, reject documented invalid cases by name
# --------------------------------------------------------------------------- #


def test_intent_constructs_from_valid_input():
    intent = Intent(
        device="leaf-01",
        platform="eos",
        source="netbox:dc1-fabric",
        hostname="leaf-01",
        role="leaf",
        asn=65001,
        router_id="10.255.0.11",
        vlans=(100, 110, 120),
        trunks=("Ethernet5", "Ethernet6"),
        trunk_allowed={"Ethernet5": (100, 110)},
    )
    assert intent.asn == 65001
    assert 120 in intent.vlans


def test_intent_rejects_unknown_platform():
    with pytest.raises(ContractError, match="platform must be one of"):
        Intent(device="x", platform="iosxe", source="s")


def test_intent_rejects_empty_device():
    with pytest.raises(ContractError, match="device is required"):
        Intent(device="", platform="eos", source="s")


def test_intent_rejects_empty_source():
    with pytest.raises(ContractError, match="source is required"):
        Intent(device="d", platform="eos", source="   ")


def test_intent_rejects_out_of_range_vlan():
    with pytest.raises(ContractError, match="outside 1-4094"):
        Intent(device="d", platform="eos", source="s", vlans=(5000,))


def test_intent_rejects_non_positive_asn():
    with pytest.raises(ContractError, match="positive 32-bit"):
        Intent(device="d", platform="eos", source="s", asn=-1)


def test_intent_rejects_trunk_allowed_for_a_port_not_in_trunks():
    """The page cannot render an allow-list for a port it does not know is a trunk."""
    with pytest.raises(ContractError, match="not in trunks"):
        Intent(
            device="d",
            platform="eos",
            source="s",
            trunks=("Ethernet5",),
            trunk_allowed={"Ethernet9": (100,)},
        )


def test_actual_constructs_and_records_unreachable():
    actual = Actual(device="leaf-01", platform="eos", reachable=False, error="timed out")
    assert actual.reachable is False
    assert actual.error == "timed out"


def test_drift_rejects_unknown_severity():
    with pytest.raises(ContractError, match="severity must be one of"):
        Drift(device="d", kind="vlans", detail="x", severity="critical")


def test_drift_rejects_empty_detail():
    with pytest.raises(ContractError, match="detail is required"):
        Drift(device="d", kind="vlans", detail="")


def test_skip_rejects_empty_reason():
    with pytest.raises(ContractError, match="reason is required"):
        Skip(reason="")


# --------------------------------------------------------------------------- #
# ac-2 — JSON-safe round trip
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "obj",
    [
        Intent(device="leaf-01", platform="eos", source="netbox:dc1",
               vlans=(100, 110), trunks=("Ethernet5",), trunk_allowed={"Ethernet5": (100,)}),
        Actual(device="leaf-01", platform="eos", reachable=True, vlans=(100,),
               trunk_allowed={"Ethernet5": (100,)}),
        Drift(device="leaf-01", kind="vlans", detail="missing 150", severity="error"),
        Plan(device="leaf-01", platform="eos", usecase="vlan-across-trunks",
             fmt="set", config="vlan 150"),
        Preview(device="leaf-01", diff="+vlan 150", changed_lines=1),
        AuditEntry(kind="detected", device="leaf-01", summary="drift",
                   autonomy="assist"),
    ],
)
def test_shape_serialises_to_json_and_round_trips(obj):
    payload = obj.to_dict()
    text = json.dumps(payload)          # must not need a custom encoder
    restored = json.loads(text)
    assert restored == payload


def test_tuples_become_lists_in_dict_form():
    """The page receives exactly `to_dict`, so no tuple may leak through."""
    d = Intent(device="d", platform="eos", source="s", vlans=(1, 2)).to_dict()
    assert d["vlans"] == [1, 2]
    assert isinstance(d["vlans"], list)


def test_nested_trunk_allowed_is_json_safe():
    d = Intent(device="d", platform="eos", source="s", trunks=("Ethernet5",),
               trunk_allowed={"Ethernet5": (1, 2)}).to_dict()
    assert d["trunk_allowed"] == {"Ethernet5": [1, 2]}


# --------------------------------------------------------------------------- #
# ac-3 — Plan carries mode/fmt/compensating, and an empty plan is refused
# --------------------------------------------------------------------------- #


def test_plan_carries_mode_fmt_and_compensating():
    plan = Plan(device="d", platform="eos", usecase="u", fmt="text",
                config="hostname x", mode="replace", compensating=True)
    assert plan.mode == "replace"
    assert plan.fmt == "text"
    assert plan.compensating is True


def test_plan_refuses_empty_config():
    """An empty plan is not a plan; 'applying' nothing must not look like success."""
    with pytest.raises(ContractError, match="config is required"):
        Plan(device="d", platform="eos", usecase="u", fmt="set", config="  \n ")


def test_plan_refuses_unknown_mode():
    with pytest.raises(ContractError, match="mode must be one of"):
        Plan(device="d", platform="eos", usecase="u", fmt="set", config="x", mode="overwrite")


def test_plan_refuses_unknown_fmt():
    with pytest.raises(ContractError, match="fmt must be one of"):
        Plan(device="d", platform="eos", usecase="u", fmt="yaml", config="x")


def test_plan_summary_counts_non_blank_lines():
    plan = Plan(device="d", platform="eos", usecase="u", fmt="set",
                config="vlan 150\n\n   name NEW\n", reason="add vlan")
    assert "2 lines" in plan.summary
    assert "add vlan" in plan.summary


# --------------------------------------------------------------------------- #
# ac-4 — autonomy levels are exactly observe/assist/autonomous
# --------------------------------------------------------------------------- #


def test_autonomy_levels_are_exactly_three():
    assert AUTONOMY_LEVELS == ("observe", "assist", "autonomous")


def test_observe_may_watch_but_not_preview_or_apply():
    assert may_act_unattended("observe", "observe") is True
    assert may_act_unattended("observe", "preview") is False
    assert may_act_unattended("observe", "apply") is False


def test_assist_may_preview_but_not_apply():
    """The demo default: you can always see what would happen, never quietly do it."""
    assert may_act_unattended("assist", "preview") is True
    assert may_act_unattended("assist", "apply") is False


def test_autonomous_may_apply():
    assert may_act_unattended("autonomous", "apply") is True


def test_unknown_autonomy_level_raises():
    with pytest.raises(ContractError, match="autonomy level must be one of"):
        may_act_unattended("full-auto", "apply")


def test_unknown_action_raises():
    with pytest.raises(ContractError, match="unknown action"):
        may_act_unattended("assist", "deploy")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# ac-5 — event kinds are a closed set
# --------------------------------------------------------------------------- #


def test_event_kinds_is_a_closed_set():
    assert "applied" in EVENT_KINDS
    assert "failed" in EVENT_KINDS


def test_audit_entry_rejects_unknown_kind():
    with pytest.raises(ContractError, match="kind must be one of"):
        AuditEntry(kind="shenanigans", device="d", summary="s", autonomy="assist")


def test_audit_entry_rejects_unknown_autonomy():
    with pytest.raises(ContractError, match="autonomy must be one of"):
        AuditEntry(kind="detected", device="d", summary="s", autonomy="yolo")


# --------------------------------------------------------------------------- #
# ac-6 — an applied change always has a named human behind it
# --------------------------------------------------------------------------- #


def test_applied_entry_requires_an_approver():
    with pytest.raises(ContractError, match="requires a named approver"):
        AuditEntry(kind="applied", device="d", summary="pushed", autonomy="assist")


def test_approved_entry_requires_an_approver():
    with pytest.raises(ContractError, match="requires a named approver"):
        AuditEntry(kind="approved", device="d", summary="ok", autonomy="assist")


def test_applied_entry_with_whitespace_approver_is_refused():
    with pytest.raises(ContractError, match="requires a named approver"):
        AuditEntry(kind="applied", device="d", summary="pushed",
                   autonomy="assist", approver="   ")


def test_applied_entry_with_approver_is_accepted():
    entry = AuditEntry(
        kind="applied",
        device="leaf-01",
        summary="added vlan 150 to 4 trunks",
        autonomy="assist",
        approver="Brian Nelson",
        diff_before="vlan 100,110",
        diff_after="vlan 100,110,150",
    )
    assert entry.approver == "Brian Nelson"
    assert entry.diff_before != entry.diff_after


def test_detected_entry_needs_no_approver():
    """Only changes that went through a human need one; detection is free."""
    entry = AuditEntry(kind="detected", device="d", summary="drift", autonomy="observe")
    assert entry.approver == ""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def test_new_id_is_readable_and_stable():
    a = new_id("vlan-across-trunks", "leaf-01")
    b = new_id("vlan-across-trunks", "leaf-01")
    assert a == b, "the same parts must produce the same id"
    assert a.startswith("vlan-across-trunks:leaf-01:")


def test_new_id_differs_for_different_parts():
    assert new_id("u", "leaf-01") != new_id("u", "leaf-02")


def test_utc_now_is_iso8601_utc():
    stamp = utc_now()
    assert stamp.endswith("+00:00")
    assert "T" in stamp


def test_platforms_closed_set():
    assert PLATFORMS == ("eos", "frr")


def test_preview_is_empty_semantics():
    assert Preview(device="d", diff="   \n").is_empty is True
    assert Preview(device="d", diff="+vlan 150").is_empty is False
