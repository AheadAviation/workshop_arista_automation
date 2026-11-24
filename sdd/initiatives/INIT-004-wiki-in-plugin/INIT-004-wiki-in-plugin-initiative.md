# Initiative: Run the Core Workshop & Day-2 Wiki End-to-End from the NetBox Plugin

**Initiative ID:** INIT-004   **Status:** completed
**Slug:** INIT-004-wiki-in-plugin
**Discipline Profile:** `software`   **Created:** 2026-11-23   **Owner:** Brian Nelson
**Branch:** `main` (deliberately not a worktree — NetBox mounts the main checkout directly for live plugin testing, matching `INIT-002`)

---

## Summary

Make the core workshop wiki (`wiki/Quick-Start.md` Steps 1–9 and `wiki/Day-2-Operations.md`) runnable from scratch inside the `netbox_ops` plugin without using the CLI or AI agents, while preserving the security boundary established in `INIT-002` (NetBox never mounts `/var/run/docker.sock`).

**Deletes:** nothing — all existing Network Ops tabs, existing `lab-runner` `/deploy` and `/destroy` routes, and CLI scripts stay intact so existing demos continue to work.

## Requirements

| ID      | Requirement | asked_by |
| ------- | ----------- | -------- |
| REQ-001 | Run `wiki/Quick-Start.md` Steps 1–9 from scratch inside `netbox_ops` without CLI or AI agents. | Brian Nelson, 2026-11-23: "I wanted the plugin to go through the wiki ... right now I don't think I can do this wiki from scratch without using the cli or AI agents." |
| REQ-002 | Run `wiki/Day-2-Operations.md` (edit in NetBox, regenerate configs, diff against running, backup, push, validate, rollback) inside `netbox_ops`. | Brian Nelson, 2026-11-23: selected "Core Workshop (Quick Start Steps 1–9 + Day 2 Operations)" |
| REQ-003 | Present the workflow as a guided top-to-bottom **Workshop** tab in `Network Ops` with live step status, one-click buttons, and inline output viewers, while keeping existing tabs. | Brian Nelson, 2026-11-23: selected "Guided 'Workshop' tab" |
| REQ-004 | Keep NetBox isolated from `/var/run/docker.sock`; all host Docker/ContainerLab/Ansible actions go through fixed, parameter-free verbs on `lab-runner`. | Inherited from `INIT-002` REQ-007 |

## Prior Art

```
prior-art:begin
status: NOT RUN — disclosed gap
reason: `<KIT>/scripts/dev/prior_art.py` is not installed on this seat.
what was done instead: Manual survey of `docker/lab-runner/runner.py`, `docker-compose.lab-from-container.yml`,
        `plugins/netbox_ops/`, `scripts/`, and `playbooks/`.
what this does not prove: No organisation-wide or external search was performed.
prior-art:end
```

## Specs

| Spec | Domain | Summary | Depends on |
| ---- | ------ | ------- | ---------- |
| `SPEC-001` | infra | Scoped workshop runner endpoints in `docker/lab-runner/runner.py` and `docker-compose.lab-from-container.yml` | — |
| `SPEC-002` | backend | Plugin workshop engine, step readiness, config diff, and live eAPI fabric checks (`workshop.py` + `engine.py`) | `SPEC-001` |
| `SPEC-003` | frontend | Guided **Workshop** tab + Day-2 UI in `plugins/netbox_ops/` (`navigation.py`, `urls.py`, `views.py`, templates) | `SPEC-002` |
| `SPEC-004` | qa | Unit tests for runner, workshop engine, eAPI parsers, views/permissions, plus wiki pointers | `SPEC-001`, `SPEC-002`, `SPEC-003` |
