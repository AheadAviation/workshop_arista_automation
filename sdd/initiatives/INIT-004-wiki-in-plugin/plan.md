# Approved Plan — INIT-004-wiki-in-plugin

**Approved by:** Brian Nelson (via `AskQuestion`: "Yes, build it") on 2026-11-23
**Note:** `plan_approval.py` is not installed on this seat; approval recorded manually.

---

# Here is what I plan to do

## What I'll build

1. **Fixed workshop runner actions** (asked for by Brian Nelson, 2026-11-23): Extend the existing `lab-runner` sidecar with fixed, locked-down actions to reset generated lab files, generate the ContainerLab topology from NetBox, render the six Arista device configs with Ansible, deploy the generated wiki topology, push full configs to all six switches, save and restore switch backups, and run the full or quick Ansible validation suite — without giving NetBox direct Docker access.
2. **Workshop status, diff, and live fabric checks** (asked for by Brian Nelson, 2026-11-23): Add a workshop engine inside `plugins/netbox_ops` that checks live completion status for every Quick Start step, reads the generated topology and config files for inspection, compares live switch running configs against newly generated configs, and runs the Step 8 device checks (BGP peers, MLAG status, VXLAN/VNI, EVPN, tenant VLANs and VRF CORE, and cross-fabric pings) directly over eAPI.
3. **Guided Workshop tab and Day 2 controls in NetBox** (asked for by Brian Nelson, 2026-11-23): Add a new **Workshop** tab under **Network Ops** that walks through Quick Start Steps 1–9 from top to bottom with live step status, one-click buttons, and inline output viewers, followed by a Day 2 Operations section (direct links to edit Devices and Config Contexts in NetBox, then Regenerate, Diff, Backup, Push, Validate, and Rollback).
4. **Automated tests and wiki pointers** (asked for by Brian Nelson, 2026-11-23): Add unit tests for the new runner actions, workshop engine, eAPI check parsers, config diff, and Django views/permissions, and add a short note to `wiki/Quick-Start.md` and `wiki/Day-2-Operations.md` pointing to the in-NetBox Workshop tab.

## What I'll reuse

- `docker/lab-runner/runner.py` and `docker-compose.lab-from-container.yml` — the existing sidecar pattern and host-path mount override so NetBox stays isolated from the Docker socket.
- `scripts/full_import.py`, `scripts/generate_clab_topology_docker.py`, `playbooks/generate_configs.yml`, and `playbooks/test_all.yml` — the repository's own scripts and playbooks so the plugin runs the exact same pipeline as the wiki.
- `plugins/netbox_ops/netbox_ops/devices_admin.py`, `eapi_exec.py`, `live_config.py`, `labs.py`, and `access.py` — the existing import helper, eAPI client, running-config reader, container status reader, and NetBox permission mixins.

## How I'll check it works

1. Run unit tests against the new `lab-runner` endpoints to verify fixed command construction, refusal of unknown actions, and clean error reporting when a command fails.
2. Run unit tests against the workshop engine to verify step-readiness detection, config diff generation, and parsing of BGP, MLAG, VXLAN/EVPN, VLAN/VRF, and ping outputs.
3. Run Django view and permission tests on the new Workshop tab and every POST action to verify read-only users cannot trigger changes, CSRF is enforced, and each action renders its result without 500 errors.
4. Run the existing `pytest` test suite alongside the new tests to confirm nothing in `plugins/netbox_ops` or `workbench` regressed.

## What I won't do

- **Deletes: nothing** — all existing Network Ops tabs, the existing `lab-runner` `/deploy` and `/destroy` endpoints, and the CLI scripts remain untouched so existing demos keep working.
- Workshop 2 / Plants Demo and Dual-DC L3LS tracks stay out of scope in this pass.
- `/var/run/docker.sock` is not mounted into NetBox and no arbitrary shell-command runner is added.
- Starting NetBox (`docker compose up -d`) and importing `ceos:4.32.0F` remain one-time host prerequisites.

## What you'll have at the end

Once NetBox is running, you can open **Network Ops → Workshop** in the browser and run the entire `Quick Start` (Steps 1–9) and `Day 2 Operations` wiki from a blank fabric to a verified 6-switch EVPN/VXLAN lab — all with clicks inside NetBox, no CLI or AI agents required.
