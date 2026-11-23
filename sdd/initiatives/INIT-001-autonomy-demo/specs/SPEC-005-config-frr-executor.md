# SPEC-005 · config: FRR executor — same intent, second vendor

## Metadata

```yaml
spec_id: SPEC-005
initiative_id: INIT-001
title: FRR executor — same intent, second vendor
domain: config
status: complete
primary_prompt: none — the network-architect persona is not installed on this seat; authored directly (see Deviations)
supplement_prompts: []
complexity: medium
complexity_reason: The comparison and reading are small; the difficulty is resisting the temptation to fake a full second apply path the night before a demo.
model: sonnet
autonomy_level: auto_tests
gate_actions: []
reversibility_class: branch
security_review: standard
estimated_effort: 3 hours
blocked_by: SPEC-003
blocks: None
```

---

## Description

**Context:** Brian Wright, on the CTO's position: *"I don't care what the vendor label is on the switch
if I'm automating across the board."* That claim is worth nothing if the demo only shows one vendor. The
repo already carries a dual-DC track with a real FRR route reflector, so the second vendor costs no new
images.

**Scope (in):** Reading frr-core1's actual state, deriving its intent from the AVD-rendered config,
diffing the two, and a two-vendor comparison the page renders.

**Scope (out):** A full command-level apply path for FRR. FRR is managed in this repo by rendering its
config and calling `frr-reload.py`, and writing a second, weaker executor for a demo would be the worse
of the two options. The limitation is stated on the page rather than hidden.

---

## Acceptance Criteria

- [x] ac-1: frr-core1's actual ASN and hostname are read from the running daemon, and an unreachable
  router returns a value rather than raising (REQ-005, REQ-007).
- [x] ac-2: Its intent is derived from the AVD-rendered config, so the comparison is against what would
  actually be deployed (REQ-005).
- [x] ac-3: The diff ignores the generated-header line, so a compliant router does not read as drifted
  (REQ-004).
- [x] ac-4: A plan for the wrong platform is refused rather than silently executed (REQ-005).
- [x] ac-5: The two-vendor view reports both sides, their intent source, and their real capability, and
  survives the FRR track being absent (REQ-005, REQ-007).
- [x] ac-6: The FRR side states plainly that it is read-and-diff, so no listener assumes a full apply
  path (REQ-008 — the same honesty rule applied to our own capability).

---

## Deliverables

- [x] `workbench/frr_executor.py`
- [x] `two_vendors()` in `workbench/scenarios.py`
- [x] The **Two vendors** page panel
- [x] `tests/test_frr_executor.py`, plus the vendor tests in `tests/test_web.py`

---

## Gates & Controls

- **Autonomy level:** `auto_tests`
- **Gate actions triggered:** none.
- **Blocking checks:** this spec's tests; the existing suite stays at 206 or more.
- **Rollback plan:** new files and an additive page panel.
- **Road considered instead of this gate:** n/a.

---

## Verification Strategy

- **Claim:** the same intent and the same gate can be shown against a second vendor, and the demo is
  honest about which capabilities are full and which are partial.
- **Check + executor (mechanized):** `python -m pytest tests/test_frr_executor.py tests/test_web.py -v`.
- **Pass condition:** every test passes, and `test_two_vendors_reports_both_sides_with_their_limits`
  asserts that the FRR capability string says `read and diff` while the Arista one says `apply`.

---

## Deviations

- **Persona gap**, as SPEC-002.
- **Partial apply path, disclosed.** The FRR side reads and diffs; applying hands off to the track's
  existing `frr-reload.py`. This is a deliberate scope decision, not an oversight, and the page says so.

---

## Completion Record

All six acceptance criteria are met. 17 unit tests plus 3 web tests. The FRR track's rendered config may
not exist on a fresh clone until `dualdc build` runs; the page handles that case and reports it, rather
than failing.
