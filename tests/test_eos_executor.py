"""EOS executor tests (INIT-001/SPEC-003).

The most important tests in this file are the ones that pin the *session script*: they
are what stops a targeted change from being committed as a full configuration replace,
which would wipe a switch in front of a customer.
"""

from __future__ import annotations

import pytest

from workbench.contracts import ContractError, Plan
from workbench.eos_executor import (
    EosApplyError,
    apply_change,
    build_session_script,
    clean_diff,
    count_changed_lines,
    preview,
    vlan_across_trunks_plan,
)


def plan(**kwargs) -> Plan:
    base = dict(
        device="leaf-01",
        platform="eos",
        usecase="vlan-across-trunks",
        fmt="text",
        config="vlan 150\n   name PHILIPS_MEDICAL",
        mode="merge",
        reason="add vlan",
    )
    base.update(kwargs)
    return Plan(**base)


# --------------------------------------------------------------------------- #
# The session script — the safety-critical part
# --------------------------------------------------------------------------- #


def test_merge_session_does_not_start_from_a_clean_config():
    """This is the assertion that stops a targeted change becoming a full replace."""
    script = build_session_script("vlan 150", "s1", mode="merge", finish="abort")
    assert "rollback clean-config" not in script
    assert script.startswith("configure session s1")


def test_replace_session_does_start_from_a_clean_config():
    """The full-config path still works; it is just never the default."""
    script = build_session_script("hostname x", "s1", mode="replace", finish="abort")
    assert "rollback clean-config" in script


def test_preview_session_ends_in_abort_not_commit():
    script = build_session_script("vlan 150", "s1", mode="merge", finish="abort")
    assert script.strip().endswith("abort")
    assert "commit" not in script.splitlines()[-1]


def test_apply_session_ends_in_commit():
    script = build_session_script("vlan 150", "s1", mode="merge", finish="commit")
    assert script.strip().endswith("commit")


def test_session_script_shows_the_diff_before_finishing():
    script = build_session_script("vlan 150", "s1", mode="merge", finish="abort")
    lines = script.splitlines()
    assert lines.index("show session-config diffs") < len(lines) - 1


def test_session_script_carries_the_config_verbatim():
    config = "vlan 150\n   name PHILIPS_MEDICAL\ninterface Ethernet5"
    script = build_session_script(config, "s1", mode="merge", finish="abort")
    assert config in script


def test_unsafe_session_name_is_refused():
    with pytest.raises(ContractError, match="unsafe session name"):
        build_session_script("vlan 1", "s1; reload now", mode="merge", finish="abort")


def test_session_name_with_newline_is_refused():
    with pytest.raises(ContractError, match="unsafe session name"):
        build_session_script("vlan 1", "s1\nreload", mode="merge", finish="abort")


# --------------------------------------------------------------------------- #
# diff cleaning
# --------------------------------------------------------------------------- #

RAW_DIFF = """configure session s1
vlan 150
   name PHILIPS_MEDICAL
show session-config diffs
--- system:/running-config
+++ session:/s1-config-session
@@ -10,6 +10,9 @@
 interface Ethernet5
    switchport mode trunk
-   switchport trunk allowed vlan 100,110
+   switchport trunk allowed vlan 100,110,150
-test username admin privilege 15 role network-admin secret sha512 $6$abc
+test username admin privilege 15 role network-admin secret sha512 $6$def
abort
"""


def test_clean_diff_drops_the_header_and_the_password_noise():
    diff = clean_diff(RAW_DIFF)
    assert "+++ session:" not in diff
    assert "secret sha512" not in diff
    # The real change survives.
    assert "+   switchport trunk allowed vlan 100,110,150" in diff


def test_clean_diff_keeps_the_context_lines_a_reader_needs():
    diff = clean_diff(RAW_DIFF)
    assert "interface Ethernet5" in diff


def test_count_changed_lines_counts_only_real_changes():
    assert count_changed_lines(clean_diff(RAW_DIFF)) == 2


def test_clean_diff_of_empty_output_is_empty():
    assert clean_diff("") == ""


def test_count_changed_lines_of_empty_diff_is_zero():
    assert count_changed_lines("") == 0


# --------------------------------------------------------------------------- #
# Fakes that behave like the device
# --------------------------------------------------------------------------- #


def device_runner(*, diff_lines: str = "+   switchport trunk allowed vlan 100,110,150\n",
                  reject: str = "", code: int = 0, stderr: str = ""):
    """A runner that reports whether the script committed or aborted.

    It also records every script it was given, so the tests can assert on what the
    device was actually asked to do.
    """
    seen: list[str] = []

    def run(args, stdin):
        seen.append(stdin)
        run.seen = seen  # type: ignore[attr-defined]
        if reject:
            return f"% Invalid input: {reject}\n", stderr, code
        header = "--- system:/running-config\n+++ session:/s\n"
        return header + diff_lines + ("commit\n" if stdin.strip().endswith("commit") else "abort\n"), stderr, code

    run.seen = seen  # type: ignore[attr-defined]
    return run


# --------------------------------------------------------------------------- #
# preview
# --------------------------------------------------------------------------- #


