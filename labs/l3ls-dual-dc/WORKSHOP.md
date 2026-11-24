# Dual-DC L3LS track

This directory is the Arista AVD dual data center lab, vendored into the NetBox workshop as a **separate track**. It does not replace the NetBox source-of-truth flow (`.\run.ps1 import` → `generate` → `deploy` → `apply`).

## What each track owns

| | NetBox workshop (repo root) | This track |
| --- | --- | --- |
| Source of truth | NetBox, loaded from `netbox_data/fabric_topology.yml` | AVD group_vars under `sites/` and `global_vars/` |
| Config generation | `roles/arista_config` Jinja templates | `arista.avd.eos_designs` + `eos_cli_config_gen` |
| Fabric | One site, 2 spines, 4 leaves | Two sites, borders, EVPN gateway, FRR DCI |
| How you run it | `.\run.ps1` | `.\run.ps1 dualdc ...` from the repo root |

The workshop container already installs `arista.avd`. Generation runs inside that container. Device push follows the workshop pattern: `docker exec` into the cEOS nodes from the host, because the Ansible container is not attached to the lab management network. FRR is copied in and reloaded the same way. The playbooks `deploy.yml` and `deploy-frr.yml` remain for a host that can reach the lab directly (the original Makefile flow).

## Run it

From the workshop root, after `docker compose build ansible` at least once:

```powershell
.\run.ps1 dualdc build     # render both sites and frr-core1
.\run.ps1 dualdc start     # containerlab: 20 cEOS + 1 FRR
.\run.ps1 dualdc apply     # push rendered EOS configs, then FRR
.\run.ps1 dualdc stop
```

`dualdc build` renders the **checked-in** data model. In that model the tenant SVIs, host ports, DCI `l3_edge` links, and EVPN gateways are commented out on purpose — that is the Day 0 starting point. Follow `day0.md` in this directory to uncomment them, then build and apply again.

The cEOS image in `clab/topology.clab.yml` is `arista/ceos:latest`. This workshop's other lab imports `ceos:4.32.0F`. Tag or edit the topology kind image so both labs use the image you actually have.

Do not run this lab and the 6-node NetBox fabric at the same time unless the machine can spare roughly 21 extra containers.
