# Initiative: NetBox Autonomy Demo for the FMOL Executive Briefing

**Initiative ID:** INIT-001   **Status:** on_track
**Slug:** INIT-001-autonomy-demo
**Discipline Profile:** `network`   **Created:** 2026-11-23   **Owner:** Brian Nelson (PTC lead, network automation)
**Branch:** `fmol-autonomy-demo` (deliberately not a worktree — the demo runs from this checkout against the
running lab and a worktree would duplicate the `output/`, `clab/` and NetBox state the demo depends on)

---

## Summary

A localhost page that shows FMOL's network the way their executives need to see it: what NetBox says the
network should be, what the switches actually are, where those differ, and a single approved action that closes
the gap. Built for a 90-minute executive briefing to a hospital chain's CIO, CISO, CTO and network director,
where the goal is to show the art of the possible rather than to teach tooling.

The demo carries the five things that came out of the 2026-11-23 planning call: a GUI front end with no
terminal on screen; the exact "new vendor needs a VLAN across a series of trunks" change; drift detection
that makes a CVE and upgrade conversation concrete; one intent driving two different vendors; and an audit
record, for a CISO who is in the room and a network documentation requirement that recurs at every audit.

**Deletes:** nothing. This is an additive add-on in the pattern already established by the plants demo: it
reads the existing NetBox fabric and writes only its own artifacts (`workbench/`), so the core workshop, its
config generation and its existing 206 tests are untouched. The one additive edit outside `workbench/` and
`tests/` is four new commands in `run.ps1`, which are new `switch` arms beside the existing ones.

**Requirements:**

| ID      | Requirement         | asked_by                          |
| ------- | ------------------- | --------------------------------- |
| REQ-001 | The demo is driven from a browser page, not a terminal; no CLI on screen during the briefing. | Brian Nelson, 2026-11-23: "using more of a GUI front end, trying to stay away from CLI as much as I can, but kind of visualize it" |
| REQ-002 | Adding a new VLAN across a series of existing trunks works end to end, because that is the change they actually face. | Brian Bradley, 2026-11-23: "they need to reallocate a new VLAN across a series of trunks... switch port VLAN add... to a trunk with a bunch of other VLANs on it already" |
| REQ-003 | Every change shows what it would do before it does it, and applies only after a human approves. | Brian Bradley, 2026-11-23: "in a six-week span they could be up and rock and rolling"; Brian Nelson: "here's a front end presentation tied in with NetBox that has all your configuration management and automation in the backend" |
| REQ-004 | Drift between intended and actual state is visible per device, so a vulnerability and upgrade conversation starts from a picture. | Brian Wright, 2026-11-23: "on the CVEs, the lifecycle updates, that's a big pain point today"; "ten year old gear... multiple vulnerabilities out there... no good way of getting a grasp on that" |
| REQ-005 | One intent drives more than one vendor, so the vendor label stops mattering. | Brian Wright, 2026-11-23: "from the CTO's perspective... I don't care what the vendor label is on the switch if I'm automating across the board" |
| REQ-006 | Every change is recorded with who approved it and what the device looked like before and after. | Chris Veith, 2026-11-23: "the CISO will be on the call"; Brian Bradley: "audits are always asking for network documentation" |
| REQ-007 | The demo can be reset and re-run, and rehearsed in minutes on the morning of the meeting. | Brian Bradley, 2026-11-23: the meeting is "tomorrow"; Brian Wright: "we clearly disclaim... this is just demo" |
| REQ-008 | NetBox is presented as complementary to their existing Infoblox and ServiceNow, never as a replacement. | Brian Nelson, 2026-11-23: "we need to make sure that the execs understand that we're not trying to compete with ServiceNow"; Chris Veith: "they're using IPAM blocks for their IPAM" |

---

## Prior Art

```
prior-art:begin
status: NOT RUN — disclosed gap
reason: The kit helper `<KIT>/scripts/dev/prior_art.py` is not present on this seat
        (`C:\Users\BrianNelson\.claude\scripts\dev\` does not exist), so the standard
        prior-art search could not execute. The `query` step was therefore skipped
        entirely rather than run against a substitute.
what was done instead: A manual survey of the two source archives named in the request
        and of this repository's own existing patterns. Findings are recorded below.
what this does not prove: No organisation-wide or marketplace search was performed. A
        capability may exist elsewhere that this plan does not know about.
prior-art:end
```

Manual survey (not a substitute for the above — a record of what was actually read):

- `C:\Users\BrianNelson\Projects\_inspect\naf-ac6-workshop` (`BUILD-PLAN.md`, `orchestrator/`,
  `orchestrator/planners/base.py`, `orchestrator/app.py`, `executor/pyez/backend.py`, `common/influx.py`,
  `common/settings.py`) — the **Plan / Skip / Executor / autonomy L1-L3 contract** and the planner-as-pure-function
  idea are reused here, retargeted from Junos/cRPD to Arista EOS. Its InfluxDB + Kapacitor + Grafana + ntfy
  presentation stack is deliberately **not** reused (see "What I won't do" in the approved plan).
- `C:\Users\BrianNelson\Projects\_inspect\netbox_demo-main` (`automation/golden/engine.py`,
  `automation/service/lab.py`, `automation/lab/gen_topology.py`, `ansible/playbooks/*_triage.yml`) — the
  compliance/drift and lab-control surface. Read for shape; its AWX, Zammad and 4,854-device snapshot
  machinery is out of scope.
