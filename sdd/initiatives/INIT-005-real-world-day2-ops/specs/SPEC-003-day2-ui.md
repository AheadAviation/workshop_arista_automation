# SPEC-003: Clean Day-Two Actions UI

**Initiative:** `INIT-005`   **Domain:** `frontend`   **Complexity:** `simple`
**Autonomy:** `auto_tests`   **Reversibility:** `branch`   **Security Review:** `optional`

## Goal

Fix the leaked multi-line `{# ... #}` comment in `plugins/netbox_ops/netbox_ops/templates/netbox_ops/_actions.html` and update the interactive JS/HTML to cleanly render all 6 real-world operations, dependent fields (e.g., selecting a leaf switch filters its interfaces; selecting port mode shows access VLAN vs. trunk VLANs), NetBox + EOS CLI diff previews, named approver input, and post-apply verification readouts.

## Acceptance Criteria

- **AC1 (`REQ-003`):** Zero `{#` comment text appears in the rendered HTML of `/plugins/ops/actions/`.
- **AC2 (`REQ-003`):** All 6 operation cards render dynamically from `/plugins/ops/actions/catalogue.json`, with live NetBox choices populated in dropdowns.
- **AC3 (`REQ-003`):** Preview displays both "NetBox Source-of-Truth update" and per-switch EOS CLI commands/diff; Approve & Apply requires an approver name and shows per-switch verification results.
