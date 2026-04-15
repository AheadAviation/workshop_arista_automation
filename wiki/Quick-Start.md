# Full Workshop Walkthrough

This is the **complete, hands-on demo** — from a blank slate to a fully operational EVPN/VXLAN fabric running in Docker. Every step is shown in order. Follow this page top to bottom.

**What you'll build:**
- NetBox populated with all devices, interfaces, IPs, cables, and config contexts
- 6 cEOS containers running in ContainerLab (2 spines + 4 leaves)
- Full configs pushed: BGP underlay, MLAG pairs, VXLAN/EVPN overlay, tenant VLANs
- Live network validation — all tests passing

**Time to complete:** ~20 minutes

---

## Prerequisites

Before you start, you need:
- Docker Desktop running
- The cEOS image imported (one-time step):
  ```powershell
  docker import cEOS-lab-4.32.0F.tar.xz ceos:4.32.0F
  docker images ceos
  ```
  Download from [arista.com](https://www.arista.com/en/user-registration) (free account — Software Downloads → EOS → cEOS-lab-4.32.0F.tar.xz)

---

## Step 1 — Clean Slate

Start fresh. If you have an existing lab or NetBox data, tear it all down first:

```powershell
# Destroy any running ContainerLab topology
docker compose run --rm clab containerlab destroy --topo /clab/topology/topology-docker.clab.yml --cleanup

# Stop all services and wipe all volumes (NetBox database, Redis, etc.)
docker compose down -v

# Clear any previously generated configs
Remove-Item output\configs\*.cfg -ErrorAction SilentlyContinue
Remove-Item clab\topology-docker.clab.yml -ErrorAction SilentlyContinue
```

---

## Step 2 — Start NetBox

```powershell
docker compose up -d
```

This starts: PostgreSQL, Redis, NetBox, and the NetBox background worker.

Watch for NetBox to become healthy:

```powershell
docker compose logs -f netbox
```

Wait for this line:
```
netbox-1  | [INFO] Listening at: http://0.0.0.0:8080
```

Press `Ctrl+C` to stop following logs.

**Verify all services are healthy:**
```powershell
docker compose ps
```

Expected:
```
NAME              STATUS
netbox-netbox-1          Up (healthy)
netbox-postgres-1        Up (healthy)
netbox-redis-1           Up (healthy)
netbox-netbox-worker-1   Up
```

**Open the NetBox UI:** http://localhost:8888  
Login: `admin` / `admin`

NetBox is empty at this point — no devices, no IPs, no cables. That's correct. We're about to load it all from our topology file.

---

## Step 3 — Import Network Data into NetBox

This single script reads `netbox_data/fabric_topology.yml` and populates NetBox with everything via its REST API:

```powershell
docker compose run --rm ansible python scripts/full_import.py
```

This creates in NetBox:
- **Site:** DC1
- **Device roles:** spine, leaf
- **Device type:** arista-ceos-lab
- **6 Devices:** spine-01, spine-02, leaf-01, leaf-02, leaf-03, leaf-04
- **Interfaces:** Management0, Loopback0, Loopback1, eth1–eth8 per device
- **IP Addresses:** Management IPs, loopback IPs, fabric P2P IPs
- **Cables:** 8 spine-to-leaf links + 4 MLAG peer links = 12 total
- **Custom fields:** bgp_asn, router_id, vtep_ip, mlag_index, mlag_name
- **Config Contexts:** fabric-global, fabric-underlay, fabric-overlay, role-spine, role-leaf, tenant-core, mlag-pair-1, mlag-pair-2
- **Tags:** containerlab, evpn-fabric, spine, leaf, mlag-pair-1, mlag-pair-2

### Explore NetBox

Refresh http://localhost:8888 — you'll now see data populated.

**Navigate to DCIM → Devices** — 6 devices appear.

Click **leaf-01**. Explore:

| Tab | What to show |
|-----|-------------|
| Interfaces | Management0, Loopback0 (router-id), Loopback1 (VTEP IP), eth1/eth2 (to spines), Port-Channel2000 (MLAG peer-link) |
| Custom Fields | BGP ASN: 65001, Router ID: 10.255.0.11, VTEP IP: 10.255.1.11, mlag_index: 0, mlag_name: MLAG-PAIR-1 |
| Config Contexts | Shows merged data from: mlag-pair-1, tenant-core, role-leaf, fabric-global |

> **Talking point:** "Everything Ansible uses to build the device config lives here. No hardcoded values anywhere in the playbooks or templates."

---

## Step 4 — Generate the ContainerLab Topology

This script queries NetBox for all devices and cables, then writes a ContainerLab topology file:

```powershell
docker compose run --rm ansible python scripts/generate_clab_topology_docker.py
```

Review the output file:

```powershell
type clab\topology-docker.clab.yml
```

The file defines 6 nodes and 12 links — all sourced from NetBox. ContainerLab will use this to create Docker containers and wire them together.

---

## Step 5 — Generate Device Configurations

Ansible reads data from NetBox via the dynamic inventory plugin and renders Jinja2 templates for each device:

```powershell
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
```

This generates 6 `.cfg` files in `output/configs/`:

```powershell
dir output\configs
```

### Review a Spine Config

```powershell
type output\configs\spine-01.cfg
```

**What to point out:**
- `hostname spine-01` — from NetBox device name
- `ip routing` and management VRF
- BGP ASN `65000` — from custom field
- Neighbors: 4 leaf loopback IPs — pulled from NetBox
- EVPN address family with all leaf peers

### Review a Leaf Config

```powershell
type output\configs\leaf-01.cfg
```

**What to point out:**

```
mlag configuration
   domain-id MLAG-PAIR-1       ← from config context
   local-interface Vlan4094
   peer-address 169.254.0.1
   peer-link Port-Channel2000   ← MLAG peer-link
```

```
interface Vxlan1
   vxlan source-interface Loopback1   ← VTEP IP from NetBox
   vxlan vlan 100 vni 100
   vxlan vlan 110 vni 110
   ...
```

```
router bgp 65001
   neighbor 10.255.0.1 remote-as 65000   ← spine-01 Loopback0, from NetBox
   neighbor 10.255.0.2 remote-as 65000   ← spine-02 Loopback0, from NetBox
   address-family evpn
      neighbor 10.255.0.1 activate
      neighbor 10.255.0.2 activate
```

> **Talking point:** "Same Ansible role generates all 6 configs. The role is data-driven — device role and config context determine what sections are rendered."

---

## Step 6 — Deploy the Lab

ContainerLab spins up 6 cEOS Docker containers and creates virtual Ethernet links between them:

```powershell
docker compose run --rm clab containerlab deploy --topo /clab/topology/topology-docker.clab.yml
```

Wait ~60 seconds for all containers to boot.

**Verify they're running:**

```powershell
docker ps --format "table {{.Names}}`t{{.Status}}" | findstr clab
```

Expected:
```
clab-netbox-arista-lab-spine-01   Up 1 minute
clab-netbox-arista-lab-spine-02   Up 1 minute
clab-netbox-arista-lab-leaf-01    Up 1 minute
clab-netbox-arista-lab-leaf-02    Up 1 minute
clab-netbox-arista-lab-leaf-03    Up 1 minute
clab-netbox-arista-lab-leaf-04    Up 1 minute
```

**Peek at a device (default unconfigured state):**

```powershell
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show running-config | head 10"
```

You'll see the bare default EOS config — no BGP, no IPs on interfaces. This is before we push.

---

## Step 7 — Push Configurations

The push script copies each generated config to the device's flash and applies it using `configure replace`:

```powershell
.\scripts\push_configs.ps1
```

Expected output:
```
Pushing configs to running ContainerLab devices...
  spine-01... OK: Config applied
  spine-02... OK: Config applied
  leaf-01...  OK: Config applied
  leaf-02...  OK: Config applied
  leaf-03...  OK: Config applied
  leaf-04...  OK: Config applied
All configurations pushed successfully!
```

---

## Step 8 — Verify Configs on the Devices

Now connect to devices and confirm the configuration was applied and the network is converging.

### Check spine-01

```powershell
# Show the running config
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show running-config"

# BGP summary — should show 4 neighbors
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ip bgp summary"
```

Expected BGP output on spine-01:
```
BGP summary information for VRF default
Router identifier 10.255.0.1, local AS number 65000
Neighbor          AS       MsgRcvd  MsgSent   State   PfxRcd
10.255.0.11   65001         ...       ...   Established  ...
10.255.0.12   65001         ...       ...   Established  ...
10.255.0.13   65002         ...       ...   Established  ...
10.255.0.14   65002         ...       ...   Established  ...
```

### Check leaf-01 — MLAG

```powershell
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show mlag"
```

Expected:
```
MLAG Status:
state          :    Active
negotiation status  :    Connected
peer-link status    :    Up
local-int status    :    Up
system-id      :    ...
MLAG Ports:
...
```

### Check leaf-01 — VXLAN

```powershell
# Interface status
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show interfaces Vxlan1"

# VNI table
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show vxlan vni"

# EVPN BGP peers
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show bgp evpn summary"
```

### Check leaf-01 — Tenant VLANs

```powershell
# VLANs defined
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show vlan"

# CORE VRF routing table
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show ip route vrf CORE"
```

### Ping Across the Fabric

```powershell
# leaf-01 Loopback0 → spine-01 Loopback0
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "ping 10.255.0.1 source 10.255.0.11"

# leaf-01 → spine-02
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "ping 10.255.0.2 source 10.255.0.11"

# leaf-03 → both spines
docker exec clab-netbox-arista-lab-leaf-03 Cli -p 15 -c "ping 10.255.0.1 source 10.255.0.13"
docker exec clab-netbox-arista-lab-leaf-03 Cli -p 15 -c "ping 10.255.0.2 source 10.255.0.13"
```

---

## Step 9 — Run the Full Validation Suite

One playbook checks everything:

```powershell
docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml
```

This runs 6 test categories across all 6 devices:

| Test | Checks |
|------|--------|
| Device Reachability | All 6 nodes answer SSH |
| BGP Status | All eBGP sessions Established |
| MLAG Status | MLAG Active on all 4 leaves |
| Routing Table | Correct route counts |
| End-to-End Pings | Leaf loopbacks ping spine loopbacks |
| VXLAN Verification | Vxlan1 up, VNIs programmed |

All tests should pass with 0 failures.

---

## What You've Demonstrated

```
netbox_data/fabric_topology.yml   ← topology source definition
         │
         ▼
scripts/full_import.py            ← Python populates NetBox via API
         │
         ▼
    NetBox                        ← single source of truth
    (devices, IPs, cables,
     custom fields, config
     contexts, tags)
         │
         ├─── scripts/generate_clab_topology_docker.py
         │         │
         │         ▼
         │    clab/topology-docker.clab.yml
         │         │
         │         ▼
         │    containerlab deploy  ← 6 cEOS containers + links
         │
         └─── ansible-playbook generate_configs.yml
                   │  (Ansible reads NetBox via inventory plugin)
                   │  (Jinja2 templates render per-device configs)
                   ▼
              output/configs/*.cfg
                   │
                   ▼
              push_configs.ps1    ← configure replace on each device
                   │
                   ▼
         Running network verified by test_all.yml
```

---

## Cleanup

When you're done:

```powershell
# Destroy ContainerLab (removes all cEOS containers and links)
docker compose run --rm clab containerlab destroy --topo /clab/topology/topology-docker.clab.yml --cleanup

# Stop all Docker services
docker compose down

# Full wipe including NetBox database (use for a fresh start)
docker compose down -v
```

---

**Next:** [[Day 2 Operations]] — make a change in NetBox and push it to the running fabric
