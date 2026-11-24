# SPEC-002: Source-of-Truth-First Day 2 Planner, NetBox Write-Back & eAPI Executor

**Initiative:** `INIT-005`   **Domain:** `backend`   **Complexity:** `medium`
**Autonomy:** `auto_tests`   **Reversibility:** `branch`   **Security Review:** `required` (writes to NetBox API and pushes EOS config over eAPI)

## Goal

Add `plugins/netbox_ops/netbox_ops/day2_ops.py` and wire `engine.py` (`plan_operation`, `apply_operation`) so every one of the 6 operations supports:
1. **Preview** (read-only): builds a staged plan with `change_id`, exact planned NetBox update summary, and exact per-switch EOS CLI commands + diff against live running state.
2. **Approve & Apply** (named approver required): updates NetBox first (IPAM VLAN / `tenant-core` config context / `dcim.interface` / `fabric-global` config context), pushes the exact EOS commands over `eapi_exec._eapi` with `write memory`, reads back live switch state to verify, and appends an audit record.

## Acceptance Criteria

- **AC1 (`REQ-002`):** Preview never mutates NetBox or any switch and returns a staged `change_id`, `netbox_changes`, and per-device `devices` list with `commands` and `diff`.
- **AC2 (`REQ-002`):** Apply refuses missing approver or unknown/expired `change_id`, updates NetBox first, applies EOS commands via eAPI, verifies live state, and writes an audit entry.
- **AC3 (`REQ-001`, `REQ-002`):** `provision_mlag_portchannel` applies real `mtu <mtu>`, `mlag <id>`, `channel-group <id> mode active`, and `switchport trunk allowed vlan` on both MLAG pair switches.
- **AC4 (`REQ-001`, `REQ-002`):** `configure_port` applies real `description`, `mtu`, `switchport mode access|trunk`, VLAN assignment, and `shutdown`/`no shutdown` on the selected switch + interface.
