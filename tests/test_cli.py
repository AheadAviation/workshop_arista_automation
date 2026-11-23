"""CLI tests (INIT-001/SPEC-008).

The behaviour that matters here is the refusal: a rehearsal that says "ready" when the lab
is down is worse than no rehearsal at all, because it is the one thing the presenter trusts
at 8am.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import workbench.cli as cli  # noqa: E402
from workbench.contracts import Actual, Intent, Preview  # noqa: E402
from workbench.gate import AuditLog  # noqa: E402
from workbench.intent_source import BASE_VLANS  # noqa: E402
from workbench.scenarios import DEMO_DEVICES, Lab  # noqa: E402

from test_scenarios import FakeLab  # noqa: E402


def patched_lab(monkeypatch, fake: FakeLab, tmp_path: Path) -> Lab:
    """A Lab backed by the fake, so the CLI runs with no lab."""
    return Lab(
        read_intent=fake.read_intent,
        read_actual=fake.read_actual,
        preview=fake.preview,
        apply=fake.apply,
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #


def test_check_says_ready_when_the_fabric_is_healthy(monkeypatch, tmp_path, capsys):
    code = cli.check(lab=patched_lab(monkeypatch, FakeLab(), tmp_path))
    output = capsys.readouterr().out
    assert code == 0
    assert "READY" in output


def test_check_fails_when_netbox_is_unreachable(monkeypatch, tmp_path, capsys):
    def boom(**_: object):
        raise RuntimeError("could not reach NetBox")

    broken = Lab(
        read_intent=boom,
        read_actual=lambda d: Actual(device=d, platform="eos", reachable=True),
        preview=lambda p: Preview(device=p.device, diff=""),
        apply=lambda p: Preview(device=p.device, diff=""),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    code = cli.check(lab=broken)
    output = capsys.readouterr().out
    assert code == 1
    assert "FAIL" in output
    assert "NetBox intended state" in output


def test_check_fails_when_a_device_is_down(monkeypatch, tmp_path, capsys):
    fake = FakeLab()
    fake.reachable.discard("leaf-02")
    code = cli.check(lab=patched_lab(monkeypatch, fake, tmp_path))
    output = capsys.readouterr().out
    assert code == 1
    assert "NOT READY" in output
    assert "leaf-02" in output


def test_check_reports_the_preview_result(monkeypatch, tmp_path, capsys):
    cli.check(lab=patched_lab(monkeypatch, FakeLab(), tmp_path))
    output = capsys.readouterr().out
    assert "dry-run preview" in output


def test_check_verifies_the_audit_log_is_writable(monkeypatch, tmp_path, capsys):
    cli.check(lab=patched_lab(monkeypatch, FakeLab(), tmp_path))
    assert "audit log writable" in capsys.readouterr().out


def test_check_verifies_the_page_assets_exist(monkeypatch, tmp_path, capsys):
    cli.check(lab=patched_lab(monkeypatch, FakeLab(), tmp_path))
    assert "page assets" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# reset
# --------------------------------------------------------------------------- #


def test_reset_refuses_to_clear_the_audit_log(monkeypatch, tmp_path, capsys):
    """Erasing history in front of a CISO is not a thing this tool does."""
    lab = patched_lab(monkeypatch, FakeLab(), tmp_path)
    code = cli.reset(assume_yes=True, keep_audit=False, lab=lab)
    assert code == 2
    assert lab.audit.read() == []


def test_reset_records_itself_in_the_audit_log(monkeypatch, tmp_path):
    lab = patched_lab(monkeypatch, FakeLab(), tmp_path)
    cli.reset(assume_yes=True, lab=lab)
    kinds = [e["kind"] for e in lab.audit.read()]
    assert "reset" in kinds


def test_reset_keeps_earlier_entries(monkeypatch, tmp_path):
    fake = FakeLab()
    lab = patched_lab(monkeypatch, fake, tmp_path)
    from workbench.scenarios import approve

    approve(lab, "leaf-01", approver="Brian Nelson")
    before = len(lab.audit.applied_changes())

    cli.reset(assume_yes=True, lab=lab)
    assert len(lab.audit.applied_changes()) == before, "a reset must not discard history"


def test_reset_asks_and_does_nothing_on_no(monkeypatch, tmp_path, capsys):
    lab = patched_lab(monkeypatch, FakeLab(), tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    code = cli.reset(assume_yes=False, lab=lab)
    assert code == 0
    assert "Nothing was changed" in capsys.readouterr().out
    assert lab.audit.read() == []


# --------------------------------------------------------------------------- #
# overview
# --------------------------------------------------------------------------- #


def test_overview_prints_one_line_per_device(monkeypatch, tmp_path, capsys):
    code = cli.overview(lab=patched_lab(monkeypatch, FakeLab(), tmp_path))
    output = capsys.readouterr().out
    assert code == 0
    for device in DEMO_DEVICES:
        assert device in output


def test_overview_json_is_machine_readable(monkeypatch, tmp_path, capsys):
    import json

    cli.overview(as_json=True, lab=patched_lab(monkeypatch, FakeLab(), tmp_path))
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


def test_overview_marks_drift(monkeypatch, tmp_path, capsys):
    fake = FakeLab()
    fake.reachable.discard("leaf-03")
    cli.overview(lab=patched_lab(monkeypatch, fake, tmp_path))
    assert "DRIFT" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the parser
# --------------------------------------------------------------------------- #


def test_parser_has_exactly_the_documented_commands():
    parser = cli.build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]
    assert actions, "a subcommand action should exist"
    choices = sorted(actions[0].choices)
    assert choices == ["check", "overview", "reset", "serve", "walkthrough"]


def test_walkthrough_runs_the_whole_demo_with_no_lab(capsys):
    """The presenter's own check: the flow works, independent of whether the lab is up."""
    code = cli.walkthrough()
    output = capsys.readouterr().out
    assert code == 0
    assert "all 16 steps" in output or "PASS" in output
