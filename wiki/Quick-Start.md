# Quick Start Guide

Get the lab running in approximately 30 minutes.

## Prerequisites

### Required Software
- **Docker Desktop** (Windows/macOS) or Docker Engine (Linux)
- **Git**

### Arista cEOS Image
1. Register at [arista.com](https://www.arista.com/en/user-registration) (free)
2. Download: **Software Downloads → cEOS-lab → cEOS-lab-4.32.0F.tar.xz**
3. Place in the `docker/` folder

---

## Step 1: Clone and Setup (5 minutes)

```bash
# Clone the repository
git clone https://github.com/AheadAviation/workshop_arista_automation.git
cd workshop_arista_automation

# Import the cEOS image
docker import docker/cEOS-lab-4.32.0F.tar.xz ceos:4.32.0F

# Verify the image
docker images ceos
```

Expected output:
```
REPOSITORY   TAG       IMAGE ID       CREATED         SIZE
ceos         4.32.0F   abc123...      5 seconds ago   2.1GB
```

Create the environment file:
```bash
cp env.example .env
```

---

## Step 2: Start NetBox (5 minutes)

```bash
# Start the Docker stack
docker compose up -d

# Watch logs until ready
docker compose logs -f netbox
```

Wait for:
```
netbox-1  | [INFO] Listening at: http://0.0.0.0:8080
```

Press `Ctrl+C` to exit logs.

**Access NetBox:** http://localhost:8888

| Field | Value |
|-------|-------|
| Username | `admin` |
| Password | `admin` |

---

## Step 3: Import Network Data (5 minutes)

```bash
docker compose run --rm ansible python scripts/full_import.py
```

Expected output:
```
✓ Created custom fields
✓ Created device roles: spine, leaf
✓ Created 6 devices
✓ Created 48 interfaces
✓ Created 16 cables
✓ Import complete!
```

**Explore NetBox:**
- **Devices → Devices**: 6 devices with BGP ASN, Router ID
- **IPAM → IP Addresses**: Loopback and fabric IPs
- **Connections → Cables**: Fabric interconnections

---

## Step 4: Deploy Arista Devices (10 minutes)

### 4.1 Generate ContainerLab Topology

```bash
docker compose run --rm ansible python scripts/generate_clab_topology_docker.py
```

### 4.2 Generate Device Configurations

```bash
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml
```

### 4.3 Review Generated Configs

**Windows PowerShell:**
```powershell
dir output\configs\
type output\configs\spine-01.cfg
type output\configs\leaf-01.cfg
```

**Linux/Mac:**
```bash
ls -la output/configs/
cat output/configs/spine-01.cfg
```

### 4.4 Deploy the Switches

```bash
docker compose run --rm clab containerlab deploy --topo /clab/topology/topology-docker.clab.yml
```

Wait 2-3 minutes for cEOS containers to boot.

### 4.5 Verify Containers

```bash
docker ps --filter "name=clab-netbox" --format "table {{.Names}}\t{{.Status}}"
```

Expected:
```
NAMES                             STATUS
clab-netbox-arista-lab-spine-01   Up 2 minutes
clab-netbox-arista-lab-spine-02   Up 2 minutes
clab-netbox-arista-lab-leaf-01    Up 2 minutes
clab-netbox-arista-lab-leaf-02    Up 2 minutes
clab-netbox-arista-lab-leaf-03    Up 2 minutes
clab-netbox-arista-lab-leaf-04    Up 2 minutes
```

---

## Step 5: Push Configurations (5 minutes)

**Windows PowerShell:**
```powershell
.\scripts\push_configs.ps1
```

**Linux/Mac:**
```bash
chmod +x scripts/push_configs.sh
./scripts/push_configs.sh
```

Expected output:
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

---

## Step 6: Validate the Network (5 minutes)

### Run Full Test Suite

```bash
docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml
```

### Manual Verification

**Check BGP on spine:**
```bash
docker exec clab-netbox-arista-lab-spine-01 Cli -p 15 -c "show ip bgp summary"
```

**Ping across fabric:**
```bash
docker exec clab-netbox-arista-lab-leaf-01 Cli -p 15 -c "ping 10.255.0.14 source 10.255.0.11"
```

---

## 🎉 Success!

You now have a fully functional EVPN/VXLAN fabric running locally, with all configuration driven from NetBox!

**Next steps:**
- [[Day 2 Operations]] - Make changes in NetBox and push updates
- [[Architecture]] - Understand how everything works together
- [[Code Walkthrough: Config Generation]] - Dive into the Jinja2 templates

---

## Cleanup

```bash
# Destroy ContainerLab topology
docker compose run --rm clab containerlab destroy --topo /clab/topology/topology-docker.clab.yml --cleanup

# Stop all services
docker compose down

# Remove all data (fresh start)
docker compose down -v
```

