# Initiative: A NetBox Plugin That Runs the Whole Day-Two Story

**Initiative ID:** INIT-002   **Status:** on_track
**Slug:** INIT-002-netbox-ops-plugin
**Discipline Profile:** `software`   **Created:** 2026-11-23   **Owner:** Brian Nelson (PTC lead, network automation)
**Branch:** `netbox-ops-plugin` (deliberately not a worktree — the plugin must be built into the NetBox image
and exercised against the running lab, which the main checkout already has)

---

## Summary

A NetBox plugin that puts the whole day-two operation on one page inside NetBox: the devices and whether
they match intent, the container labs, the change flow with its approval and audit, the AWX jobs, and the
ITSM tickets. The customer already trusts NetBox as the source of truth; this makes it the place the work
actually happens, instead of a separate console beside it.

The engine is reused, not rebuilt. `workbench/` already does the drift comparison, the preview-without-apply
executor, the approval gate and the audit record, and it is verified against live switches with 482 tests.
The plugin is a new surface on that engine.

**Deletes:** nothing. The workbench keeps its own page and tests; this is an additional surface. If the
plugin proves the better surface we retire the other deliberately, in a later initiative, not by accident
here.

**Requirements:**

| ID      | Requirement         | asked_by                          |
| ------- | ------------------- | --------------------------------- |
| REQ-001 | One plugin inside NetBox that manages devices, labs and day-two operations, rather than a separate console. | Brian Nelson, 2026-11-23: "I want a plugin to manage it all" |
| REQ-002 | Load in and show devices, and check the container labs from the plugin. | Brian Nelson, 2026-11-23: "load in devices, check the container labs" |
| REQ-003 | Show AWX — real AWX containers, not a mock — including its job templates and runs. | Brian Nelson, 2026-11-23: "show the awx" (chose "real AWX containers") |
| REQ-004 | Show the ITSM ticket — real Zammad containers — and take the approval from the ticket. | Brian Nelson, 2026-11-23: "show the itsm ticket" (chose "real Zammad containers") |
| REQ-005 | All of the day-two operations reachable from the plugin. | Brian Nelson, 2026-11-23: "all of the day two opps" |
| REQ-006 | Build the whole thing, accepting it will not be ready for the next morning's meeting. | Brian Nelson, 2026-11-23: "everything" / "Build all five waves straight through" |
| REQ-007 | NetBox must not hold unrestricted control of the Docker host, because the audience includes a hospital CISO. | Derived from the audience in the 2026-11-23 briefing call (CISO in the room, HIPAA, audit-heavy) |

---

## Prior Art

```
prior-art:begin
status: NOT RUN — disclosed gap
reason: The kit helper `<KIT>/scripts/dev/prior_art.py` is not present on this seat
        (`C:\Users\BrianNelson\.claude\scripts\dev\` does not exist), so the standard
        prior-art search could not execute. Skipped entirely rather than run against a
        substitute.
what was done instead: A manual survey of this repository's own existing plugin and of the
        workbench engine it will reuse. Recorded below.
what this does not prove: No organisation-wide or marketplace search was performed. A
        NetBox plugin may already exist that does part of this.
prior-art:end
```

Manual survey:

