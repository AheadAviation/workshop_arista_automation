# SPEC-004: Automated Tests & Wiki Pointers

**Initiative:** `INIT-004`   **Domain:** `qa`
**Complexity:** `simple` — pytest suites + small doc pointers in two wiki pages.
**Autonomy:** `auto_tests`   **Reversibility:** `branch`   **Security Review:** `optional`

## Goal

Add comprehensive unit tests for `SPEC-001`..`SPEC-003`, run the full `pytest` suite to verify zero regressions, and add concise pointers in `wiki/Quick-Start.md` and `wiki/Day-2-Operations.md` noting that the workflow can be run from NetBox → **Network Ops → Workshop**.

## Acceptance Criteria

- **AC1 (`REQ-001`..`REQ-004`):** `tests/test_workshop_runner.py` verifies all new `lab-runner` endpoints, fixed commands, device allowlist, and 404/error handling.
- **AC2 (`REQ-001`..`REQ-003`):** `tests/test_workshop_plugin.py` verifies `workshop.py` step status, safe file reading, config diffing, BGP/MLAG/VXLAN/EVPN/VLAN/ping parsers, and view/permission wiring.
- **AC3 (`REQ-001`..`REQ-003`):** Full `pytest` run passes with 0 failures.
- **AC4 (`REQ-001`, `REQ-002`):** `wiki/Quick-Start.md` and `wiki/Day-2-Operations.md` include a short callout pointing to **Network Ops → Workshop** in NetBox.

## Verification Strategy

Run `pytest` across the repository and confirm all new and existing tests pass.
