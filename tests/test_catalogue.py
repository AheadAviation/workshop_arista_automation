"""The service-catalogue day-two workflow (INIT-003).

The design claim is that a change should be expressible as a sentence a CIO understands —
"deliver Pharmacy to MLAG pair 1" — and that one approval should cover every switch that
sentence implies. These tests pin the sentence, the switch resolution, and the gate.

The gate matters more here than anywhere: a workflow that makes changes easier is also a
workflow that could make *wrong* changes easier, so the refusals are tested first.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from workbench import catalogue  # noqa: E402
from workbench.contracts import Actual, Plan, Preview  # noqa: E402
from workbench.gate import AuditLog  # noqa: E402
from workbench.scenarios import Lab  # noqa: E402


# --------------------------------------------------------------------------- #
# the catalogue
# --------------------------------------------------------------------------- #


def test_every_service_has_a_name_that_is_not_a_number():
    """The whole point: a requester thinks in services, not VLAN ids."""
    for svc in catalogue.CATALOGUE:
        assert svc.name.strip(), f"{svc.key} has no name"
        assert not svc.name.strip().isdigit(), f"{svc.key} is named with a number"
        assert not svc.name.upper().startswith("VLAN"), f"{svc.key} is named after its own VLAN"


def test_service_keys_are_unique():
    keys = [svc.key for svc in catalogue.CATALOGUE]
    assert len(keys) == len(set(keys))


def test_vlan_numbers_are_unique_and_valid():
    vlans = [svc.vlan for svc in catalogue.CATALOGUE]
    assert len(vlans) == len(set(vlans)), "two services share a VLAN"
    assert all(2 <= v <= 4094 for v in vlans)


def test_every_service_says_why_it_exists():
    """The purpose line is read aloud to explain why the service matters."""
    for svc in catalogue.CATALOGUE:
        assert len(svc.purpose) > 20, f"{svc.key} has no real purpose line"


def test_an_unknown_service_names_the_valid_ones():
    with pytest.raises(catalogue.UnknownService) as exc:
        catalogue.service("does-not-exist")
    assert "pharmacy" in str(exc.value).lower()


# --------------------------------------------------------------------------- #
# resolving a sentence to switches
# --------------------------------------------------------------------------- #


def test_a_pair_resolves_to_exactly_the_two_switches_in_it():
    assert catalogue.targets("mlag-pair", pair="pair-1") == ["leaf-01", "leaf-02"]
    assert catalogue.targets("mlag-pair", pair="pair-2") == ["leaf-03", "leaf-04"]


def test_all_leaves_resolves_to_every_leaf_once():
    every = catalogue.targets("all-leaves")
    assert sorted(every) == ["leaf-01", "leaf-02", "leaf-03", "leaf-04"]
    assert len(every) == len(set(every)), "no switch may be listed twice"


def test_one_switch_resolves_to_just_that_switch():
    assert catalogue.targets("one-switch", device="leaf-03") == ["leaf-03"]


def test_a_scope_never_resolves_to_nothing():
    """An empty target list is the worst outcome: a change that silently does nothing."""
    with pytest.raises(Exception):
        catalogue.targets("mlag-pair", pair="pair-99")
    with pytest.raises(Exception):
        catalogue.targets("one-switch", device="spine-01")
    with pytest.raises(Exception):
        catalogue.targets("nonsense")


def test_targets_never_include_a_spine():
    """Spines carry no tenant VLANs; including one would create permanent false drift."""
    for scope in catalogue.SCOPES:
        if scope == "all-leaves":
            assert not any(d.startswith("spine") for d in catalogue.targets(scope))
        elif scope == "mlag-pair":
            for pair in catalogue.MLAG_PAIRS:
                assert not any(d.startswith("spine") for d in catalogue.targets(scope, pair=pair))


# --------------------------------------------------------------------------- #
# the sentence
# --------------------------------------------------------------------------- #


def test_the_sentence_names_the_service_the_place_and_the_scale():
    text = catalogue.describe("pharmacy", "mlag-pair", pair="pair-1")
    assert "Pharmacy" in text
    assert "333" in text
    assert "2 switches" in text


def test_the_sentence_is_grammatical_for_one_and_many():
    """An earlier version produced 'they carries'. It is read aloud, so it has to parse."""
    one = catalogue.describe("pharmacy", "one-switch", device="leaf-03")
    many = catalogue.describe("pharmacy", "all-leaves")

    assert "1 switch will change" in one
    assert "4 switches will change" in many
    for text in (one, many):
        assert "they carries" not in text
        assert "[" not in text and "{" not in text, "no formatting artifacts"
        assert "  " not in text, "no doubled spaces"


def test_the_sentence_does_not_leak_internal_ids():
    """It is shown to executives; no change ids, no dict reprs, no scope keys."""
    for scope in catalogue.SCOPES:
        for pair in ("", *catalogue.MLAG_PAIRS):
            if scope == "mlag-pair" and not pair:
                continue
            text = catalogue.describe("radiology", scope, pair=pair, device="leaf-01")
            for leak in ("change_id", "mlag-pair", "all-leaves", "None", "{"):
                assert leak not in text, f"{leak!r} leaked into the sentence: {text}"


# --------------------------------------------------------------------------- #
# the workflow, against a fake fabric
# --------------------------------------------------------------------------- #


class FakeLab:
    """A six-switch fabric where nothing carries anything yet."""

    def __init__(self):
        self.applied: list[Plan] = []
        self.reachable = {f"leaf-0{n}": True for n in range(1, 5)}
        self.preview_error: dict[str, str] = {}

    def read_intent(self, new_vlan=None):
        from workbench.contracts import Intent

        out = {}
        for name in ("leaf-01", "leaf-02", "leaf-03", "leaf-04"):
            out[name] = Intent(
                device=name,
                platform="eos",
                source="fake",
                hostname=name,
                role="leaf",
                asn=65000,
                router_id="10.0.0.1",
                vlans=(),
                trunks=("Ethernet5",),
                trunk_allowed={"Ethernet5": ()},
            )
        for name in ("spine-01", "spine-02"):
            out[name] = Intent(
                device=name, platform="eos", source="fake", hostname=name, role="spine",
                asn=65000, router_id="10.0.0.2", vlans=(), trunks=(),
            )
        return out

    def read_actual(self, name):
        return Actual(device=name, platform="eos", reachable=True)

    def preview(self, plan):
        if plan.device in self.preview_error:
            raise RuntimeError(self.preview_error[plan.device])
        return Preview(device=plan.device, diff="+ vlan", changed_lines=2)

    def apply(self, plan):
        self.applied.append(plan)
        return Preview(device=plan.device, diff="+ vlan", changed_lines=2)


@pytest.fixture
def lab(tmp_path):
    fake = FakeLab()
    return Lab(
        read_intent=fake.read_intent,
        read_actual=fake.read_actual,
        preview=fake.preview,
        apply=fake.apply,
        audit=AuditLog(tmp_path / "audit.jsonl"),
    ), fake


def test_planning_a_pair_produces_one_change_per_switch(lab):
    from workbench.scenarios import plan_service_change

    lab_obj, fake = lab
    out = plan_service_change(lab_obj, service_key="pharmacy", scope="mlag-pair", pair="pair-1")

    assert out["ok"] is True
    assert sorted(out["to_change"]) == ["leaf-01", "leaf-02"]
    assert out["change_id"]
    assert fake.applied == [], "planning must not touch a switch"


def test_planning_returns_a_sentence_and_a_per_switch_breakdown(lab):
    from workbench.scenarios import plan_service_change

    lab_obj, _ = lab
    out = plan_service_change(lab_obj, service_key="radiology", scope="mlag-pair", pair="pair-2")

    assert "Radiology" in out["headline"]
    assert len(out["devices"]) == 2
    assert all(d["state"] == "needs-change" for d in out["devices"])
    assert all(d["diff"] for d in out["devices"])


def test_an_unknown_service_is_refused_without_touching_anything(lab):
    from workbench.scenarios import plan_service_change

    lab_obj, fake = lab
    out = plan_service_change(lab_obj, service_key="nope", scope="all-leaves")

    assert out["ok"] is False
    assert "not a change this network offers" in out["headline"]
    assert fake.applied == []


def test_a_partially_unreachable_pair_is_reported_not_hidden(lab):
    """"one of two answered" is exactly what a presenter must know before approving."""
    from workbench.scenarios import plan_service_change

    lab_obj, fake = lab
    fake.preview_error["leaf-02"] = "device did not answer"

    out = plan_service_change(lab_obj, service_key="pharmacy", scope="mlag-pair", pair="pair-1")

    assert out["ok"] is True
    assert out["unreachable"] == ["leaf-02"]
    assert out["to_change"] == ["leaf-01"]
    states = {d["device"]: d["state"] for d in out["devices"]}
    assert states["leaf-02"] == "unreachable"


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #


def test_approval_requires_a_named_person(lab):
    from workbench.scenarios import approve_service_change, plan_service_change

    lab_obj, fake = lab
    out = plan_service_change(lab_obj, service_key="pharmacy", scope="mlag-pair", pair="pair-1")
    refused = approve_service_change(lab_obj, change_id=out["change_id"], approver="   ")

    assert refused["ok"] is False
    assert "approver is required" in refused["headline"].lower()
    assert fake.applied == []


def test_approval_applies_to_every_switch_in_one_action(lab):
    from workbench.scenarios import approve_service_change, plan_service_change

    lab_obj, fake = lab
    out = plan_service_change(lab_obj, service_key="pharmacy", scope="mlag-pair", pair="pair-1")
    result = approve_service_change(lab_obj, change_id=out["change_id"], approver="Brian Nelson")

    assert result["ok"] is True
    assert sorted(item["device"] for item in result["applied"]) == ["leaf-01", "leaf-02"]
    assert sorted(plan.device for plan in fake.applied) == ["leaf-01", "leaf-02"]
    assert len(lab_obj.audit.applied_changes()) == 2


def test_a_change_cannot_be_approved_twice(lab):
    """A replay would double-apply. The staged change is consumed by the approval."""
    from workbench.scenarios import approve_service_change, plan_service_change

    lab_obj, fake = lab
    out = plan_service_change(lab_obj, service_key="pharmacy", scope="mlag-pair", pair="pair-1")
    approve_service_change(lab_obj, change_id=out["change_id"], approver="Brian Nelson")
    again = approve_service_change(lab_obj, change_id=out["change_id"], approver="Brian Nelson")

    assert again["ok"] is False
    assert again["refused"] is True
    assert len(fake.applied) == 2, "the replay must not apply a second time"


def test_an_unknown_change_id_is_refused_rather_than_applied(lab):
    from workbench.scenarios import approve_service_change

    lab_obj, fake = lab
    out = approve_service_change(lab_obj, change_id="made-up", approver="Brian Nelson")

    assert out["ok"] is False
    assert fake.applied == []


def test_the_approval_sentence_matches_what_was_previewed(lab):
    """The thing approved is the thing applied: the previews are reused, not re-derived."""
    from workbench.scenarios import approve_service_change, plan_service_change

    lab_obj, fake = lab
    out = plan_service_change(lab_obj, service_key="voip", scope="all-leaves")
    previewed = {d["device"] for d in out["devices"] if d["state"] == "needs-change"}

    approve_service_change(lab_obj, change_id=out["change_id"], approver="Brian Nelson")
    assert {plan.device for plan in fake.applied} == previewed


def test_the_audit_records_the_service_by_name_not_by_vlan(lab):
    """An auditor reading the log should see what was delivered, in the customer's language."""
    from workbench.scenarios import approve_service_change, plan_service_change

    lab_obj, _ = lab
    out = plan_service_change(lab_obj, service_key="pharmacy", scope="mlag-pair", pair="pair-1")
    approve_service_change(lab_obj, change_id=out["change_id"], approver="Brian Nelson")

    entries = lab_obj.audit.applied_changes()
    assert entries
    assert any("pharmacy" in e["summary"].lower() for e in entries), (
        f"the service name should be in the record, got: {[e['summary'] for e in entries]}"
    )
    assert all(e["approver"] == "Brian Nelson" for e in entries)
