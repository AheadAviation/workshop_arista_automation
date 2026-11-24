# SPEC-001: Real-World EVPN/VXLAN Day 2 Catalogue & Live NetBox Resolver

**Initiative:** `INIT-005`   **Domain:** `backend`   **Complexity:** `medium`
**Autonomy:** `auto_tests`   **Reversibility:** `branch`   **Security Review:** `optional`

## Goal

Update `workbench/operations.py` and `workbench/operations_resolve.py` to define the six real-world EVPN/VXLAN Day 2 operations and populate every dynamic field from live NetBox objects (IPAM VLANs + `tenant-core` config-context fallback, `dcim.device` leaves, `dcim.interface` server-facing ports per leaf, MLAG pairs, and `fabric-global` NTP/DNS entries).

## Acceptance Criteria

- **AC1 (`REQ-001`):** `OPERATIONS` defines exactly 6 operations: `provision_vlan`, `configure_port`, `provision_mlag_portchannel`, `update_fabric_services`, `retire_vlan`, `remediate_drift`.
- **AC2 (`REQ-001`):** Live VLAN fetcher merges `ipam.vlan` with `tenant-core` config-context `l2_segments` so dropdowns are populated even immediately after Day-Zero `full_import.py`.
- **AC3 (`REQ-001`):** Live device & interface fetchers return real leaf switches (`leaf-01`..`leaf-04`) and server-facing Ethernet interfaces (`Ethernet3`–`Ethernet8`, `eth3`–`eth8`) from NetBox.
- **AC4 (`REQ-001`):** `resolve()` validates every input strictly (VLAN ranges 2–4094, valid IPv4/CIDR if SVI provided, valid device/interface/pair, valid MTU `1500`/`9214`, valid IPv4/hostname for NTP/DNS) and raises `ResolveError` on invalid input.