- `plugins/netbox_ztp_portal/` — a working NetBox plugin in this repo on NetBox 4.1.11. Its
  structure (plain Django views in `api/`, `conf.py` rather than `config.py`, `navigation.py`,
  a `client.py` talking to NetBox's own REST API) is the pattern this plugin follows.
- `workbench/` — the engine being reused: `contracts.py` (shapes), `drift.py` (comparison),
  `eos_executor.py` (preview/apply), `gate.py` (approval + audit), `device_reader.py`,
  `demo_lab.py` (lab bring-up). ~3,000 lines, 482 tests, verified against live cEOS switches.
- `sdd/initiatives/INIT-001-autonomy-demo/` — the initiative that produced the engine.

---

## Facts established before planning (not assumed)

| Fact | How established |
| --- | --- |
| NetBox is **4.1.11 Community** | `/opt/netbox/netbox/release.yaml` inside the running container |
| AWX 24.6.1 image pulls and executes | `docker pull` + `docker run --entrypoint /bin/sh` |
| AWX execution environment 24.6.1 pulls | `docker pull quay.io/ansible/awx-ee:24.6.1` |
| Zammad image pulls and executes | `docker pull` + `docker run` (Ruby 3.4.13 present) |
| `pg_trgm` available for Zammad | listed in `postgres:15-alpine` extension directory |
| Host RAM 63.5 GB total, 14.7 GB free at planning time | `Win32_OperatingSystem` |
| Disk 54.5 GB free | `Get-PSDrive C` |

---

## Specs

| Spec     | Domain         | Title   | Complexity | Review class | Gates | Blocked by | Status  |
| -------- | -------------- | ------- | ---------- | ------------ | ----- | ---------- | ------- |
| SPEC-001 | docs           | Plugin contract and the engine adapter | medium | combined | — | None | ready |
| SPEC-002 | infrastructure | Scoped Docker access and the lab control API | high | full | dependency_add | SPEC-001 | ready |
| SPEC-003 | service        | Ops plugin package: config, urls, navigation, views | medium | full | — | SPEC-001 | ready |
| SPEC-004 | frontend       | The Ops page: devices, labs, changes, audit | high | full | — | SPEC-002, SPEC-003 | ready |
| SPEC-005 | infrastructure | Containerised AWX with bootstrap | high | full | dependency_add | SPEC-001 | ready |
| SPEC-006 | infrastructure | Containerised Zammad with bootstrap | high | full | dependency_add | SPEC-001 | ready |
| SPEC-007 | api            | AWX and ITSM adapters, with the change↔ticket loop | high | full | — | SPEC-004, SPEC-005, SPEC-006 | ready |
| SPEC-008 | data           | Audit and change records as NetBox models | medium | full | db_migration | SPEC-003 | ready |
| SPEC-009 | discovery      | Lifecycle view and the pre-demo rehearsal | medium | combined | — | SPEC-007, SPEC-008 | ready |

**Dependency edges:** SPEC-001 → {SPEC-002, SPEC-003, SPEC-005, SPEC-006}; SPEC-003 → {SPEC-004, SPEC-008};
{SPEC-002, SPEC-003} → SPEC-004; {SPEC-004, SPEC-005, SPEC-006} → SPEC-007; {SPEC-007, SPEC-008} → SPEC-009

**Waves (the owner's "all five waves" answer, 2026-11-23):**

1. SPEC-001, SPEC-003 — plugin skeleton and the engine inside it
2. SPEC-002, SPEC-004 — lab control, visible and drivable
3. SPEC-008, and the day-two flow through SPEC-004 — a working demo
4. SPEC-005, SPEC-006, SPEC-007 — AWX and Zammad, real, with integrations
5. SPEC-009 — lifecycle, rehearsal, runbook

> Security review runs at completion (`complete-initiative` Phase 3.5), not as a spec.

---

## Deviations

- **`prior_art.py` and `plan_approval.py` could not run** — the kit's `scripts/dev/` helpers are not
  installed on this seat. Prior art is disclosed as NOT RUN above; the plan approval is recorded by hand in
  `plan.md` from the actual replies.
- **`dependency_add` gates.** SPEC-002, SPEC-005 and SPEC-006 each add third-party containers and images.
  Each carries the gate action `dependency_add` and requires approval before those containers are started;
  the code and compose files can be written first.

---

## Success Criteria

- [ ] A NetBox menu item opens a page showing devices, labs, changes, AWX and tickets.
- [ ] The page shows each device's intended vs actual state, read from the real devices.
- [ ] Container labs are visible and can be deployed/destroyed from the page.
- [ ] A day-two VLAN change runs end to end: preview, ticket, approval in the ticket, apply, verify, audit.
- [ ] Real AWX is running with credentials, inventory and job templates, and the page shows a real job run.
- [ ] Real Zammad is running, and the ticket a change opens is real and can be approved there.
- [ ] Every applied change is a queryable NetBox record with its approver and before/after.
- [ ] NetBox can reach Docker only through a scoped proxy — no raw socket mount.
- [ ] `python -m pytest tests -q` passes with the new tests included.