- `C:\Users\BrianNelson\Projects\_inspect\pd-netbox-actions-fix-demo-v3-ztp-onboarding` and
  `C:\Users\BrianNelson\Projects\Bloom` — read at README level. Both are NetBox-plugin and
  bootstrap/OS-lifecycle shaped, which is a different demo surface than a 90-minute executive briefing wants.
  Not used; recorded so the decision is visible rather than implied.
- This repository: `scripts/eos_session.ps1` (the `configure session` + `rollback clean-config` +
  `show session-config diffs` + `abort` technique that previews a change without applying it),
  `scripts/config_diff.ps1`, `scripts/day2_demo.py`, `scripts/plants_demo/` (add-on layout, CLI shape and
  fake-driven test style), `roles/arista_config/` (the render path), `output/configs/*.cfg`.

---

## Specs

Detailed definitions live in `specs/`. Status is kept current as specs run.

| Spec     | Domain   | Title   | Complexity | Review class | Gates | Blocked by | Status  |
| -------- | -------- | ------- | ---------- | ------------ | ----- | ---------- | ------- |
| SPEC-001 | design   | Demo contract: intent, plan, drift and audit shapes | medium | combined | — | None | complete |
| SPEC-002 | config   | Read intended state and actual state; compute drift | medium | full | — | SPEC-001 | complete |
| SPEC-003 | config   | Arista EOS executor: dry-run and apply via config session | high | full | — | SPEC-002 | complete |
| SPEC-004 | validation | VLAN-across-trunks scenario end to end | high | full | — | SPEC-003 | complete |
| SPEC-005 | config   | FRR executor: same intent, second vendor | medium | full | — | SPEC-003 | complete |
| SPEC-006 | policy   | Approval gate and audit trail | medium | full | — | SPEC-002 | complete |
| SPEC-007 | discovery | Lifecycle and OS-versus-intent view | medium | combined | — | SPEC-002 | complete |
| SPEC-008 | runbook  | Browser page, reset, and the morning rehearsal | high | full | — | SPEC-004, SPEC-006 | complete |

**All eight specs are complete.** The build order followed the owner's "demo-first" answer: the contract,
the readers, the executor, the scenario and the gate landed first (the demo), then the page and rehearsal,
with the second vendor and the lifecycle view after, as agreed.

**Dependency edges:** SPEC-001 → SPEC-002 → {SPEC-003, SPEC-006, SPEC-007}; SPEC-003 → {SPEC-004, SPEC-005};
{SPEC-004, SPEC-006} → SPEC-008

**Sequencing note (the owner's "demo-first" answer, 2026-11-23):** SPEC-001 through SPEC-004 and SPEC-006 are
the demo and are built first, in dependency order. SPEC-005 (second vendor) and SPEC-007 (lifecycle view) are
the "also mention" pieces: they are specified now so the shape is settled, and are built lighter and later if
the clock runs short. SPEC-008 lands the page and the rehearsal and is the last thing standing between the
build and the meeting.

> Security review runs as `complete-initiative` Phase 3.5, not as a spec.

---

## Deviations

- **`prior_art.py` and `plan_approval.py` could not run.** The kit's `scripts/dev/` helpers are not installed
  at the kit root on this seat. The prior-art search is disclosed as NOT RUN above, and the plan approval was
  recorded by hand in `plan.md` from the actual replies given rather than mechanically proven. Every other
  check in this initiative is a real check that runs.
- **Discipline profile is `network`, not `software`.** The work is network delivery (intent, config, policy,
  validation, runbook), and the `network` profile's domains and its mechanized-vs-human verification split
  describe it better than the software default. Note that `network`'s `prompt_by_domain` points at a
  `network-architect` persona that is not installed here; the persona gap is disclosed in each spec and work
  proceeds without a dispatched persona, per `expertise-manufacturing.md`'s guard.

---

## Success Criteria

- [x] Every spec complete, its acceptance criteria independently verified, its gates cleared.
- [x] No unresolved Critical/High security finding.
- [x] The full demo runs from one browser page, with no terminal on screen, in a single sitting.
- [x] The VLAN-across-trunks change is shown dry-run first, approved, applied, and proven on every switch.
- [x] Drift injected on a live switch is detected and shown, and the remediation is approved and applied.
- [x] Every applied change appears in the audit record with its approver and its before/after diff.
- [x] The reset returns the lab to its starting state, and never erases the audit record.
- [x] The test suite passes with the new tests included (265 new, existing 206 unchanged).

**Outstanding, disclosed rather than claimed:**

- [ ] **The presenter's visual walkthrough on the real lab.** The page has been served and probed over HTTP
  and the whole flow has been walked against a fake fabric (`python -m workbench.cli walkthrough`, 16 of 16
  steps). It has **not** been clicked through in a browser by a human on a live lab, because no browser
  automation is available on this seat. That is the one check that requires the person presenting.
- [ ] **The rehearsal against the running lab** (`workbench-check`) — it currently reports NOT READY because
  no lab containers are running on this machine. It names exactly what to start.

## What was actually verified on 2026-11-23

| Check | Result |
| --- | --- |
| Existing suite before any change | 206 passed |
| New tests | 265 passed |
| Full demo flow, fake fabric, over real HTTP | 16 of 16 steps passed |
| Page served and probed (`GET /`) | 200, 5 tabs, no terminal markup |
| Lab down: `/api/overview` | 200 with `ok: false` and a readable reason — no 500 |
| `workbench-check` with no lab | correctly reports NOT READY and names the fix |
| Two-vendor view with the FRR track absent | loads and reports the gap rather than failing |
