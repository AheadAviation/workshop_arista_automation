"""Preview and apply a change on an Arista switch (INIT-001/SPEC-003).

**The safety decision in this module, stated plainly.**

This repository already has a technique for pushing a *complete* generated config:
open a session, `rollback clean-config` so the session starts empty, load the full
config, and commit — which makes the commit a full replace. That is correct for the
generated config, because the config is complete and the replace is the point.

It is **catastrophic for a targeted change.** If you load "vlan 150 / trunk add 150"
into a clean-config session and commit it, the commit replaces the entire device
configuration with those three lines. The switch loses its management address, its
BGP sessions and its fabric links. Doing that in a lab is annoying; doing it in front
of a hospital chain's CTO is the end of the meeting and possibly the relationship.

So this module has two modes and defaults to the safe one:

* `merge`   — a normal session, which starts from the running config. Committing applies
              only the delta. **This is the default and the only mode the demo uses.**
* `replace` — a clean-config session and a full config, for the existing full-config
              path. Only selectable explicitly.

`preview()` and `apply()` share one code path: both build the session, load, and read
the diff. The only difference is whether the last command is `abort` or `commit`. That
identity is what makes the preview trustworthy — it is not a separate approximation of
what would happen, it is the same thing minus the commit.

Nothing here builds a shell string. The session script goes to the CLI's stdin.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Callable, Literal, Sequence

from workbench.contracts import ContractError, Plan, Preview

#: Runner: take (argument list, stdin), return (stdout, stderr, returncode).
EosRunner = Callable[[Sequence[str], str], "tuple[str, str, int]"]

#: A session name must be safe to type into a device CLI.
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

#: Lines EOS re-renders on every load, so they always appear to differ even when
#: nothing meaningful changed. Filtering them is what makes "no drift" mean no drift.
#: Matched after the diff's own `+`/`-` prefix, and the username body is matched
#: loosely because the privilege/role clauses vary between EOS versions.
#: (This is the same intent as `scripts/eos_session.ps1`'s filter.)
NOISE_RE = re.compile(r"^[+-](?:\S+ )?username \S+ .*secret sha512 ")


class EosApplyError(RuntimeError):
    """The device refused the change, or could not be reached. Fit to show a person."""


@dataclass
class EosResult:
    """What the device said about a session."""

    diff: str
    committed: bool
    raw: str = ""


def clean_diff(raw: str) -> str:
    """The meaningful part of `show session-config diffs`.

    EOS separates the diff header (`+++ system:/running-config`) from the body. Only the
    body is returned, with two kinds of noise removed:

    * the password-hash lines, which EOS re-salts on every load so they always *look*
      changed even when nothing meaningful did;
    * the echoes of the session control commands the CLI prints back, which are not part
      of the diff at all. Without this, an empty diff reads as the single line "abort"
      and the page would report drift on a device that matches intent perfectly.
    """
    lines = raw.splitlines()
    start = 0
    for index, line in enumerate(lines):
        if line.startswith("+++ "):
            start = index + 1
            break
    body = lines[start:] if start else lines
    kept = [
        ln for ln in body
        if ln.strip()
        and not NOISE_RE.match(ln)
        and ln.strip() not in _SESSION_ECHOES
    ]
    return "\n".join(kept)


#: Lines the CLI echoes that are ours, not the device's. Kept as an exact-match set so a
#: genuine config line can never be mistaken for one.
_SESSION_ECHOES = frozenset({
    "abort",
    "commit",
    "end",
    "configure session",
})


def count_changed_lines(diff: str) -> int:
    """Lines actually added or removed — the number the page shows."""
    return sum(1 for ln in diff.splitlines() if ln.startswith(("+", "-")))


# --------------------------------------------------------------------------- #
# The real runner
# --------------------------------------------------------------------------- #


def container_eos_runner(container_prefix: str = "clab-netbox-arista-lab-") -> EosRunner:
    """Run a session script through the device's CLI, the way this lab already does."""

    def run(args: Sequence[str], stdin: str) -> tuple[str, str, int]:
        import subprocess

        try:
            proc = subprocess.run(  # noqa: S603 - argument list, no shell
                list(args),
                input=stdin,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except FileNotFoundError as exc:
            return "", f"docker is not available: {exc}", 127
        except subprocess.TimeoutExpired:
            return "", "the device did not answer in 60 seconds", 124
        return proc.stdout or "", proc.stderr or "", proc.returncode

    return run


# --------------------------------------------------------------------------- #
# The session script
# --------------------------------------------------------------------------- #


def build_session_script(
    config: str,
    session: str,
    *,
    mode: Literal["merge", "replace"],
    finish: Literal["abort", "commit"],
) -> str:
    """The exact lines sent to the switch's CLI.

    Kept as its own function because it is the one thing worth reading in full before
    trusting this module, and because the tests assert on it directly.

    `replace` starts the session from an empty config (`rollback clean-config`), which
    is why a commit in that mode is a full replace. `merge` does not, so a commit only
    applies the delta.
    """
    if not SESSION_RE.match(session):
        raise ContractError(f"unsafe session name {session!r}")

    lines = [f"configure session {session}"]
    if mode == "replace":
        lines.append("rollback clean-config")
    lines.append(config.rstrip("\n"))
    lines.append("show session-config diffs")
    lines.append(finish)
    return "\n".join(lines) + "\n"


def _cli_args(container: str) -> list[str]:
    return ["docker", "exec", "-i", container, "Cli", "-p", "15"]


def _unique_session(base: str) -> str:
    """A session name no earlier attempt used.

    EOS keeps a completed session's name reserved, so `configure session X` fails with
    "already completed" if X was used before. A fixed name therefore makes the second run of
    any change fail permanently; a short suffix makes each attempt independent.
    """
    suffix = uuid.uuid4().hex[:6]
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", base)[:24]
    return f"{stem}_{suffix}"


def _run_session(
    device: str,
    plan: Plan,
    session: str,
    finish: Literal["abort", "commit"],
    runner: EosRunner,
    container_prefix: str,
) -> EosResult:
    if not SESSION_RE.match(device):
        raise ContractError(f"invalid device name {device!r}")

    # A session name must be unique per attempt. EOS refuses to re-enter a session it has
    # already completed ("Cannot enter session X (already completed)"), so a fixed name makes
    # every retry fail forever after the first run -- which is exactly what happened live.
    session = _unique_session(session)
    container = f"{container_prefix}{device}"
    script = build_session_script(plan.config, session, mode=plan.mode, finish=finish)  # type: ignore[arg-type]
    out, err, code = runner(_cli_args(container), script)

    blob = f"{out}\n{err}"
    if code != 0:
        raise EosApplyError(_explain(code, blob, container))
    # EOS reports configuration errors on stdout with a zero exit code.
    if "% Invalid input" in blob or "% Error" in blob:
        raise EosApplyError(
            f"{device} rejected the change: {_first_complaint(blob) or 'invalid input'}"
        )

    diff = clean_diff(out)
    if finish == "commit":
        _persist_to_startup(device, runner, container)
    return EosResult(diff=diff, committed=finish == "commit", raw=out)


def _persist_to_startup(device: str, runner: EosRunner, container: str) -> None:
    """Save the running config to startup-config after a commit.

    Found on the live lab: cEOS runs ZTP when there is no `startup-config`, so a change held
    only in running-config was silently reverted a minute or two later and the switch fell
    back to `localhost`. Every commit therefore has to be persisted, or the demo appears to
    work and then undoes itself.

    A failure here is reported rather than swallowed: a change that will not survive is worse
    than a change that failed, because the presenter finds out in front of the customer.
    """
    out, err, code = runner(
        _cli_args(container),
        "copy running-config startup-config\n",
    )
    blob = f"{out}\n{err}"
    if code != 0 or "% Invalid" in blob or "% Error" in blob:
        raise EosApplyError(
            f"{device} committed the change but could not save it to startup-config, so it "
            f"may be reverted: {_first_complaint(blob) or blob.strip().splitlines()[:1] or 'no output'}"
        )


def _first_complaint(blob: str) -> str:
    for line in blob.splitlines():
        if "% Invalid input" in line or "% Error" in line:
            return line.strip()[:300]
    return ""


def _explain(code: int, blob: str, container: str) -> str:
    """A reason a person can act on, rather than an exit code."""
    if code == 127:
        return "docker is not available on this machine"
    if code == 124:
        return "the device did not answer in 60 seconds"
    if "No such container" in blob or "is not running" in blob:
        return f"container {container} is not running (deploy the lab first)"
    first = (blob.strip().splitlines() or ["no output"])[0][:300]
    return f"the session on {container} failed (exit {code}): {first}"


# --------------------------------------------------------------------------- #
# The two operations
# --------------------------------------------------------------------------- #


def preview(
    plan: Plan,
    *,
    runner: EosRunner | None = None,
    session: str = "workbench_preview",
    container_prefix: str = "clab-netbox-arista-lab-",
) -> Preview:
    """What this plan would change, without changing it.

    An empty diff means the device already matches intent — the demo's definition of
    "no drift". The session is aborted, never committed.
    """
    result = _run_session(
        plan.device,
        plan,
        session,
        "abort",
        runner or container_eos_runner(container_prefix),
        container_prefix,
    )
    return Preview(
        device=plan.device,
        diff=result.diff,
        changed_lines=count_changed_lines(result.diff),
    )


def apply_change(
    plan: Plan,
    *,
    runner: EosRunner | None = None,
    session: str = "workbench_apply",
    container_prefix: str = "clab-netbox-arista-lab-",
) -> Preview:
    """Commit the plan, but only if it would actually change something.

    Committing an empty diff is not a no-op a customer wants to watch: it creates a
    commit on the device, moves the configuration revision, and proves nothing. So an
    empty diff is returned as a preview with `committed=False` and nothing is sent.

    Callers must have an approver on record before reaching here; the audit entry that
    records this change refuses to construct without one (`AuditEntry`'s invariant).
    """
    return _apply_or_abort(
        plan, runner or container_eos_runner(container_prefix), session, container_prefix
    )


def _apply_or_abort(
    plan: Plan, runner: EosRunner, session: str, container_prefix: str
) -> Preview:
    # First: what would this do? Same code path as `preview`, so the answer is real.
    first = _run_session(plan.device, plan, session, "abort", runner, container_prefix)
    if not first.diff.strip():
        return Preview(device=plan.device, diff="", changed_lines=0)

    # It would change something, so commit it — a second, identical session that ends
    # in `commit` instead of `abort`. The name is derived from the preview's, and both
    # get their own unique suffix inside `_run_session`.
    second = _run_session(plan.device, plan, session, "commit", runner, container_prefix)
    return Preview(
        device=plan.device,
        diff=second.diff or first.diff,
        changed_lines=count_changed_lines(second.diff or first.diff),
    )


# --------------------------------------------------------------------------- #
# Building the Philips plan
# --------------------------------------------------------------------------- #


def vlan_across_trunks_plan(
    device: str, vlan: int, vlan_name: str, trunks: Sequence[str]
) -> Plan:
    """The change the customer described: one new VLAN, every trunk on the switch.

    Uses EOS's `allowed vlan add` rather than replacing the allow-list, because adding
    is idempotent and cannot drop a VLAN somebody else added. A replace would be the
    wrong instrument here and would need the whole list to be authoritative — which, on
    a switch with local VLANs, it is not.
    """
    if not (1 <= vlan <= 4094):
        raise ContractError(f"vlan {vlan} is outside 1-4094")
    if not trunks:
        raise ContractError("a trunk list is required; there is nothing to add the VLAN to")

    name = re.sub(r"[^A-Za-z0-9_.-]", "_", vlan_name).strip("_")[:32]
    if not name:
        name = f"VLAN{vlan}"
    lines = [f"vlan {vlan}", f"   name {name}"]
    for port in trunks:
        lines.append(f"interface {port}")
        lines.append(f"   switchport trunk allowed vlan add {vlan}")
    return Plan(
        device=device,
        platform="eos",
        usecase="vlan-across-trunks",
        fmt="text",
        config="\n".join(lines),
        mode="merge",
        reason=f"add VLAN {vlan} ({name}) to {len(trunks)} trunk(s)",
    )


__all__ = [
    "EosApplyError",
    "EosResult",
    "build_session_script",
    "clean_diff",
    "container_eos_runner",
    "count_changed_lines",
    "apply_change",
    "preview",
    "vlan_across_trunks_plan",
]
