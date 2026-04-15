# PRESENTATION GUIDE
**Workshop: NetBox + Arista Automation Lab**  
**Last Verified:** 2026-04-15  
**Status: FULLY OPERATIONAL — All tests passing**

---

## CURRENT STATE

Everything is running. The full pipeline — NetBox → Ansible → ContainerLab → validation — has been tested end-to-end:

| Component | Status |
|-----------|--------|
| NetBox | Running at http://localhost:8888 |
| 6 Devices in NetBox | spine-01/02, leaf-01/02/03/04 |
| ContainerLab | 6 cEOS nodes deployed |
| BGP Underlay | All peers established |
| MLAG | Active on all leaf pairs |
| VXLAN/EVPN Overlay | Configured and verified |

---

## QUICK PRE-DEMO CHECK

Run these before you start presenting:

```powershell
# Services up?
docker compose ps

# Lab containers running?
docker ps --format "table {{.Names}}\t{{.Status}}" | findstr clab

# One quick sanity check
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ip bgp summary"
```

If anything looks wrong, see the **Recovery Steps** section below.

---

## DEMO FLOW (18 minutes)

### Opening (2 min)

> "This workshop demonstrates using **NetBox as a single source of truth** to fully automate an Arista network. All device data — IPs, BGP ASNs, VLANs, MLAG pairs — lives in NetBox. Ansible reads it via API, renders Jinja2 templates, and pushes configs to devices running in ContainerLab. What you're about to see isn't scripted output — the network is live."

---

### Step 1: NetBox — The Source of Truth (5 min)

Open http://localhost:8888 — login: `admin / admin`

**Navigate to:** DCIM → Devices

Show the 6-device table. Click `leaf-01`:

- **Interfaces tab** — show eth1/eth2 (fabric links to spines), Port-Channel2000 (MLAG peer-link), Loopback0 (router ID), Loopback1 (VTEP IP)
- **Custom Fields** — BGP ASN `65001`, Router ID, VTEP IP, `mlag_index: 0`, `mlag_name: MLAG-PAIR-1`
- **Config Contexts tab** — show `mlag-pair-1` context (MLAG domain config), `tenant-core` context (VLANs), `fabric-global` (EVPN defaults)

Key talking point:
> "This is all the data Ansible needs. No hardcoded values in any playbook or template."

---

### Step 2: Data Import (1 min)

```powershell
docker compose run --rm ansible python scripts/full_import.py
```

> "This Python script reads our topology YAML and creates every device, interface, IP address, cable, custom field, and config context in NetBox via its REST API. It's idempotent — safe to run repeatedly."

---

### Step 3: Config Generation (3 min)

```powershell
# Generate ContainerLab topology from NetBox
docker compose run --rm ansible python scripts/generate_clab_topology_docker.py

# Generate all device configs
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml

# Show the results
dir output\configs
type output\configs\leaf-01.cfg
```

While reviewing `leaf-01.cfg`, point out:
- Hostname comes from NetBox
- Management IP from NetBox IPAM
- MLAG peer-link and domain config from `mlag-pair-1` config context
- BGP neighbors: spine-01 and spine-02 Loopback0 IPs pulled from NetBox
- VXLAN interface bound to Loopback1 (VTEP IP from NetBox)
- Tenant CORE: VRF + 5 VLANs + SVI IPs — all from the `tenant-core` config context

> "Same Ansible role generates all six configs. Role-based templates, data-driven."

---

### Step 4: ContainerLab Deployment (1 min)

```powershell
docker compose run --rm clab containerlab deploy --topo /clab/topology/topology-docker.clab.yml
```

> "Six cEOS containers spin up with virtual Ethernet links between them — exactly matching the topology in NetBox. Under 60 seconds."

---

### Step 5: Config Push (30 sec)

```powershell
.\scripts\push_configs.ps1
```

> "Copies each generated config to the device's flash and applies it with `configure replace`. The device's running config now matches exactly what NetBox defined."

---

### Step 6: Live Validation (3 min)

```powershell
docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml
```

Walk through the test output as it runs:

| Test | What It Verifies |
|------|-----------------|
| Device Reachability | All 6 nodes respond to SSH |
| BGP Status | All eBGP peers established |
| MLAG Status | MLAG Active on all 4 leaves |
| Routing Table | Routes present (21 on spines, 15 on leaves) |
| End-to-End | Leaf-to-spine pings across the fabric |
| VXLAN | Vxlan1 up, VNIs programmed |

> "Every test is green. Zero failures. The fabric is fully operational."

---

### Step 7: Day-2 Operations (optional, 2 min)

> "Here's the Day-2 story: you make a change in NetBox — add a VLAN, update an IP, change a BGP timer — then run these three commands and the network is updated:"

```powershell
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
.\scripts\push_configs.ps1
docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml
```

> "NetBox is your config management database. Ansible is your deployment engine. You never SSH into a device to make changes manually again."

---

### Closing (2 min)

> "What you saw:
> - NetBox as a single source of truth — all network data in one place
> - Ansible pulling that data via API, no static inventory files  
> - Jinja2 templates rendering production-quality Arista configs
> - ContainerLab deploying the lab in under a minute
> - Automated validation confirming the network works
>
> The whole workflow — fresh start to fully validated fabric — runs in under 5 minutes. Change something in NetBox, regenerate, push, validate. That's modern network automation."

---

## LIVE DEVICE COMMANDS (Extra Detail)

If audience wants to dig deeper:

```powershell
# BGP summary — all peers established
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ip bgp summary"

# MLAG state — Active
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show mlag"

# VXLAN interface
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show interfaces Vxlan1"

# VNI table
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show vxlan vni"

# EVPN peers
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show bgp evpn summary"

# IP routes (EVPN-learned)
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show ip route vrf CORE"
```

---

## KEY METRICS TO HIGHLIGHT

- **6 devices** configured end-to-end in under 5 minutes
- **0 manual edits** to any device config — all from templates
- **100% data-driven** — NetBox is the only place to make changes
- **100% test coverage** — 6 automated test categories after every push
- **Fully reproducible** — `docker compose down -v && docker compose up` starts from scratch

---

## KNOWN REQUIREMENTS (If Asked)

| Requirement | Detail |
|-------------|--------|
| Docker Desktop | Running on Windows with WSL2 |
| cEOS Image | `ceos:4.32.0F` — download from arista.com (free account) |
| RAM | ~6 GB for 6 cEOS containers |
| Disk | ~2 GB for images + data |
| Python packages | Bundled in Ansible container |

---

## RECOVERY STEPS

### If ContainerLab is not running
```powershell
docker compose run --rm clab containerlab deploy --topo /clab/topology/topology-docker.clab.yml
.\scripts\push_configs.ps1
```

### If NetBox data is missing
```powershell
docker compose run --rm ansible python scripts/full_import.py
```

### If configs need regeneration
```powershell
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
```

### Full reset (nuclear option)
```powershell
docker compose run --rm clab containerlab destroy --topo /clab/topology/topology-docker.clab.yml --cleanup
docker compose down -v
docker compose up -d
# Wait 2-3 min
docker compose run --rm ansible python scripts/full_import.py
docker compose run --rm ansible python scripts/generate_clab_topology_docker.py
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
docker compose run --rm clab containerlab deploy --topo /clab/topology/topology-docker.clab.yml
.\scripts\push_configs.ps1
docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml
```

---

**Everything is working. You're ready to present.**