def test_preview_reports_the_diff_without_committing():
    run = device_runner()
    result = preview(plan(), runner=run)
    assert "+   switchport trunk allowed vlan 100,110,150" in result.diff
    assert result.changed_lines == 1
    assert all(s.strip().endswith("abort") for s in run.seen), "preview must never commit"


def test_preview_on_a_matching_device_is_empty():
    run = device_runner(diff_lines="")
    result = preview(plan(), runner=run)
    assert result.is_empty is True


def test_preview_surfaces_a_rejection_as_an_error():
    run = device_runner(reject="'allowed'")
    with pytest.raises(EosApplyError, match="rejected the change"):
        preview(plan(), runner=run)


def test_preview_reports_a_missing_container_plainly():
    def run(args, stdin):
        return "", "Error: No such container: clab-netbox-arista-lab-leaf-01", 1

    with pytest.raises(EosApplyError, match="not running"):
        preview(plan(), runner=run)


def test_preview_reports_missing_docker_plainly():
    def run(args, stdin):
        return "", "", 127

    with pytest.raises(EosApplyError, match="docker is not available"):
        preview(plan(), runner=run)


def test_preview_reports_a_timeout_plainly():
    def run(args, stdin):
        return "", "", 124

    with pytest.raises(EosApplyError, match="did not answer"):
        preview(plan(), runner=run)


# --------------------------------------------------------------------------- #
# apply_change — two sessions, abort-then-commit
# --------------------------------------------------------------------------- #


def test_apply_runs_a_preview_first_then_commits():
    run = device_runner()
    result = apply_change(plan(), runner=run)
    assert len(run.seen) == 2, "expected an abort session then a commit session"
    assert run.seen[0].strip().endswith("abort")
    assert run.seen[1].strip().endswith("commit")
    assert result.changed_lines == 1


def test_apply_on_a_matching_device_commits_nothing():
    """An empty diff must not create a commit on the switch."""
    run = device_runner(diff_lines="")
    result = apply_change(plan(), runner=run)
    assert result.is_empty is True
    assert len(run.seen) == 1, "only the preview session should have run"
    assert run.seen[0].strip().endswith("abort")


def test_apply_never_uses_the_clean_config_rollback_for_a_merge_plan():
    run = device_runner()
    apply_change(plan(mode="merge"), runner=run)
    for script in run.seen:
        assert "rollback clean-config" not in script


def test_apply_refuses_an_unsafe_device_name():
    run = device_runner()
    with pytest.raises(ContractError, match="invalid device name"):
        apply_change(plan(device="leaf-01; reload"), runner=run)


# --------------------------------------------------------------------------- #
# the Philips plan builder
# --------------------------------------------------------------------------- #


def test_vlan_plan_uses_allowed_vlan_add_not_a_replace():
    """`add` is idempotent and cannot drop a VLAN someone else added."""
    built = vlan_across_trunks_plan("leaf-01", 150, "PHILIPS_MEDICAL", ["Ethernet5", "Ethernet6"])
    assert "switchport trunk allowed vlan add 150" in built.config
    assert "switchport trunk allowed vlan 150" not in built.config.replace("add 150", "")


def test_vlan_plan_covers_every_trunk():
    built = vlan_across_trunks_plan("leaf-01", 150, "X", ["Ethernet5", "Ethernet6"])
    assert built.config.count("interface Ethernet") == 2


def test_vlan_plan_creates_the_vlan_with_its_name():
    built = vlan_across_trunks_plan("leaf-01", 150, "PHILIPS_MEDICAL", ["Ethernet5"])
    assert "vlan 150" in built.config
    assert "name PHILIPS_MEDICAL" in built.config


def test_vlan_plan_is_a_merge_by_default():
    built = vlan_across_trunks_plan("leaf-01", 150, "X", ["Ethernet5"])
    assert built.mode == "merge"


def test_vlan_plan_refuses_an_out_of_range_vlan():
    with pytest.raises(ContractError, match="outside 1-4094"):
        vlan_across_trunks_plan("leaf-01", 9000, "X", ["Ethernet5"])


def test_vlan_plan_refuses_an_empty_trunk_list():
    with pytest.raises(ContractError, match="trunk list is required"):
        vlan_across_trunks_plan("leaf-01", 150, "X", [])


def test_vlan_plan_sanitises_a_hostile_name():
    """A newline must not become a second CLI command."""
    built = vlan_across_trunks_plan("leaf-01", 150, "bad\nname; reload", ["Ethernet5"])
    name_line = next(ln for ln in built.config.splitlines() if ln.strip().startswith("name "))
    assert "\n" not in name_line
    assert ";" not in name_line
    # The whole config is four lines (vlan / name / interface / switchport), which proves
    # the embedded newline did not split the name into a command of its own.
    assert len(built.config.splitlines()) == 4


def test_vlan_plan_replaces_a_blank_name_with_a_default():
    built = vlan_across_trunks_plan("leaf-01", 150, "   ", ["Ethernet5"])
    assert "name VLAN150" in built.config


def test_vlan_plan_summary_is_readable():
    built = vlan_across_trunks_plan("leaf-01", 150, "PHILIPS_MEDICAL", ["Ethernet5", "Ethernet6"])
    assert "2 trunk" in built.summary
    assert "150" in built.summary
