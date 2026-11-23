# SPEC-001 · design: Demo contract — intent, plan, drift and audit shapes

## Metadata

```yaml
spec_id: SPEC-001
initiative_id: INIT-001
title: Demo contract — intent, plan, drift and audit shapes
domain: design
status: complete
primary_prompt: none — the network-architect persona is not installed on this seat; authored directly (see Deviations)
supplement_prompts: []
complexity: medium
complexity_reason: Defines four cross-cutting data shapes that every other spec consumes; wrong here is expensive everywhere else, but each shape is small and the work is well-bounded.
model: sonnet
autonomy_level: auto_tests
gate_actions: []
reversibility_class: branch
security_review: standard
estimated_effort: 3 hours
blocked_by: None
blocks: SPEC-002
```

---

## Description

**Context:** Every other spec in this initiative reads or writes the same four things: what the network should
be, what change is needed, how the device differs from intent, and who approved what. If those four shapes are
invented separately in each spec they will diverge, and the page that has to render all of them will be the
place the divergence surfaces — the night before the demo. This spec fixes them once.

The contract is informed by the NAF workshop's `Plan` / `Skip` / `Executor` / autonomy-level model, which
already survived a four-hour workshop with 60 attendees, retargeted from Junos to Arista EOS and stripped of
its InfluxDB/Kapacitor/push-notification presentation layer.

**Scope (in):** Four dataclasses with validation, their serialisation to plain dicts for JSON, the autonomy
levels, the event-log kinds, and the documented reason for each field. Plus the module that produces them so
there is one import path.

**Scope (out):** Any device contact, any NetBox contact, any HTTP. This spec is pure data and rules; the page
and the device drivers come later. The lifecycle/OS shape is in SPEC-007 because it is not on the demo's
critical path.

---

## Acceptance Criteria

- [x] ac-1: `Intent`, `Plan`, `Drift`, `Change` and `AuditEntry` construct from valid input, and each rejects its
  documented invalid cases with a named error rather than a silent default (REQ-003, REQ-006).
- [x] ac-2: Every shape serialises to a JSON-safe dict and round-trips back through the constructor unchanged
  (REQ-001 — the page consumes exactly these dicts).
- [x] ac-3: `Plan` carries `mode` (`merge`|`replace`), `fmt` (`set`|`text` or the EOS session equivalent), and a
  `compensating` flag, and a `Plan` with an empty `config` is refused (REQ-003).
- [x] ac-4: The autonomy levels are exactly `observe`, `assist`, `autonomous`, and no other value constructs;
  each level's permitted action is stated in one place and tested (REQ-003).
- [x] ac-5: The event-log kinds are a closed set, and an unknown kind raises rather than being recorded (REQ-006).
- [x] ac-6: `AuditEntry` requires an approver, a timestamp, and a before/after diff; there is no code path that
  constructs one with an empty approver (REQ-006).

---

## Deliverables

- [x] `workbench/contracts.py` — the dataclasses, the closed sets, and the validation rules
- [x] `workbench/__init__.py` — package marker and the public exports
- [x] `tests/test_contracts.py` — construction, rejection, round-trip, and closed-set tests

---

## Gates & Controls

- **Autonomy level:** `auto_tests` — code and tests on a branch, nothing merged, nothing pushed.
- **Gate actions triggered:** none — fully reversible / branch-only.
- **Blocking checks:** the spec's own tests, plus the existing suite must stay at 206 or more.
- **Rollback plan:** the work is new files under `workbench/` and `tests/`; deleting them restores the tree.
- **Road considered instead of this gate:** n/a — this spec adds no gate.

---

## Verification Strategy

- **Claim:** the four cross-cutting shapes are defined once, validate what they say they validate, and survive a
  JSON round trip, so the page and every device driver agree on them.
- **Check + executor (mechanized):** `python -m pytest tests/test_contracts.py -v`. Runs anywhere, with no lab
  and no NetBox.
- **Pass condition:** every test in `tests/test_contracts.py` passes, and each of ac-1 through ac-6 is named by
  at least one test whose name identifies the behaviour it pins. A reader can open the test file, match each
  acceptance criterion to a test, and re-run it to the same result.

---

## Completion Record

All six acceptance criteria are met by `tests/test_contracts.py` (47 tests). The closed sets are module-level
tuples with a guard function each, so a typo raises rather than being silently accepted.
