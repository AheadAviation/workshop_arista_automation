"""The approval gate and the audit trail (INIT-001/SPEC-006).

Two jobs, both for the CISO in the room:

1. **A change cannot happen without a human.** Not by policy statement — by code path.
   `request_change` produces something that can only be consumed by `approve_and_apply`,
   which requires a named approver or raises. There is no argument that skips it.

2. **Every change is on the record.** Who approved it, when, what the device looked like
   before and after, and the diff that proves it. The record is append-only JSONL, so it
   survives a demo reset, is readable without the app running, and is the artefact an
   auditor is actually handed.

Design choice worth stating: the audit file is **not** reset by the demo reset. A reset
puts the *lab* back; it does not rewrite history. Erasing audit records on a reset would
be the single most alarming thing this demo could do in front of a hospital chain, so
`demo_reset` writes its own `reset` entry and leaves everything before it intact.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from workbench.contracts import (
    AUTONOMY_LEVELS,
    AuditEntry,
    ContractError,
    Plan,
    Preview,
    may_act_unattended,
    utc_now,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_PATH = REPO_ROOT / "output" / "workbench" / "audit.jsonl"


# --------------------------------------------------------------------------- #
# The pending change
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PendingChange:
    """A change that has been previewed and is waiting for a human.

    Frozen, and the only way to turn one into an applied change is
    `approve_and_apply`, which demands an approver name. That is the whole gate: there
    is no `pending.plan` path to the device that does not go through it.
    """

    change_id: str
    plan: Plan
    preview: Preview
    requested_at: str = field(default_factory=utc_now)
    requested_by: str = "workbench"

    @property
    def is_empty(self) -> bool:
        return self.preview.is_empty

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "device": self.plan.device,
            "usecase": self.plan.usecase,
            "summary": self.plan.summary,
            "diff": self.preview.diff,
            "changed_lines": self.preview.changed_lines,
            "is_empty": self.is_empty,
            "requested_at": self.requested_at,
            "requested_by": self.requested_by,
        }


class GateRefused(ContractError):
    """The gate said no. Always carries the reason, because the page shows it."""


def request_change(
    change_id: str,
    plan: Plan,
    preview: Preview,
    *,
    autonomy: str = "assist",
    requested_by: str = "workbench",
) -> PendingChange:
    """Stage a previewed change for approval.

    Refused outright when the diff is empty: there is nothing to approve, and offering
    an approval button for a change that does nothing teaches the wrong lesson on screen.
    """
    if autonomy not in AUTONOMY_LEVELS:
        raise GateRefused(f"unknown autonomy level {autonomy!r}")
    if not may_act_unattended(autonomy, "preview"):
        raise GateRefused(
            f"autonomy is {autonomy!r}: this level may watch but not prepare a change"
        )
    if preview.device != plan.device:
        raise GateRefused(
            f"preview is for {preview.device} but the plan is for {plan.device}"
        )
    if preview.is_empty:
        raise GateRefused(
            f"{plan.device} already matches intended state; there is nothing to approve"
        )
    return PendingChange(
        change_id=change_id,
        plan=plan,
        preview=preview,
        requested_by=requested_by,
    )


def approve_and_apply(
    pending: PendingChange,
    approver: str,
    *,
    audit: "AuditLog | None" = None,
    apply: Callable[[Plan], Preview] | None = None,
) -> AuditEntry:
    """Apply a pending change, on the record, with a named human behind it.

    The approver is required and checked here as well as in `AuditEntry`, deliberately
    twice: this is the function a page button calls, and a caller that forgets is a bug
    the demo must not survive.
    """
    if not approver or not approver.strip():
        raise GateRefused("a change cannot be applied without a named approver")

    if apply is None:
        from workbench.eos_executor import apply_change as apply

    log = audit or AuditLog()

    applied = apply(pending.plan)

    if applied.is_empty:
        # Someone re-approved a change that no longer needs doing (the device was fixed
        # in between). Record it honestly rather than claiming a change that did not
        # happen.
        entry = AuditEntry(
            kind="no-drift",
            device=pending.plan.device,
            summary=(
                f"{pending.plan.usecase}: approved by {approver.strip()}, but the device "
                "already matched intended state so nothing was sent"
            ),
            autonomy="assist",
            approver=approver.strip(),
            event_id=pending.change_id,
        )
        log.append(entry)
        return entry

    entry = AuditEntry(
        kind="applied",
        device=pending.plan.device,
        summary=f"{pending.plan.usecase}: {pending.plan.summary}",
        autonomy="assist",
        approver=approver.strip(),
        event_id=pending.change_id,
        diff_before=pending.preview.diff,
        diff_after=applied.diff,
    )
    log.append(entry)
    return entry


def reject_change(
    pending: PendingChange,
    approver: str,
    reason: str = "",
    *,
    audit: "AuditLog | None" = None,
) -> AuditEntry:
    """A human said no. Recorded, because a refusal is also a decision."""
    if not approver or not approver.strip():
        raise GateRefused("a rejection must be attributable to a named person")
    entry = AuditEntry(
        kind="rejected",
        device=pending.plan.device,
        summary=(
            f"{pending.plan.usecase}: rejected by {approver.strip()}"
            + (f" — {reason}" if reason else "")
        ),
        autonomy="assist",
        approver=approver.strip(),
        event_id=pending.change_id,
        diff_before=pending.preview.diff,
    )
    (audit or AuditLog()).append(entry)
    return entry


# --------------------------------------------------------------------------- #
# The audit log
# --------------------------------------------------------------------------- #


class AuditLog:
    """Append-only JSONL. The artefact an auditor is handed.

    One line per event, so it can be read with any text tool, tailed during a demo, and
    imported into anything. Nothing here rewrites or deletes a line, and that is
    deliberate: `demo_reset` adds a `reset` entry rather than clearing the file.
    """

    def __init__(self, path: Path | None = None) -> None:
        override = os.environ.get("WORKBENCH_AUDIT_LOG")
        self.path = Path(path or override or DEFAULT_AUDIT_PATH)

    def append(self, entry: AuditEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), default=str) + "\n")

    def read(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Newest first, which is the order the page shows."""
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    entries.append(json.loads(text))
                except json.JSONDecodeError:
                    # A half-written final line (the process died mid-append) must not
                    # make the whole record unreadable.
                    continue
        entries.reverse()
        return entries[:limit] if limit else entries

    def for_device(self, device: str, limit: int | None = None) -> list[dict[str, Any]]:
        matches = [e for e in self.read() if e.get("device") == device]
        return matches[:limit] if limit else matches

    def applied_changes(self) -> list[dict[str, Any]]:
        """Only the changes that actually reached a device — the audit question."""
        return [e for e in self.read() if e.get("kind") == "applied"]


def append_entry(log: AuditLog, entry: AuditEntry) -> None:
    """Record any event that is not a decision.

    Approvals and rejections go through `approve_and_apply` / `reject_change` because
    they must be attributable. Everything else (a change was staged, drift was injected)
    is context, and this is the one way to write it, so the log format stays uniform.
    """
    log.append(entry)


__all__ = [
    "AuditLog",
    "GateRefused",
    "PendingChange",
    "append_entry",
    "approve_and_apply",
    "reject_change",
    "request_change",
]
