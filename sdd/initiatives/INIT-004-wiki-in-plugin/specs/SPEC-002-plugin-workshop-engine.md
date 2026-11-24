# SPEC-002: Plugin Workshop Engine & Live Fabric Verification

**Initiative:** `INIT-004`   **Domain:** `backend`
**Complexity:** `medium` — new `workshop.py` module + `engine.py` adapter functions + eAPI parsers.
**Autonomy:** `auto_tests`   **Reversibility:** `branch`   **Security Review:** `optional`

## Goal

Add `plugins/netbox_ops/netbox_ops/workshop.py` and expose its functions through `engine.py` so the UI can inspect live status for every Quick Start step, read generated topology/config files, diff live running configs against generated configs, clear NetBox fabric objects for a clean slate, run Step 8 live checks over eAPI, and call the scoped `lab-runner` workshop endpoints.

## Acceptance Criteria

- **AC1 (`REQ-001`, `REQ-003`):** `workshop_status()` returns live status for Quick Start Steps 1–9 (NetBox devices count, topology file presence/size, rendered configs list, running cEOS container count, and whether configs are pushed/converged).
- **AC2 (`REQ-001`, `REQ-002`):** `read_topology()` and `read_config(device)` read the mounted files safely (allowlisted device names only, no path traversal) and return structured content for inline viewing.
- **AC3 (`REQ-002`):** `config_diffs()` compares each switch's live running config (via `live_config.running_config`) against `output/configs/<dev>.cfg` using unified diff, returning per-device diff text and changed-line counts.
- **AC4 (`REQ-001`):** `verify_fabric()` runs Step 8 checks over `eapi_exec._eapi` across the 6 switches: BGP summary (`spine-01`/`spine-02` established peer counts), MLAG status (`leaf-01`..`leaf-04` active/connected), VXLAN/EVPN (`show interfaces Vxlan1`, `show vxlan vni`, `show bgp evpn summary`), Tenant VLANs & VRF CORE (`show vlan`, `show ip route vrf CORE`), and cross-fabric loopback pings (`leaf-01` & `leaf-03` → `spine-01` & `spine-02`).
- **AC5 (`REQ-001`, `REQ-002`):** `clean_slate(clear_netbox=False)` calls `lab-runner /workshop/clean` and, when `clear_netbox=True`, removes the 6 fabric devices/cables/config contexts from NetBox via its REST API so Step 3 (`full_import.py`) can be run from an empty NetBox.
- **AC6 (`REQ-001`, `REQ-002`):** Thin wrappers call `lab-runner` for `generate_topology()`, `generate_configs()`, `deploy_workshop_lab()`, `destroy_workshop_lab()`, `push_full_configs()`, `backup_configs()`, `rollback_configs()`, and `run_validation(quick=False)`, returning page-safe dicts without raising on network errors.

## Verification Strategy

Unit tests with mocked eAPI, HTTP, and temp filesystem fixtures verify every parser, path-traversal guard, diff output, and error path.
