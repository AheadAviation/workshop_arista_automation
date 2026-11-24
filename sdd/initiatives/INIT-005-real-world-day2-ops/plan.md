# Approved Plan — INIT-005-real-world-day2-ops

**Approved by:** Brian Nelson (via `AskQuestion`: "Yes, build it") on 2026-11-24
**Note:** `plan_approval.py` is not installed on this seat; approval recorded manually.

---

# Here is what I plan to do

## What I'll build

1. **Real-world Arista EVPN/VXLAN Day 2 operation catalogue and live NetBox resolver** (asked for by Brian Nelson, 2026-11-24): Replace the four old placeholder cards with six real-world Day 2 operations backed by live NetBox devices, interfaces, MLAG pairs, config contexts, and IPAM VLANs (reading VLANs from both NetBox IPAM and the `tenant-core` config context):
   - Provision Tenant VLAN / VNI
   - Configure / Reassign Switch Port
   - Provision Dual-Homed MLAG Port-Channel
   - Update Fabric Services (NTP / DNS)
   - Retire Tenant VLAN / VNI
   - Detect & Remediate Config Drift
2. **Source-of-Truth-first preview, NetBox write-back, eAPI apply, and verification engine** (asked for by Brian Nelson, 2026-11-24): Preview shows planned NetBox updates and exact per-switch EOS CLI diffs; Approve and apply updates NetBox first, pushes EOS commands over eAPI with `write memory`, reads back live state, and logs to the audit trail.
3. **Clean Day-two actions UI in NetBox** (asked for by Brian Nelson, 2026-11-24): Fix the leaked `{# ... #}` comment in `_actions.html`, render the six real-world operation cards with live-populated dropdowns (including dependent interface dropdowns when a leaf switch is chosen), NetBox + CLI diff previews, named-approver gating, and post-apply verification readouts.
4. **Automated tests and live verification** (asked for by Brian Nelson, 2026-11-24): Update `tests/test_operations.py`, run `pytest`, restart NetBox, and verify live against the running 6-switch cEOS lab.
