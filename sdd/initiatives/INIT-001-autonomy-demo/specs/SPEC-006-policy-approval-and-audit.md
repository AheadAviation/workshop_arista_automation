# SPEC-006 · policy: Approval gate and audit trail

## Metadata

```yaml
spec_id: SPEC-006
initiative_id: INIT-001
title: Approval gate and audit trail
domain: policy
status: complete
primary_prompt: none — the network-architect persona is not installed on this seat; authored directly (see Deviations)
supplement_prompts: []
complexity: medium
complexity_reason: The logic is small, but it is the artefact the CISO evaluates, and the invariant it enforces (no applied change without a named human) is the one thing in this demo that must never be wrong.
model: sonnet
autonomy_level: auto_tests
gate_actions: []
reversibility_class: branch
security_review: required
estimated_effort: 4 hours
blocked_by: SPEC-002
blocks: SPEC-008
```

---

## Description

**Context:** Chris Veith flagged that the CISO will be in the room, and Brian Bradley said audits keep
asking for network documentation. This spec is the answer to both: a change cannot happen without a
named human, and every change is on a record that survives a reset.

The gate is enforced in code, not by policy statement. There is no code path from a plan to a device
that does not require an approver.

**Scope (in):** The pending-change object, the approval and rejection paths, the append-only JSONL audit
log, and the invariant that an applied change always carries an approver.

**Scope (out):** Authentication (the page is loopback-only and the demo is single-operator; that is
stated in the runbook rather than pretended away), and any integration with a ticketing system —
deliberately excluded by the approved plan.

---

## Acceptance Criteria

- [x] ac-1: A change with an empty diff cannot be staged for approval (REQ-003).
- [x] ac-2: Applying requires a non-blank approver, refused both in the gate and at the HTTP boundary
  (REQ-006).
- [x] ac-3: An `applied` or `approved` audit entry cannot be constructed without a non-blank approver
  (REQ-006).
- [x] ac-4: The audit record carries the approver, the timestamp, and the diff before and after
  (REQ-006).
- [x] ac-5: The record is append-only JSONL and is readable without the application running (REQ-006).
- [x] ac-6: A half-written final line from a crashed process does not make the record unreadable (REQ-006).
- [x] ac-7: A demo reset does not clear the record; the reset is itself recorded (REQ-007).
- [x] ac-8: A rejection is recorded with its reason — a refusal is also a decision (REQ-006).
- [x] ac-9: Staging a change writes nothing to the audit log; only decisions are recorded (REQ-006).

---

## Deliverables

- [x] `workbench/gate.py`
- [x] `tests/test_gate.py`

---

## Gates & Controls

- **Autonomy level:** `auto_tests`
- **Gate actions triggered:** none.
- **Blocking checks:** this spec's tests; the existing suite stays at 206 or more.
- **Rollback plan:** new files.
- **Road considered instead of this gate:** the gate itself is the road considered instead of relying on
  operator discipline. A rule in a runbook would have been cheaper and would not survive a demo under
  pressure, so the refusal is in the code path instead.

---

## Verification Strategy

- **Claim:** no change reaches a device without a named human, and every decision is durably recorded.
- **Check + executor (mechanized):** `python -m pytest tests/test_gate.py -v`.
- **Pass condition:** every refusal test passes — blank approver, whitespace approver, empty diff,
  mismatched device — and `test_audit_log_is_append_only_jsonl` shows two decisions producing two lines.
  A reader can see the invariant enforced in two independent places (`AuditEntry.__post_init__` and
  `approve_and_apply`).

---

## Security

- **Triggers matched:** auth/authz intent (attribution), secrets in logs, tamper-resistance.
- **Security acceptance criteria:**
  - [x] No diff written to the audit log contains a password hash (enforced upstream in SPEC-003's
    diff cleaner, and asserted there).
  - [x] The audit log has no delete or truncate path anywhere in the codebase.
  - [x] The approver string is stored as given and rendered with `textContent`, never as markup.
  - [x] A corrupt line is skipped on read rather than causing an exception, so a crashed process cannot
    destroy the record's usability.

---

## Deviations

- **Persona gap**, as SPEC-002.

---

## Completion Record

All nine acceptance criteria are met. 22 tests. One defect was found by the tests during the spec:
`reject_change` ignored an injected audit log and always wrote to the default path, which would have made
a test (and any custom-path deployment) read the wrong file.
