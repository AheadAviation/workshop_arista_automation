# SPEC-004 · validation: VLAN-across-trunks scenario, end to end

## Metadata

```yaml
spec_id: SPEC-004
initiative_id: INIT-001
title: VLAN-across-trunks scenario, end to end
domain: validation
status: complete
primary_prompt: none — the network-architect persona is not installed on this seat; authored directly (see Deviations)
supplement_prompts: []
complexity: high
complexity_reason: This is the story the demo exists for; every seam (intent, drift, executor, gate, audit) has to line up, and it is where the closed loop was found not to close on the first attempt.
model: sonnet
autonomy_level: auto_tests
gate_actions: []
reversibility_class: branch
security_review: standard
estimated_effort: 5 hours
blocked_by: SPEC-003
blocks: SPEC-008
```

---

## Description

**Context:** Brian Bradley's story from the call, in his words: a new clinical system arrives, needs a
VLAN, "you need to reallocate that VLAN across a series of trunks... switch port VLAN add... to a trunk
with a bunch of other VLANs on it already." That is the change the demo exists to show, and it has to
work end to end rather than as a series of disconnected screens.

**Scope (in):** The scenario functions the page calls — overview, propose, approve, inject drift,
remediate, lifecycle, audit view — composed so each one is a story, plus the `Lab` seam that makes the
whole narrative testable.

**Scope (out):** The HTTP layer and the page (SPEC-008). The FRR executor (SPEC-005).

---

## Acceptance Criteria

- [x] ac-1: Proposing the VLAN produces a non-empty diff and applies nothing (REQ-003).
- [x] ac-2: Approving with a named approver applies the change and records it; approving with a blank
  name is refused and applies nothing (REQ-006).
- [x] ac-3: After the change, the device reads as compliant against intent (REQ-002).
- [x] ac-4: Injecting drift makes the device read as drifted, with the port and VLAN named (REQ-004).
- [x] ac-5: Remediating through the same gate restores compliance, and the loop can be closed
  repeatedly (REQ-003, REQ-004).
- [x] ac-6: Re-approving a change that is no longer needed records "nothing was sent" rather than
  claiming a change that did not happen (REQ-006).
- [x] ac-7: An unreachable NetBox and an unreachable device each produce a page-shaped result with a
  readable reason, never a raised exception (REQ-007).
- [x] ac-8: Remediation is additive only: it never removes a VLAN that intent does not know about
  (REQ-002).
- [x] ac-9: The lifecycle view reports every device with the software version read from the device
  (REQ-004).
- [x] ac-10: The whole narrative is exercisable with no lab, through the `Lab` seam (REQ-007).

---

## Deliverables

- [x] `workbench/scenarios.py`
- [x] `tests/test_scenarios.py` (fake six-switch lab)

---

## Gates & Controls

- **Autonomy level:** `auto_tests`
- **Gate actions triggered:** none.
- **Blocking checks:** this spec's tests; the existing suite stays at 206 or more.
- **Rollback plan:** new files.
- **Road considered instead of this gate:** n/a.

---

## Verification Strategy

- **Claim:** the change the customer described works end to end, including the loop back to compliance
  after drift, and every refusal is a first-class result rather than an exception.
- **Check + executor (mechanized):** `python -m pytest tests/test_scenarios.py -v`, notably
  `test_the_full_loop_through_http` in `tests/test_web.py` and
  `test_inject_drift_then_overview_catches_it`.
- **Pass condition:** every test passes, and the closed-loop test asserts all four states in order:
  compliant → drifted after injection → remediated → compliant. A reader can follow that one test and
  see the entire demo's narrative.

---

## Deviations

- **Persona gap**, as SPEC-002.
- **First attempt did not close the loop.** The initial `inject_drift` removed an intended VLAN while
  `approve` only ever added the demo VLAN, so nothing could restore what was broken. The test caught it.
  `remediate` was added as the missing half, additive-only, and the fake lab was corrected to keep
  intended and actual state genuinely separate — which was the root cause.

---

## Completion Record

All ten acceptance criteria are met. 35 tests. The closed loop is proven, and the fake lab now models
intended and actual state separately, which is what the demo itself does.
