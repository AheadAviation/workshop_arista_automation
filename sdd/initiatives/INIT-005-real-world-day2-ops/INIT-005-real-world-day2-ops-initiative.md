# Initiative: Real-World Arista EVPN/VXLAN Day 2 Operations

**Initiative ID:** INIT-005   **Status:** completed
**Slug:** INIT-005-real-world-day2-ops
**Discipline Profile:** `software`   **Created:** 2026-11-24   **Owner:** Brian Nelson
**Branch:** `main` (deliberately not a worktree — NetBox mounts the main checkout directly for live plugin testing)

---

## Summary

Replace the four placeholder hospital-demo cards on **Network Ops → Day-two actions** (and fix the leaked `{# ... #}` template comment) with six genuine, Source-of-Truth-first Arista EVPN/VXLAN Day 2 operations wired to live NetBox devices, interfaces, IPAM/config-contexts, and live cEOS switches over eAPI.

**Deletes / Replaces:** Replaces `deliver_service`, `add_storage_array`, `change_jack`, and `retire_service` in `workbench/operations.py` with the six real-world EVPN/VXLAN operations; updates `tests/test_operations.py` to match.

## Requirements

| ID      | Requirement | asked_by |
| ------- | ----------- | -------- |
| REQ-001 | Replace placeholder Day 2 cards with 6 real-world Arista EVPN/VXLAN operations backed by live NetBox data. | Brian Nelson, 2026-11-24 |
| REQ-002 | Source-of-Truth first: Preview shows NetBox change + per-switch EOS CLI diff; Approve & Apply updates NetBox first, applies via eAPI, verifies live state, and records audit. | Brian Nelson, 2026-11-24 |
| REQ-003 | Fix leaked `{# ... #}` comment in `_actions.html` and render clean, live-populated dynamic inputs (including device → interface cascading). | Brian Nelson, 2026-11-24 |
| REQ-004 | Automated unit tests + live verification against running NetBox and 6-switch cEOS lab. | Brian Nelson, 2026-11-24 |

## Specs

| Spec | Domain | Summary | Depends on |
| ---- | ------ | ------- | ---------- |
| `SPEC-001` | backend | Six real-world EVPN/VXLAN Day 2 operations catalogue & live NetBox resolver (`workbench/operations.py`, `workbench/operations_resolve.py`) | — |
| `SPEC-002` | backend | Source-of-Truth-first planner, NetBox write-back, eAPI executor, verifier & audit (`plugins/netbox_ops/netbox_ops/day2_ops.py` + `engine.py`) | `SPEC-001` |
| `SPEC-003` | frontend | Clean Day-two actions UI (`_actions.html` + `views.py`) fixing leaked comment and supporting all 6 operations | `SPEC-002` |
| `SPEC-004` | qa | Updated `tests/test_operations.py` + new Day-2 engine tests + live verification | `SPEC-001`, `SPEC-002`, `SPEC-003` |
