"""Approval gate and audit trail tests (INIT-001/SPEC-006).

The tests that matter most here are the refusals: an applied change with no approver, an
approval for a change that does nothing, and an audit log that survives a reset.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workbench.contracts import ContractError, Plan, Preview
from workbench.gate import (
    AuditLog,
    GateRefused,
    PendingChange,
    approve_and_apply,
    reject_change,
    request_change,
)


def plan(device: str = "leaf-01") -> Plan:
    return Plan(
        device=device,
        platform="eos",
        usecase="vlan-across-trunks",
        fmt="text",
        config="vlan 150",
        reason="add VLAN 150",
    )


def preview(device: str = "leaf-01", diff: str = "+   switchport trunk allowed vlan add 150") -> Preview:
    return Preview(device=device, diff=diff, changed_lines=1)


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


# --------------------------------------------------------------------------- #
# staging a change
# --------------------------------------------------------------------------- #


def test_request_change_stages_a_previewed_change(audit: AuditLog):
    """Staging writes nothing to the audit log: only decisions are recorded."""
    pending = request_change("c1", plan(), preview())
    assert pending.change_id == "c1"
    assert pending.is_empty is False
    assert audit.read() == [], "staging a change is not a decision and must not be logged"


def test_request_change_refuses_an_empty_diff():
    """No approval button for a change that does nothing."""
    with pytest.raises(GateRefused, match="nothing to approve"):
        request_change("c1", plan(), preview(diff=""))


def test_request_change_refuses_a_mismatched_device():
    with pytest.raises(GateRefused, match="preview is for"):
        request_change("c1", plan("leaf-01"), preview("leaf-02"))


def test_observe_level_may_not_prepare_a_change():
    """`observe` watches; it does not get as far as a preview."""
    with pytest.raises(GateRefused, match="may watch but not prepare"):
        request_change("c1", plan(), preview(), autonomy="observe")


def test_assist_level_may_prepare():
    pending = request_change("c1", plan(), preview(), autonomy="assist")
    assert pending.change_id == "c1"


def test_unknown_autonomy_level_is_refused():
    with pytest.raises(GateRefused, match="unknown autonomy level"):
        request_change("c1", plan(), preview(), autonomy="full-auto")


def test_pending_change_is_frozen():
    """It cannot be edited after preview; a changed plan needs a new preview."""
    pending = request_change("c1", plan(), preview())
    with pytest.raises(Exception):
        pending.change_id = "c2"  # type: ignore[misc]


def test_pending_change_serialises_for_the_page():
    data = request_change("c1", plan(), preview()).to_dict()
    json.dumps(data)  # must be JSON-safe
    assert data["device"] == "leaf-01"
    assert data["changed_lines"] == 1


# --------------------------------------------------------------------------- #
# ac-6 — no change without a named human
# --------------------------------------------------------------------------- #


def test_apply_without_an_approver_is_refused(audit: AuditLog):
    pending = request_change("c1", plan(), preview())
    with pytest.raises(GateRefused, match="named approver"):
        approve_and_apply(pending, "", audit=audit)


def test_apply_with_a_whitespace_approver_is_refused(audit: AuditLog):
    pending = request_change("c1", plan(), preview())
    with pytest.raises(GateRefused, match="named approver"):
        approve_and_apply(pending, "   ", audit=audit)


def test_rejection_without_an_approver_is_refused(audit: AuditLog):
    pending = request_change("c1", plan(), preview())
    with pytest.raises(GateRefused, match="named person"):
        reject_change(pending, "", audit=audit)


def test_apply_calls_the_executor_and_records_the_change(audit: AuditLog):
    pending = request_change("c1", plan(), preview())
    entry = approve_and_apply(
        pending, "Brian Nelson", audit=audit,
        apply=lambda p: Preview(device=p.device, diff="+   vlan 150", changed_lines=1),
    )
    assert entry.kind == "applied"
    assert entry.approver == "Brian Nelson"
    assert entry.diff_after.startswith("+")
    assert audit.applied_changes()[0]["approver"] == "Brian Nelson"


def test_apply_records_both_the_preview_and_the_result(audit: AuditLog):
    """The before/after pair is what makes the record useful to an auditor."""
    pending = request_change("c1", plan(), preview(diff="+preview line"))
    entry = approve_and_apply(
        pending, "Chris Veith", audit=audit,
        apply=lambda p: Preview(device=p.device, diff="+applied line", changed_lines=1),
    )
    assert entry.diff_before == "+preview line"
    assert entry.diff_after == "+applied line"


def test_re_approving_a_change_that_became_unnecessary_is_recorded_honestly(audit: AuditLog):
    """If the device was fixed in between, do not claim a change that did not happen."""
    pending = request_change("c1", plan(), preview())
    entry = approve_and_apply(
        pending, "Brian Nelson", audit=audit,
        apply=lambda p: Preview(device=p.device, diff="", changed_lines=0),
    )
    assert entry.kind == "no-drift"
    assert "nothing was sent" in entry.summary


def test_rejection_is_recorded_with_its_reason(audit: AuditLog):
    pending = request_change("c1", plan(), preview())
    entry = reject_change(pending, "Brittany", "outside the window", audit=audit)
    assert entry.kind == "rejected"
    assert "outside the window" in entry.summary


# --------------------------------------------------------------------------- #
# the audit log
# --------------------------------------------------------------------------- #


def test_audit_log_is_append_only_jsonl(audit: AuditLog):
    pending = request_change("c1", plan(), preview())
    approve_and_apply(pending, "A", audit=audit,
                      apply=lambda p: Preview(device=p.device, diff="+x", changed_lines=1))
    reject_change(request_change("c2", plan(), preview()), "B", audit=audit)

    lines = audit.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["kind"] for line in lines)


def test_audit_log_reads_newest_first(audit: AuditLog):
    reject_change(request_change("c1", plan(), preview()), "A", audit=audit)
    reject_change(request_change("c2", plan(), preview()), "B", audit=audit)
    entries = audit.read()
    assert entries[0]["event_id"] == "c2"


def test_audit_log_on_a_missing_file_is_empty(audit: AuditLog):
    assert audit.read() == []


def test_a_corrupt_final_line_does_not_break_the_record(audit: AuditLog):
    """A process that died mid-append must not make the history unreadable."""
    reject_change(request_change("c1", plan(), preview()), "A", audit=audit)
    with audit.path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "applied", "trunc')  # half a line

    entries = audit.read()
    assert len(entries) == 1
    assert entries[0]["event_id"] == "c1"


def test_audit_log_filters_by_device(audit: AuditLog):
    reject_change(request_change("c1", plan("leaf-01"), preview("leaf-01")), "A", audit=audit)
    reject_change(request_change("c2", plan("leaf-02"), preview("leaf-02")), "A", audit=audit)
    assert len(audit.for_device("leaf-01")) == 1


def test_applied_changes_excludes_everything_that_did_not_reach_a_device(audit: AuditLog):
    reject_change(request_change("c1", plan(), preview()), "A", audit=audit)
    approve_and_apply(
        request_change("c2", plan(), preview()), "A", audit=audit,
        apply=lambda p: Preview(device=p.device, diff="+x", changed_lines=1),
    )
    assert [e["event_id"] for e in audit.applied_changes()] == ["c2"]


def test_audit_log_path_can_be_overridden_by_environment(monkeypatch, tmp_path: Path):
    target = tmp_path / "custom" / "audit.jsonl"
    monkeypatch.setenv("WORKBENCH_AUDIT_LOG", str(target))
    log = AuditLog()
    reject_change(request_change("c1", plan(), preview()), "A", audit=log)
    assert target.exists()
