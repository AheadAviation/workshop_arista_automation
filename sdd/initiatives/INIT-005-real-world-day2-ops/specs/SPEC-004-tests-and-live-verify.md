# SPEC-004: Automated Tests & Live Verification

**Initiative:** `INIT-005`   **Domain:** `qa`   **Complexity:** `simple`
**Autonomy:** `auto_tests`   **Reversibility:** `branch`   **Security Review:** `optional`

## Goal

Update `tests/test_operations.py` for the 6 real-world operations, add unit tests for `day2_ops.py`, run `pytest`, restart NetBox, and verify `/plugins/ops/actions/` live against NetBox + the 6 running cEOS switches.

## Acceptance Criteria

- **AC1 (`REQ-004`):** `tests/test_operations.py` passes with full coverage of all 6 operations, input validation, and live resolver fallbacks.
- **AC2 (`REQ-004`):** `tests/test_day2_ops.py` verifies preview, NetBox write-back payloads, EOS CLI generation, approver gating, and audit logging.
- **AC3 (`REQ-003`, `REQ-004`):** Live check on `http://localhost:8888/plugins/ops/actions/` confirms HTTP 200, zero leaked comment text, live NetBox choices in `/plugins/ops/actions/catalogue.json`, and working preview + apply against the running lab.
