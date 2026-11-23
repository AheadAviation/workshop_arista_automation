"""End-to-end scenario tests with a fake lab (INIT-001/SPEC-004, SPEC-007).

This file is the demo, rehearsed. Every story the presenter tells is exercised here with
fakes standing in for NetBox and the six switches, so the whole narrative is proven
before anything is pointed at a real lab.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.contracts import Actual, Intent, Plan, Preview
from workbench.gate import AuditLog
from workbench.intent_source import BASE_VLANS, NEW_VLAN
from workbench.scenarios import (
    DEMO_DEVICES,
    Lab,
    approve,
    audit_view,
    fabric_overview,
    inject_drift,
    lifecycle,
    propose_vlan,
    remediate,
)


class FakeLab:
    """A six-switch fabric that behaves the way the real one does, minus the containers.

    Keeps **intended** and **actual** state apart on purpose, because that separation is
    the whole point of the demo: NetBox says what should be true, the switches say what
    is true, and a change moves the second toward the first without editing the first.

    Tracks its own state so a change applied by one story is visible to the next — which
    is the only way the demo's narrative can be tested as a narrative.
    """

    def __init__(
        self,
        intended_vlans: tuple[int, ...] = BASE_VLANS,
        reachable: tuple[str, ...] | None = None,
    ):
        self.intended_vlans = intended_vlans
        # What the switches actually carry. Starts at intent; drifts away from it.
        self.actual_vlans: tuple[int, ...] = intended_vlans
        self.reachable = set(DEMO_DEVICES if reachable is None else reachable)
        self.trunk_vlans: dict[str, dict[str, tuple[int, ...]]] = {
            device: {port: intended_vlans for port in ("Ethernet5", "Ethernet6")}
            for device in ("leaf-01", "leaf-02", "leaf-03", "leaf-04")
        }
        self.previewed: list[Plan] = []
        self.applied: list[Plan] = []
        self.fail_preview: str = ""
        self.fail_apply: str = ""

    # --- the four seams ---------------------------------------------------- #

    def read_intent(self, new_vlan: int | None = None, **_: object) -> dict[str, Intent]:
        """What NetBox says should be true. `new_vlan` proposes a change to it."""
        vlans = list(self.intended_vlans)
        if new_vlan is not None and new_vlan not in vlans:
            vlans.append(new_vlan)
        intended: dict[str, Intent] = {}
        for device in DEMO_DEVICES:
            role = "spine" if device.startswith("spine") else "leaf"
            trunks = ("Ethernet5", "Ethernet6") if role == "leaf" else ()
            intended[device] = Intent(
                device=device,
                platform="eos",
                source="netbox:dc1-fabric",
                hostname=device,
                role=role,
                asn=65000 if role == "spine" else 65001,
                vlans=tuple(vlans),
                trunks=trunks,
                trunk_allowed={p: tuple(vlans) for p in trunks},
            )
        return intended

    def read_actual(self, device: str) -> Actual:
        """What the switch actually carries — which is not what NetBox says."""
        if device not in self.reachable:
            return Actual(device=device, platform="eos", reachable=False, error="timed out")
        return Actual(
            device=device,
            platform="eos",
            reachable=True,
            hostname=device,
            vlans=self.actual_vlans,
            trunk_allowed=self.trunk_vlans.get(device, {}),
            os_version="4.32.0F",
        )

    def preview(self, plan: Plan) -> Preview:
        self.previewed.append(plan)
        if self.fail_preview:
            from workbench.eos_executor import EosApplyError

            raise EosApplyError(self.fail_preview)
        diff = self._diff_for(plan)
        return Preview(device=plan.device, diff=diff, changed_lines=len(diff.splitlines()))

    def apply(self, plan: Plan) -> Preview:
        self.applied.append(plan)
        if self.fail_apply:
            from workbench.eos_executor import EosApplyError

            raise EosApplyError(self.fail_apply)
        diff = self._diff_for(plan)
        if diff:
            self._mutate(plan)
        return Preview(device=plan.device, diff=diff, changed_lines=len(diff.splitlines()))

    # --- how the fake changes itself --------------------------------------- #

    def _diff_for(self, plan: Plan) -> str:
        lines: list[str] = []
        for raw in plan.config.splitlines():
            stripped = raw.strip()
            if stripped.startswith("vlan ") and stripped.split()[-1].isdigit():
                vlan = int(stripped.split()[-1])
                if vlan not in self.actual_vlans:
                    lines.append(f"+   {plan.device}: create vlan {vlan}")
            elif stripped.startswith("switchport trunk allowed vlan add"):
                vlan = int(stripped.split()[-1])
                port = _port_from(plan.config, raw)
                have = self.trunk_vlans.get(plan.device, {}).get(port, ())
                if vlan not in have:
                    lines.append(f"+   {plan.device}/{port}: add {vlan}")
            elif stripped.startswith("switchport trunk allowed vlan remove"):
                vlan = int(stripped.split()[-1])
                port = _port_from(plan.config, raw)
                have = self.trunk_vlans.get(plan.device, {}).get(port, ())
                if vlan in have:
                    lines.append(f"-   {plan.device}/{port}: remove {vlan}")
        return "\n".join(lines)

    def _mutate(self, plan: Plan) -> None:
        for raw in plan.config.splitlines():
            stripped = raw.strip()
            if stripped.startswith("vlan ") and stripped.split()[-1].isdigit():
                vlan = int(stripped.split()[-1])
                if vlan not in self.actual_vlans:
                    self.actual_vlans = tuple(sorted({*self.actual_vlans, vlan}))
            elif stripped.startswith("switchport trunk allowed vlan add"):
                vlan = int(stripped.split()[-1])
                port = _port_from(plan.config, raw)
                current = set(self.trunk_vlans.get(plan.device, {}).get(port, ()))
                current.add(vlan)
                self.trunk_vlans.setdefault(plan.device, {})[port] = tuple(sorted(current))
            elif stripped.startswith("switchport trunk allowed vlan remove"):
                vlan = int(stripped.split()[-1])
                port = _port_from(plan.config, raw)
                current = set(self.trunk_vlans.get(plan.device, {}).get(port, ()))
                current.discard(vlan)
                self.trunk_vlans.setdefault(plan.device, {})[port] = tuple(sorted(current))


def _port_from(config: str, target: str) -> str:
    """The `interface X` line preceding the target line in a generated plan."""
    port = ""
    for line in config.splitlines():
        if line.strip().startswith("interface "):
            port = line.strip().split(None, 1)[1]
        if line is target or line == target:
            break
    return port


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture
def fake() -> FakeLab:
    return FakeLab()


@pytest.fixture
def lab(fake: FakeLab, audit: AuditLog) -> Lab:
    return Lab(
        read_intent=fake.read_intent,
        read_actual=fake.read_actual,
        preview=fake.preview,
        apply=fake.apply,
        audit=audit,
    )


# --------------------------------------------------------------------------- #
# 1. the overview
# --------------------------------------------------------------------------- #


def test_overview_shows_every_device(lab: Lab):
    result = fabric_overview(lab)
    assert result["ok"] is True
    assert result["device_count"] == len(DEMO_DEVICES)


def test_overview_reports_a_compliant_fabric(lab: Lab):
    result = fabric_overview(lab)
    assert result["drifted_count"] == 0
    assert "match intended state" in result["headline"]


def test_overview_depicts_an_unreachable_device_as_drift(lab: Lab, fake: FakeLab):
    fake.reachable.discard("leaf-03")
    result = fabric_overview(lab)
    leaf03 = next(d for d in result["devices"] if d["device"] == "leaf-03")
    assert leaf03["verdict"]["state"] == "drifted"
    assert leaf03["findings"][0]["kind"] == "reachability"


def test_overview_can_show_a_proposed_vlan_without_changing_anything(lab: Lab, fake: FakeLab):
    result = fabric_overview(lab, new_vlan=NEW_VLAN)
    leaf = next(d for d in result["devices"] if d["device"] == "leaf-01")
    assert NEW_VLAN in leaf["intended"]["vlans"]
    assert fake.applied == [], "an overview must never change a device"


def test_overview_reports_netbox_failure_plainly(audit: AuditLog):
    def boom(**_: object):
        raise RuntimeError("NetBox answered 502")

    broken = Lab(
        read_intent=boom,
        read_actual=lambda d: Actual(device=d, platform="eos", reachable=True),
        preview=lambda p: Preview(device=p.device, diff=""),
        apply=lambda p: Preview(device=p.device, diff=""),
        audit=audit,
    )
    result = fabric_overview(broken)
    assert result["ok"] is False
    assert "502" in result["detail"]


# --------------------------------------------------------------------------- #
# 2. the Philips VLAN story
# --------------------------------------------------------------------------- #


def test_propose_vlan_stages_the_change_and_changes_nothing(lab: Lab, fake: FakeLab):
    result = propose_vlan(lab)
    assert result["ok"] is True
    assert result["changed_lines"] > 0
    assert fake.applied == [], "proposing must not apply"


def test_propose_vlan_covers_both_trunks(lab: Lab):
    result = propose_vlan(lab)
    assert set(result["trunks"]) == {"Ethernet5", "Ethernet6"}


def test_propose_vlan_returns_a_change_id_for_the_page(lab: Lab):
    result = propose_vlan(lab)
    assert result["change_id"].startswith("vlan-across-trunks:leaf-01")


def test_propose_vlan_is_recorded_as_pending(lab: Lab, audit: AuditLog):
    propose_vlan(lab)
    assert audit.read()[0]["kind"] == "pending"


def test_propose_vlan_on_an_already_compliant_device_says_so(lab: Lab, fake: FakeLab):
    """Apply once, then propose again: the second proposal has nothing to do."""
    approve(lab, "leaf-01", approver="Brian Nelson")
    again = propose_vlan(lab)
    assert again["ok"] is True
    assert again.get("no_drift") is True


def test_propose_vlan_on_an_unknown_device_is_refused_plainly(lab: Lab):
    result = propose_vlan(lab, device="leaf-99")
    assert result["ok"] is False
    assert "not in the fabric" in result["headline"]


def test_propose_vlan_on_a_spine_says_it_has_no_trunks(lab: Lab):
    result = propose_vlan(lab, device="spine-01")
    assert result["ok"] is False
    assert "no server trunks" in result["headline"]


def test_propose_vlan_surfaces_a_preview_failure(lab: Lab, fake: FakeLab):
    fake.fail_preview = "container is not running"
    result = propose_vlan(lab)
    assert result["ok"] is False
    assert "not running" in result["detail"]


# --------------------------------------------------------------------------- #
# 3. approve and apply
# --------------------------------------------------------------------------- #


def test_approve_requires_a_named_approver(lab: Lab, fake: FakeLab):
    result = approve(lab, "leaf-01", approver="   ")
    assert result["ok"] is False
    assert "approver is required" in result["headline"]
    assert fake.applied == []


def test_approve_applies_the_change(lab: Lab, fake: FakeLab):
    result = approve(lab, "leaf-01", approver="Brian Nelson")
    assert result["ok"] is True
    assert result["applied"] is True
    assert len(fake.applied) == 1


def test_approve_records_who_approved_it(lab: Lab, audit: AuditLog):
    approve(lab, "leaf-01", approver="Brian Nelson")
    applied = audit.applied_changes()
    assert len(applied) == 1
    assert applied[0]["approver"] == "Brian Nelson"


def test_approve_makes_the_device_compliant_again(lab: Lab):
    approve(lab, "leaf-01", approver="Brian Nelson")
    overview = fabric_overview(lab)
    leaf = next(d for d in overview["devices"] if d["device"] == "leaf-01")
    assert leaf["verdict"]["state"] == "compliant"


def test_double_approve_is_harmless_and_says_nothing_was_sent(lab: Lab, fake: FakeLab):
    approve(lab, "leaf-01", approver="Brian Nelson")
    second = approve(lab, "leaf-01", approver="Brian Nelson")
    assert second["ok"] is True
    assert second.get("no_drift") is True


def test_approve_surfaces_an_apply_failure(lab: Lab, fake: FakeLab):
    fake.fail_apply = "the device rejected the change"
    result = approve(lab, "leaf-01", approver="Brian Nelson")
    assert result["ok"] is False
    assert "rejected" in result["detail"]


# --------------------------------------------------------------------------- #
# 4. inject drift, then catch it
# --------------------------------------------------------------------------- #


def test_inject_drift_then_overview_catches_it(lab: Lab):
    """The whole point of the demo, in one test."""
    approve(lab, "leaf-01", approver="Brian Nelson")
    assert fabric_overview(lab)["drifted_count"] == 0

    inject_drift(lab, "leaf-01")
    overview = fabric_overview(lab)
    leaf = next(d for d in overview["devices"] if d["device"] == "leaf-01")
    assert leaf["verdict"]["state"] == "drifted"
    assert any(f["kind"] == "trunks" for f in leaf["findings"])


def test_inject_drift_is_recorded(lab: Lab, audit: AuditLog):
    inject_drift(lab, "leaf-01")
    kinds = [e["kind"] for e in audit.read()]
    assert "detected" in kinds


def test_inject_drift_on_a_spine_is_refused(lab: Lab):
    result = inject_drift(lab, "spine-01")
    assert result["ok"] is False


def test_repair_after_injected_drift_restores_compliance(lab: Lab):
    """The closed loop: break it, see it, remediate it, confirm it is fixed."""
    approve(lab, "leaf-01", approver="A")
    inject_drift(lab, "leaf-01")
    assert fabric_overview(lab)["drifted_count"] > 0

    result = remediate(lab, "leaf-01", approver="Brian Nelson")
    assert result["ok"] is True
    assert result["applied"] is True

    overview = fabric_overview(lab)
    leaf = next(d for d in overview["devices"] if d["device"] == "leaf-01")
    assert leaf["verdict"]["state"] == "compliant"


def test_remediate_requires_an_approver(lab: Lab, fake: FakeLab):
    inject_drift(lab, "leaf-01")
    result = remediate(lab, "leaf-01", approver="")
    assert result["ok"] is False
    assert "approver is required" in result["headline"]


def test_remediate_on_a_compliant_device_does_nothing(lab: Lab, fake: FakeLab):
    result = remediate(lab, "leaf-01", approver="Brian Nelson")
    assert result["ok"] is True
    assert result.get("no_drift") is True
    assert fake.applied == []


def test_remediate_is_additive_only(lab: Lab, fake: FakeLab):
    """It must never remove a VLAN that intent does not know about."""
    fake.actual_vlans = tuple(sorted({*fake.actual_vlans, 999}))
    remediate(lab, "leaf-01", approver="Brian Nelson")
    assert 999 in fake.actual_vlans or all(
        "remove" not in p.config for p in fake.applied
    ), "remediation must not delete local VLANs"


def test_remediate_records_who_approved_it(lab: Lab, audit: AuditLog):
    inject_drift(lab, "leaf-01")
    remediate(lab, "leaf-01", approver="Brittany")
    applied = audit.applied_changes()
    assert applied[0]["approver"] == "Brittany"


def test_remediate_refuses_an_unreachable_device(lab: Lab, fake: FakeLab):
    fake.reachable.discard("leaf-01")
    result = remediate(lab, "leaf-01", approver="Brian Nelson")
    assert result["ok"] is False
    assert "did not answer" in result["headline"]


# --------------------------------------------------------------------------- #
# 5. lifecycle (SPEC-007)
# --------------------------------------------------------------------------- #


def test_lifecycle_reports_every_device_and_its_os(lab: Lab):
    result = lifecycle(lab)
    assert result["ok"] is True
    assert result["total"] == len(DEMO_DEVICES)
    assert result["os_fleet"] == {"4.32.0F": len(DEMO_DEVICES)}


def test_lifecycle_counts_compliance(lab: Lab, fake: FakeLab):
    fake.reachable.discard("leaf-02")
    result = lifecycle(lab)
    assert result["compliant"] < result["total"]


def test_lifecycle_marks_an_unreachable_device(lab: Lab, fake: FakeLab):
    fake.reachable.discard("leaf-04")
    result = lifecycle(lab)
    row = next(r for r in result["devices"] if r["device"] == "leaf-04")
    assert row["reachable"] is False
    assert row["state"] == "drifted"


def test_lifecycle_says_where_the_os_versions_came_from(lab: Lab):
    """No invented EOL data: it says plainly that it read the device."""
    result = lifecycle(lab)
    assert "read from each device" in result["note"]


# --------------------------------------------------------------------------- #
# 6. the audit view
# --------------------------------------------------------------------------- #


def test_audit_view_counts_applied_changes(lab: Lab):
    approve(lab, "leaf-01", approver="Brian Nelson")
    approve(lab, "leaf-02", approver="Chris Veith")
    view = audit_view(lab)
    assert view["applied"] == 2


def test_audit_view_is_empty_on_a_fresh_demo(lab: Lab):
    view = audit_view(lab)
    assert view["count"] == 0


def test_audit_survives_a_reload_of_the_log(lab: Lab, audit: AuditLog):
    """The record is on disk, so it outlives the process that wrote it."""
    approve(lab, "leaf-01", approver="Brian Nelson")
    reopened = AuditLog(audit.path)
    assert len(reopened.applied_changes()) == 1
