# SPEC-008 · runbook: Browser page, reset, and the morning rehearsal

## Metadata

```yaml
spec_id: SPEC-008
initiative_id: INIT-001
title: Browser page, reset, and the morning rehearsal
domain: runbook
status: complete
primary_prompt: none — the network-architect persona is not installed on this seat; authored directly (see Deviations)
supplement_prompts: []
complexity: high
complexity_reason: The last thing between the build and the meeting; it has to look credible to a CIO and a CISO, never show a stack trace, and be operable under pressure at 8am.
model: sonnet
autonomy_level: auto_tests
gate_actions: []
reversibility_class: branch
security_review: required
estimated_effort: 6 hours
blocked_by: SPEC-004, SPEC-006
blocks: None
```

---

## Description

**Context:** Brian Nelson's brief: "using more of a GUI front end, trying to stay away from CLI as much
as I can, but kind of visualize it." So the page **is** the demo. It also has to survive a demo: a
presenter who sees a stack trace in front of a customer has lost the room.

**Scope (in):** The HTTP API, the page, the CLI (`serve`, `check`, `reset`, `overview`), the run.ps1
wiring, and the runbook.

**Scope (out):** Authentication beyond loopback binding (stated in the runbook, not pretended), and any
build step — the page is hand-written so there is nothing to compile the night before.

---

## Acceptance Criteria

- [x] ac-1: The page renders a fabric view, a day-two change view, a lifecycle view and an audit view,
  and shows no terminal or CLI (REQ-001).
- [x] ac-2: The approve control is rendered only for a non-empty, unapplied change (REQ-003).
- [x] ac-3: No endpoint returns a 500 for a bad lab: an unreachable NetBox yields a page-shaped error
  with the reason (REQ-007).
- [x] ac-4: Untrusted text from a device is inserted as text and never as markup; pinned by a test that
  fails if `innerHTML` is used in code (REQ-006).
- [x] ac-5: Static files are served from a fixed allow-list, so no path is built from user input
  (REQ-006).
- [x] ac-6: The server binds to loopback by default, and says so plainly when told to bind elsewhere
  (REQ-006).
- [x] ac-7: `workbench check` reports READY or NOT READY, names each failing step, and exits non-zero
  when not ready (REQ-007).
- [x] ac-8: `workbench reset` returns the fabric to intended state, records itself, and refuses to clear
  the audit log (REQ-007, REQ-006).
- [x] ac-9: The runbook covers the 15-minute demo order, the honest limits, a symptom-to-action table,
  and how to run it twice (REQ-007, REQ-008).
- [x] ac-10: The page is reachable from `run.ps1` under documented commands (REQ-007).

---

## Deliverables

- [x] `workbench/web.py`, `workbench/static/{index.html,workbench.css,workbench.js}`
- [x] `workbench/cli.py`
- [x] `run.ps1` — `workbench`, `workbench-check`, `workbench-reset`, `workbench-status`
- [x] `wiki/Autonomy-Workbench.md` — the runbook
- [x] `tests/test_web.py`, `tests/test_cli.py`

---

## Gates & Controls

- **Autonomy level:** `auto_tests`
- **Gate actions triggered:** none.
- **Blocking checks:** this spec's tests; the existing suite stays at 206 or more.
- **Rollback plan:** new files, plus a small additive block in `run.ps1` that can be removed.
- **Road considered instead of this gate:** n/a.

---

## Verification Strategy

- **Claim:** the page can be operated by a non-engineer, cannot silently apply a change, and degrades to
  a readable message rather than a stack trace.
- **Check + executor (mechanized + human):** mechanized — `python -m pytest tests/test_web.py tests/test_cli.py -v`.
  Human — a presentable run-through by Brian Nelson following `wiki/Autonomy-Workbench.md`, pass
  condition: the 15-minute order in that runbook can be followed without a terminal on screen and
  without consulting this repository.
- **Pass condition (mechanized):** every test passes, notably the full loop over HTTP
  (`test_the_full_loop_through_http`), the no-markup pin
  (`test_the_page_never_inserts_untrusted_text_as_markup`), and the no-500 pins.
- **Pass condition (human):** the page was served and probed successfully on 2026-11-23 during this
  spec — `GET /` returned 200, `/api/health` returned the audit path, and with the lab down
  `/api/overview` returned 200 with `ok: false` and a readable reason rather than a 500. The visual
  walkthrough by the presenter remains the outstanding human check, disclosed rather than claimed.

---

## Security

- **Triggers matched:** external input rendering, path handling, bind address, credential exposure.
- **Security acceptance criteria:**
  - [x] Bound to `127.0.0.1` by default; a non-loopback bind prints an explicit warning.
  - [x] Static serving uses a fixed set of names, never a path from the request.
  - [x] All device and audit text rendered with `textContent`; pinned by a test.
  - [x] No secret is returned by any endpoint; `/api/health` reports only the audit path.
  - [x] The approver requirement is enforced at the HTTP boundary as well as in the gate.

---

## Deviations

- **Persona gap**, as SPEC-002.
- **No visual verification.** No browser-automation tool is available on this seat, so the page was
  verified by serving it and probing it over HTTP, and by pinning its markup rules in tests. It has not
  been screenshotted or clicked through. Stated plainly rather than implied as done.
- **`check`, `reset` and `overview` were initially untestable** because they called `Lab.real()`
  internally. Their own tests found it; each now accepts an injected lab.

---

## Completion Record

All ten acceptance criteria are met, with the visual walkthrough disclosed as outstanding. 24 web tests
and 14 CLI tests. Three real defects were found by the tests: a FastAPI annotation-resolution failure
that silently made `request` a query parameter, `check` bypassing the injected lab seam, and
`reject_change` ignoring its audit log.
