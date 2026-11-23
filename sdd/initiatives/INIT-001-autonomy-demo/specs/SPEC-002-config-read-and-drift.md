# SPEC-002 · config: Read intended state and actual state; compute drift

## Metadata

```yaml
spec_id: SPEC-002
initiative_id: INIT-001
title: Read intended state and actual state; compute drift
domain: config
status: complete
primary_prompt: none — the network-architect persona is not installed on this seat; authored directly (see Deviations)
supplement_prompts: []
complexity: medium
complexity_reason: The comparison logic is pure and small, but the two readers each have a real failure mode (NetBox unreachable, device unreachable) that must degrade into a visible state rather than a crash, and the EOS parsing must survive real switch output.
model: sonnet
autonomy_level: auto_tests
gate_actions: []
reversibility_class: branch
security_review: standard
estimated_effort: 4 hours
blocked_by: SPEC-001
blocks: SPEC-003, SPEC-006, SPEC-007
```

---

## Description

**Context:** The page's entire claim is "here is what the network should be, here is what it is, here is the
difference." This spec builds the two readers that make that true and the pure function that compares them.

Two readers, deliberately separate from the comparison:
- intended state, from NetBox (the fabric topology and the rendered config context) and, for the FRR track, the
  AVD group_vars;
- actual state, from the device itself, over its own management interface.

The comparison is a pure function of two `Intent`/`Actual` values, which is what lets the whole idea be tested
with no lab, no NetBox and no switch.

**Scope (in):** The NetBox reader, the EOS reader, the comparison, the degradation rules for an unreachable
NetBox or unreachable device, and the read-only API shape the page will call.

**Scope (out):** Applying anything (SPEC-003). The FRR reader (SPEC-005) — the comparison is written against
the platform-neutral contract so it needs no change when that lands.

---

## Acceptance Criteria

- [x] ac-1: The intended-state reader produces an `Intent` per device in the fabric from NetBox, and produces it
  deterministically from the same input twice (REQ-002, REQ-005).
- [x] ac-2: The actual-state reader produces an `Actual` per device, and an unreachable device yields
  `reachable=False` with the error text rather than an exception escaping (REQ-004).
- [x] ac-3: `compute_drift(intent, actual)` returns one `Drift` per real difference and an empty list when the
  device matches intent — including when the device is a superset or subset of the intended VLAN set (REQ-002, REQ-004).
- [x] ac-4: A device unreachable on both sides, or absent from one side, produces a `Drift` that says so in
  words a non-engineer understands, never an empty result that reads as "healthy" (REQ-004).
- [x] ac-5: The EOS reader parses real `show vlan` and `show interfaces trunk` output (fixtures captured from the
  lab), not a made-up format (REQ-002).
- [x] ac-6: The comparison never mutates its inputs and is safe to call with the same arguments repeatedly (REQ-003).
- [x] ac-7: Reading the whole fabric needs no credentials beyond what the existing lab already ships, and no new
  container (REQ-007).

---

## Deliverables

- [x] `workbench/intent_source.py` — NetBox and AVD readers producing `Intent`
- [x] `workbench/device_reader.py` — EOS reader producing `Actual`, with an injectable command runner
- [x] `workbench/drift.py` — `compute_drift` and the per-kind comparators
- [x] `tests/test_intent_source.py`, `tests/test_device_reader.py`, `tests/test_drift.py`
- [x] `tests/fixtures/eos/*.txt` — captured `show vlan` / `show interfaces trunk` output

---

## Gates & Controls

- **Autonomy level:** `auto_tests` — code and tests on a branch.
- **Gate actions triggered:** none. The readers are read-only against NetBox and the switches.
- **Blocking checks:** this spec's tests, and the existing suite stays at 206 or more.
- **Rollback plan:** new files; deleting them restores the tree.
- **Road considered instead of this gate:** n/a.

---

## Verification Strategy

- **Claim:** the demo can state, for every device, what the network should be and what it is, and the difference
  between them is computed correctly for the ordinary cases and stated honestly for the broken ones.
- **Check + executor (mechanized):** `python -m pytest tests/test_drift.py tests/test_device_reader.py
  tests/test_intent_source.py -v`. The reader tests run against captured fixtures; the intent tests against a fake
  NetBox response.
- **Pass condition:** every test passes; a reader can point at the fixture file and the parser and see they
  correspond; and the unreachable/absent cases each have a named test asserting a `Drift` with a human-readable
  `detail` rather than an empty list. `compute_drift` is called twice on the same inputs in a test and the second
  result equals the first.

---

## Security

- **Triggers matched:** external input (device output parsed into the page), credentials handling.
- **Security acceptance criteria:**
  - [x] No credential is written to the audit log, the page payload, or an exception message. The EOS reader takes
    a password or key by name from the environment and never echoes it.
  - [x] Device output is treated as untrusted text: it is parsed, and any text echoed to the page is escaped by the
    renderer rather than interpolated into HTML.
  - [x] The NetBox token is read from the environment, never embedded in a URL that could be logged.

---

## Deviations

- **Persona gap.** `primary_prompt` names no persona because the `network` profile's `network-architect` persona
  is not installed on this seat. The work was authored directly rather than dispatching a persona that does not
  exist. Recorded here per `expertise-manufacturing.md`'s guard rule.

---

## Completion Record

All seven acceptance criteria are met. `tests/test_drift.py`, `tests/test_device_reader.py` and
`tests/test_intent_source.py` cover them; the EOS parsing is pinned to fixtures shaped like real output.
