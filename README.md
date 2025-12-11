# 🔬 NetBox + Arista Lab Workshop

**Build a local Arista network lab using NetBox as your Source of Truth**

This hands-on workshop demonstrates a complete "Source of Truth" workflow:
1. **Start fresh NetBox** → Import network data
2. **Deploy Arista devices** → ContainerLab spins up cEOS switches
3. **Pull data from NetBox** → Generate & push configs to devices
4. **Validate the network** → Run tests to verify everything works

Everything runs in Docker containers - no special setup required.

---

## 📋 What You'll Build

A fully functional 2-spine, 4-leaf EVPN/VXLAN fabric:

```
                    ┌─────────────┐
                    │   NetBox    │  ← Source of Truth
                    │  (Docker)   │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   Ansible   │  ← Config Generation
                    │  (Docker)   │
                    └──────┬──────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
   ┌────▼────┐        ┌────▼────┐        ContainerLab
   │ spine-01│◄──────►│ spine-02│        (6 cEOS nodes)
   └────┬────┘        └────┬────┘
        │                  │
   ┌────┼────┬────┬────┬───┼────┐
   │    │    │    │    │   │    │
┌──▼─┐┌─▼──┐┌▼──┐┌──▼┐┌▼──┐┌─▼──┐
│L-01││L-02││L-03││L-04│ (Leaves)
└────┘└────┘└────┘└────┘
 MLAG Pair 1   MLAG Pair 2
```

| Device | Role | BGP ASN | Loopback | Management IP |
|--------|------|---------|----------|---------------|
| spine-01 | Spine | 65000 | 10.255.0.1 | 172.30.30.11 |
| spine-02 | Spine | 65000 | 10.255.0.2 | 172.30.30.12 |
| leaf-01 | Leaf | 65001 | 10.255.0.11 | 172.30.30.21 |
| leaf-02 | Leaf | 65001 | 10.255.0.12 | 172.30.30.22 |
| leaf-03 | Leaf | 65002 | 10.255.0.13 | 172.30.30.23 |
| leaf-04 | Leaf | 65002 | 10.255.0.14 | 172.30.30.24 |

---

## 🚀 Prerequisites

### Required Software
- **Docker Desktop** (Windows/macOS) or Docker Engine (Linux)
- **Git** (to clone this repo)

### Arista cEOS Image
You need the Arista cEOS container image (free with Arista account):

