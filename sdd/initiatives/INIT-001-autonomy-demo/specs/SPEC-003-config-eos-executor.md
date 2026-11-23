# SPEC-003 · config: Arista EOS executor — dry-run and apply

## Metadata

```yaml
spec_id: SPEC-003
initiative_id: INIT-001
title: Arista EOS executor — dry-run and apply
domain: config
status: complete
primary_prompt: none — the network-architect persona is not installed on this seat; authored directly (see Deviations)
supplement_prompts: []
complexity: high
complexity_reason: Contains the single most dangerous decision in the initiative (merge versus a clean-config replace) and is the code path that changes a real switch in front of a customer.
model: sonnet
autonomy_level: auto_tests
gate_actions: []
reversibility_class: branch
security_review: required
estimated_effort: 5 hours
blocked_by: SPEC-002
blocks: SPEC-004, SPEC-005
```

---

## Description

**Context:** The repo already knows how to push a *complete* generated config: open a session,
`rollback clean-config` so the session starts empty, load the full config, commit — a full replace.
That technique is correct for a generated config and **catastrophic for a targeted change**: loading
"vlan 150 / trunk add 150" into a clean session and committing would replace the entire running config
with those three lines, taking the management address, the BGP sessions and the fabric links with it.
On a lab switch that is annoying; in front of a hospital chain's CTO it ends the meeting.

This spec builds an executor that defaults to the safe mode and makes the dangerous one explicit.

**Scope (in):** The EOS session script builder, preview, apply, diff cleaning, error surfacing, and the
Philips VLAN plan builder.

**Scope (out):** Anything multipvendor (SPEC-005), and the gate (SPEC-006) — apply here assumes the
caller has an approver and does not itself record one.

---

## Acceptance Criteria

- [x] ac-1: A `merge` plan's session script does **not** contain `rollback clean-config`, so a commit
  applies only the delta. A `replace` plan's script does. (REQ-003)
- [x] ac-2: `preview` and `apply` share one code path; the only difference is whether the session ends
  in `abort` or `commit`, so the preview is the real thing minus the commit (REQ-003).
- [x] ac-3: Applying a plan whose diff is empty commits nothing, and says so (REQ-003).
- [x] ac-4: A device rejection (`% Invalid input`) and a missing container and a timeout each produce a
  distinct, actionable message rather than an exit code (REQ-007).
- [x] ac-5: The diff is cleaned of the password-hash lines EOS re-salts on every load and of the echoed
  session commands, so "no drift" means no drift (REQ-004).
- [x] ac-6: The VLAN plan uses `allowed vlan add`, not a replace of the allow-list, so it cannot drop a
  VLAN somebody else added (REQ-002).
- [x] ac-7: A device name or session name that could reach a shell or a CLI as more than one token is
  refused before any command runs (REQ-006 security).
- [x] ac-8: A VLAN name containing a newline cannot become a second CLI command (REQ-006 security).

---

## Deliverables

- [x] `workbench/eos_executor.py`
- [x] `tests/test_eos_executor.py`

---

## Gates & Controls

- **Autonomy level:** `auto_tests`
- **Gate actions triggered:** none — the spec writes code and tests; it does not run against a switch
  in this initiative's automated path. Any real application goes through SPEC-006's gate.
- **Blocking checks:** this spec's tests; the existing suite stays at 206 or more.
- **Rollback plan:** new files.
- **Road considered instead of this gate:** n/a.

---

## Verification Strategy

- **Claim:** the executor can show precisely what a change would do without doing it, and cannot
  accidentally convert a targeted change into a full configuration replace.
- **Check + executor (mechanized):** `python -m pytest tests/test_eos_executor.py -v`, including
  `test_merge_session_does_not_start_from_a_clean_config` and
  `test_apply_never_uses_the_clean_config_rollback_for_a_merge_plan`.
- **Pass condition:** every test passes; specifically, a reader can open the session-script builder and
  see the two modes, and the two tests above pin that the default path cannot replace a whole config.
  The preview test asserts that no script in a preview run ends in `commit`.

---

## Security

- **Triggers matched:** external input reaching a command path; credentials.
- **Security acceptance criteria:**
  - [x] Commands are passed as an argument list with no shell, and the session script goes to stdin.
  - [x] Device and session names are validated against a strict pattern before use.
  - [x] A VLAN name is sanitised to a single token, so a newline cannot inject a command.
  - [x] Password-hash lines are stripped from diffs before they reach the page or the audit log, so a
    secret hash is never rendered or recorded.

---

## Deviations

- **Persona gap**, as SPEC-002.

---

## Completion Record

All eight acceptance criteria are met. Two real defects were found and fixed during this spec by its own
tests: `clean_diff` did not strip the echoed session commands (so an empty diff read as the single line
`abort`, which would have shown drift on a perfectly matching device), and the noise filter did not match
the `+`/`-`-prefixed form of the password line.
