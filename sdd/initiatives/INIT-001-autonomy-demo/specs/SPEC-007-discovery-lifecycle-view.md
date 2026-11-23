# SPEC-007 · discovery: Lifecycle and OS-versus-intent view

## Metadata

```yaml
spec_id: SPEC-007
initiative_id: INIT-001
title: Lifecycle and OS-versus-intent view
domain: discovery
status: complete
primary_prompt: none — the network-architect persona is not installed on this seat; authored directly (see Deviations)
supplement_prompts: []
complexity: medium
complexity_reason: Small surface, but it must resist the temptation to invent EOL data, which would be the easiest way to mislead a CISO.
model: sonnet
autonomy_level: auto_tests
gate_actions: []
reversibility_class: branch
security_review: standard
estimated_effort: 2 hours
blocked_by: SPEC-002
blocks: SPEC-008
```

---

## Description

**Context:** Brian Wright said the CVE and upgrade problem is a big pain point because they cannot find
outages to patch, and that ten-year-old gear has multiple vulnerabilities they have no way to see. This
view turns that into a picture.

**Scope (in):** Per-device compliance state and software version, fleet counts, and the honest note
about where the versions came from.

**Scope (out):** Any EOL or CVE database. Deliberately excluded: shipping invented vendor EOL dates
would be the single easiest way to mislead a security-conscious audience, and it is not needed to make
the point.

---

## Acceptance Criteria

- [x] ac-1: Every device appears with its compliance state and the software version read from the device
  itself (REQ-004).
- [x] ac-2: A device that could not be read appears as drifted with its reason, not omitted and not
  green (REQ-004).
- [x] ac-3: The view states plainly that versions were read from the devices, so no listener assumes an
  EOL feed that is not there (REQ-004, REQ-008).
- [x] ac-4: A per-device target version is carried through from intent, so setting one in NetBox turns
  this into an upgrade worklist (REQ-004).
- [x] ac-5: Fleet counts by version are reported (REQ-004).

---

## Deliverables

- [x] `lifecycle()` in `workbench/scenarios.py`
- [x] Its tests in `tests/test_scenarios.py`

---

## Gates & Controls

- **Autonomy level:** `auto_tests`
- **Gate actions triggered:** none.
- **Blocking checks:** this spec's tests; the existing suite stays at 206 or more.
- **Rollback plan:** new code in an existing module.
- **Road considered instead of this gate:** n/a.

---

## Verification Strategy

- **Claim:** the view reports what the fleet is actually running and what is not matching intent, without
  implying data it does not have.
- **Check + executor (mechanized):** `python -m pytest tests/test_scenarios.py -k lifecycle -v`.
- **Pass condition:** every lifecycle test passes; specifically `test_lifecycle_says_where_the_os_versions_came_from`
  asserts the disclosure is present, and `test_lifecycle_marks_an_unreachable_device` asserts an
  unreadable device is a finding rather than an omission.

---

## Deviations

- **Persona gap**, as SPEC-002.

---

## Completion Record

All five acceptance criteria are met. The deliberate absence of EOL data is stated in the view's own note
so a listener cannot mistake this for a lifecycle-management platform.