1. Register at [arista.com](https://www.arista.com/en/user-registration)
2. Download: **Software Downloads → cEOS-lab → cEOS-lab-4.32.0F.tar.xz**
3. Place the file in the `docker/` folder of this project

---

# 🎓 Workshop

## Step 1: Clone and Setup (5 minutes)

### 1.1 Clone the Repository

```bash
git clone https://github.com/yourusername/netbox-arista-lab.git
cd netbox-arista-lab
```

### 1.2 Import the cEOS Image

```bash
docker import docker/cEOS-lab-4.32.0F.tar.xz ceos:4.32.0F
docker images ceos
```

You should see:
```
REPOSITORY   TAG       IMAGE ID       CREATED         SIZE
ceos         4.32.0F   abc123...      5 seconds ago   2.1GB
```

### 1.3 Create Environment File

```bash
cp env.example .env
```

---

## Step 2: Start NetBox (5 minutes)

### 2.1 Start the Docker Stack

```bash
docker compose up -d
```

This starts:
- **NetBox** (web interface + worker)
- **PostgreSQL** (database)
- **Redis** (cache)

### 2.2 Wait for NetBox to Initialize

```bash
docker compose logs -f netbox
```

Wait until you see:
```
netbox-1  | [INFO] Listening at: http://0.0.0.0:8080
```

Press `Ctrl+C` to exit the logs.

### 2.3 Access NetBox

Open your browser: **http://localhost:8888**

**Login Credentials:**
| Field | Value |
|-------|-------|
| Username | `admin` |
| Password | `admin` |

✅ **Checkpoint:** You should see an empty NetBox dashboard.

---

## Step 3: Import Network Data into NetBox (5 minutes)

Now we'll import our network topology into NetBox. This creates all devices, interfaces, IP addresses, and cables.

### 3.1 Run the Import Script

```bash
docker compose run --rm ansible python scripts/full_import.py
```

You'll see output like:
```
✓ Created custom fields
✓ Created device roles: spine, leaf
✓ Created device types
✓ Created 6 devices
✓ Created 48 interfaces
✓ Created 16 cables
✓ Import complete!
```

### 3.2 Explore NetBox

Refresh your browser and explore:

| Section | What to See |
|---------|-------------|
| **Devices → Devices** | 6 devices with custom fields (BGP ASN, Router ID, MLAG settings) |
| **IPAM → IP Addresses** | Loopback and fabric link IPs |
| **Connections → Cables** | 12 fabric cables connecting spines to leaves |
| **Extras → Config Contexts** | EVPN fabric settings |

✅ **Checkpoint:** NetBox shows 6 devices with all their network data.

---

## Step 4: Deploy Arista Devices (10 minutes)

Now we'll use ContainerLab to spin up the actual Arista cEOS switches.

### 4.1 Generate the ContainerLab Topology

This script reads device data from NetBox and creates the topology file:

```bash
docker compose run --rm ansible python scripts/generate_clab_topology_docker.py
```

### 4.2 Generate Device Configurations

Before deploying switches, generate the configurations from NetBox data:

```bash
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
```

### 4.3 Review Generated Configurations

Take a moment to inspect the generated configs before deployment:

**Windows PowerShell:**
```powershell
# List all generated configs
dir output\configs\

# View a spine config
type output\configs\spine-01.cfg

# View a leaf config (notice MLAG and VXLAN sections)
type output\configs\leaf-01.cfg
```

**Linux/Mac:**
```bash
# List all generated configs
ls -la output/configs/

# View a spine config
cat output/configs/spine-01.cfg

# View a leaf config
cat output/configs/leaf-01.cfg
```

You'll see complete Arista EOS configurations with:
- Hostname and management settings
- Loopback interfaces (Router ID, VTEP IP)
- Fabric link IP addresses
- BGP underlay configuration
- EVPN/VXLAN overlay settings (for leaves)
- MLAG configuration (for leaf pairs)

✅ **Checkpoint:** Configs generated in `output/configs/` - review them before proceeding.

### 4.4 Deploy the Switches

```bash
docker compose run --rm clab containerlab deploy --topo /clab/topology/topology-docker.clab.yml
```

This will take 2-3 minutes as each cEOS container boots.

### 4.5 Verify Containers are Running

```bash
docker ps --filter "name=clab-netbox" --format "table {{.Names}}\t{{.Status}}"
```

You should see all 6 devices:
```
NAMES                             STATUS
clab-netbox-arista-lab-spine-01   Up 2 minutes
clab-netbox-arista-lab-spine-02   Up 2 minutes
clab-netbox-arista-lab-leaf-01    Up 2 minutes
clab-netbox-arista-lab-leaf-02    Up 2 minutes
clab-netbox-arista-lab-leaf-03    Up 2 minutes
clab-netbox-arista-lab-leaf-04    Up 2 minutes
```

✅ **Checkpoint:** 6 Arista cEOS containers running.

---

## Step 5: Push Configurations to Devices (5 minutes)

Now we'll push the configurations we generated in Step 4 to the running cEOS switches.

### 5.1 Push Configs to the Switches

**Windows PowerShell:**
```powershell
.\scripts\push_configs.ps1
```

**Linux/Mac:**
```bash
chmod +x scripts/push_configs.sh
./scripts/push_configs.sh
```

You'll see:
```
Pushing configs to running ContainerLab devices...
  Pushing config to spine-01... Done
  Pushing config to spine-02... Done
  Pushing config to leaf-01... Done
  Pushing config to leaf-02... Done
  Pushing config to leaf-03... Done
  Pushing config to leaf-04... Done
All configurations pushed successfully!
```

✅ **Checkpoint:** All 6 devices configured from NetBox data.

---

## Step 6: Validate the Network (10 minutes)

Let's verify the network is working correctly.

### 6.1 Run the Full Test Suite

```bash
docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml
```

This tests:
- ✓ Device reachability (SSH)
- ✓ BGP underlay (all neighbors established)
- ✓ MLAG status
- ✓ Routing table population
- ✓ End-to-end connectivity (ping between leaves)
- ✓ VXLAN interface status

### 6.2 Manual Verification

**Check BGP on a spine:**
```bash
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ip bgp summary"
```

Expected: 4 neighbors, all Established

**Check BGP on a leaf:**
```bash
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "show ip bgp summary"
```

Expected: 2 neighbors (both spines), all Established

**Ping across the fabric:**
```bash
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "ping 10.255.0.14 source 10.255.0.11"
```

Expected: 100% success (leaf-01 → leaf-04 through spines)

### 6.3 Generate a Fabric Report

```bash
docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/gather_facts_report.yml
```

View the report:

**Windows:**
```powershell
type output\reports\fabric_report.json
```

**Linux/Mac:**
```bash
cat output/reports/fabric_report.json
```

✅ **Checkpoint:** Network fully operational with all BGP sessions up.

---

## Step 7: Day 2 Operations - Change in NetBox (10 minutes)

Now let's demonstrate the "Source of Truth" workflow by making a change in NetBox.

### 7.1 Make a Change in NetBox

1. Open NetBox: **http://localhost:8888**
2. Go to **Devices → spine-01**
3. Click **Edit**
4. Change the **Description** field to "Core Spine - Updated via NetBox"
5. Click **Save**

### 7.2 Regenerate Configs

```bash
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
```

### 7.3 Push the Updated Config

**Windows:**
```powershell
.\scripts\push_configs.ps1
```

**Linux/Mac:**
```bash
./scripts/push_configs.sh
```

### 7.4 Verify the Change

```bash
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show running-config | grep description"
```

✅ **Checkpoint:** Changes made in NetBox are now on the device.

---

## 🧹 Cleanup

When you're done with the workshop:

```bash
# Destroy the ContainerLab topology
docker compose run --rm clab containerlab destroy --topo /clab/topology/topology-docker.clab.yml --cleanup

# Stop NetBox and all services
docker compose down

# Remove all data (fresh start for next time)
docker compose down -v
```

---

## 📁 Project Structure

```
netbox-arista-lab/
├── docker-compose.yml          # Docker services (NetBox, Ansible, ContainerLab)
├── docker/
│   ├── ansible/Dockerfile      # Ansible container with all dependencies
│   └── clab-runner/Dockerfile  # ContainerLab runner
│
├── scripts/
│   ├── full_import.py          # Import topology to NetBox
│   ├── generate_clab_topology_docker.py  # Generate ContainerLab topology
│   ├── push_configs.ps1        # Push configs (Windows)
│   └── push_configs.sh         # Push configs (Linux/Mac)
│
├── playbooks/
│   ├── generate_configs.yml    # Generate configs from NetBox data
│   ├── test_all.yml            # Run all validation tests
│   ├── validate_fabric.yml     # BGP/MLAG/VXLAN validation
│   ├── test_connectivity.yml   # Ping/traceroute tests
│   └── gather_facts_report.yml # Generate fabric report
│
├── roles/arista_config/
│   └── templates/              # Jinja2 templates for EOS configs
│
├── inventory/
│   └── hosts.yml               # Static inventory with management IPs
│
├── netbox_data/
│   └── fabric_topology.yml     # Source data for NetBox import
│
└── output/
    ├── configs/                # Generated device configs
    └── reports/                # Fabric reports
```

---

## 🔧 Quick Reference

| Action | Command |
|--------|---------|
| **Start NetBox** | `docker compose up -d` |
| **Import data to NetBox** | `docker compose run --rm ansible python scripts/full_import.py` |
| **Generate topology** | `docker compose run --rm ansible python scripts/generate_clab_topology_docker.py` |
| **Deploy switches** | `docker compose run --rm clab containerlab deploy --topo /clab/topology/topology-docker.clab.yml` |
| **Generate configs** | `docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml` |
| **Push configs** | `.\scripts\push_configs.ps1` (Win) or `./scripts/push_configs.sh` (Linux/Mac) |
| **Run all tests** | `docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml` |
| **Destroy lab** | `docker compose run --rm clab containerlab destroy --topo /clab/topology/topology-docker.clab.yml --cleanup` |
| **Stop everything** | `docker compose down` |

---

## ❓ Troubleshooting

### NetBox won't start
```bash
docker compose logs netbox
# Wait 2-3 minutes on first start - it needs to run migrations
```

### cEOS containers not starting
```bash
# Verify the image exists
docker images ceos

# If not, import it again
docker import docker/cEOS-lab-4.32.0F.tar.xz ceos:4.32.0F
```

### BGP not establishing
```bash
# Check if configs were pushed
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show running-config | section bgp"

# If empty, re-push configs
.\scripts\push_configs.ps1
```

### Tests failing with "unreachable"
```bash
# Verify containers are running
docker ps --filter "name=clab-netbox"

# Try pinging from Ansible container
docker compose run --rm ansible ping -c 1 172.30.30.11
```

---

## 🎯 What You Learned

1. **NetBox as Source of Truth** - All network data lives in NetBox
2. **Dynamic Config Generation** - Ansible pulls data from NetBox API
3. **Jinja2 Templates** - Modular templates render device configs
4. **ContainerLab** - Quick lab deployment with cEOS containers
5. **Validation Testing** - Ansible playbooks verify the network works
6. **Day 2 Operations** - Change in NetBox → Regenerate → Push → Validate

---

## 📚 Resources

- [NetBox Documentation](https://docs.netbox.dev/)
- [Arista EOS Documentation](https://www.arista.com/en/um-eos)
- [ContainerLab Documentation](https://containerlab.dev/)
- [Ansible Network Automation](https://docs.ansible.com/ansible/latest/network/index.html)

---

## 📝 License

MIT License - See LICENSE file for details.
