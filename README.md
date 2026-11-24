# 🔬 NetBox + Arista Lab Workshop

**Build a local Arista network lab using NetBox as your Source of Truth**

This hands-on workshop demonstrates a complete "Source of Truth" workflow:
1. **Start fresh NetBox** → Import network data
2. **Deploy Arista devices** → ContainerLab spins up cEOS switches  
3. **Pull data from NetBox** → Generate & push configs to devices
4. **Validate the network** → Run tests to verify everything works

Everything runs in Docker containers - no special setup required.

## 📚 Documentation

**📖 [Full Workshop Wiki](https://github.com/AheadAviation/workshop_arista_automation/wiki)** - Detailed walkthroughs and reference

| Page | Description |
|------|-------------|
| [Quick Start](https://github.com/AheadAviation/workshop_arista_automation/wiki/Quick-Start) | Get the lab running in 30 minutes |
| [Architecture](https://github.com/AheadAviation/workshop_arista_automation/wiki/Architecture) | System design and data flow |
| [Code Walkthrough: NetBox](https://github.com/AheadAviation/workshop_arista_automation/wiki/Code-Walkthrough-NetBox-Import) | How Python scripts populate NetBox |
| [Code Walkthrough: Config Generation](https://github.com/AheadAviation/workshop_arista_automation/wiki/Code-Walkthrough-Config-Generation) | Ansible roles and Jinja2 templates |
| [Code Walkthrough: ContainerLab](https://github.com/AheadAviation/workshop_arista_automation/wiki/Code-Walkthrough-ContainerLab) | Dynamic topology generation |
| [Template Reference](https://github.com/AheadAviation/workshop_arista_automation/wiki/Template-Reference) | All Jinja2 templates explained |
| [Troubleshooting](https://github.com/AheadAviation/workshop_arista_automation/wiki/Troubleshooting) | Common issues and solutions |
| [Day 2 Operations](https://github.com/AheadAviation/workshop_arista_automation/wiki/Day-2-Operations) | Making changes and re-pushing configs |

---

## 🚀 Quick Start (30 minutes)

### Prerequisites

- **Docker Desktop** (Windows/macOS) or Docker Engine (Linux)
- **Git**
- **Arista cEOS Image** - Download from [arista.com](https://www.arista.com/en/user-registration) (free account)

### Step 1: Setup

```bash
git clone https://github.com/AheadAviation/workshop_arista_automation.git
cd workshop_arista_automation

# Import cEOS image
docker import docker/cEOS-lab-4.32.0F.tar.xz ceos:4.32.0F

# Create environment file
cp env.example .env
```

### Step 2: Start NetBox

```bash
docker compose up -d

# Wait for NetBox to be ready (2-3 minutes)
docker compose logs -f netbox
# Look for: [INFO] Listening at: http://0.0.0.0:8080
```

**Access NetBox:** http://localhost:8888 (admin / admin)

### Step 3: Import Network Data

```bash
docker compose run --rm ansible python scripts/full_import.py
```

### Step 4: Generate and Review Configs

```bash
# Generate ContainerLab topology
docker compose run --rm ansible python scripts/generate_clab_topology_docker.py

# Generate device configurations from NetBox
docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml

# Review generated configs before deploying
dir output\configs\                    # Windows
ls -la output/configs/                 # Linux/Mac

# View a config
type output\configs\spine-01.cfg       # Windows
cat output/configs/spine-01.cfg        # Linux/Mac
```

### Step 5: Deploy Lab

```bash
docker compose run --rm clab containerlab deploy --topo /clab/topology/topology-docker.clab.yml
```

### Step 6: Push Configs

```powershell
# Windows
.\scripts\push_configs.ps1

# Linux/Mac
./scripts/push_configs.sh
```

### Step 7: Validate

```bash
docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml
```

---

## 🏗️ What You'll Build

A 2-spine, 4-leaf EVPN/VXLAN fabric:

```
                    ┌─────────────┐
                    │   NetBox    │  ← Source of Truth
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   Ansible   │  ← Config Generation
                    └──────┬──────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
   ┌────▼────┐        ┌────▼────┐        ContainerLab
   │ spine-01│◄──────►│ spine-02│        (6 cEOS nodes)
   └────┬────┘        └────┬────┘
        │                  │
   ┌────┼────┬────────────┼────┐
   │    │    │            │    │
┌──▼─┐┌─▼──┐┌▼──┐    ┌──▼┐┌─▼──┐
│L-01││L-02││L-03│    │L-04│
└────┘└────┘└────┘    └────┘
 MLAG Pair 1      MLAG Pair 2
```

| Device | Role | BGP ASN | Management IP |
|--------|------|---------|---------------|
| spine-01 | Spine | 65000 | 172.30.30.11 |
| spine-02 | Spine | 65000 | 172.30.30.12 |
| leaf-01 | Leaf | 65001 | 172.30.30.21 |
| leaf-02 | Leaf | 65001 | 172.30.30.22 |
| leaf-03 | Leaf | 65002 | 172.30.30.23 |
| leaf-04 | Leaf | 65002 | 172.30.30.24 |

---

## 🔧 Quick Reference

| Action | Command |
|--------|---------|
| **Start NetBox** | `docker compose up -d` |
| **Import data** | `docker compose run --rm ansible python scripts/full_import.py` |
| **Generate topology** | `docker compose run --rm ansible python scripts/generate_clab_topology_docker.py` |
| **Generate configs** | `docker compose run --rm ansible ansible-playbook playbooks/generate_configs.yml` |
| **Deploy lab** | `docker compose run --rm clab containerlab deploy --topo /clab/topology/topology-docker.clab.yml` |
| **Push configs** | `.\scripts\push_configs.ps1` (Win) / `./scripts/push_configs.sh` (Linux) |
| **Run tests** | `docker compose run --rm ansible ansible-playbook -i inventory/hosts.yml playbooks/test_all.yml` |
| **Destroy lab** | `docker compose run --rm clab containerlab destroy --topo /clab/topology/topology-docker.clab.yml --cleanup` |
| **Stop everything** | `docker compose down` |

---

## 📁 Project Structure

```
netbox-arista-lab/
├── docker-compose.yml          # Docker services
├── scripts/                    # Python import/topology scripts
├── playbooks/                  # Ansible playbooks
├── roles/arista_config/        # Config generation role + Jinja2 templates
├── inventory/                  # Ansible inventories
├── netbox_data/               # Source topology YAML
├── output/configs/            # Generated device configs
└── wiki/                      # Documentation (mirrors GitHub Wiki)
```

---

## 🧹 Cleanup

```bash
# Destroy ContainerLab
docker compose run --rm clab containerlab destroy --topo /clab/topology/topology-docker.clab.yml --cleanup

# Stop all services
docker compose down

# Remove all data (fresh start)
docker compose down -v
```

---

## 🎯 What You'll Learn

- **NetBox as Source of Truth** - All network data lives in NetBox
- **Dynamic Config Generation** - Ansible pulls data from NetBox API
- **Jinja2 Templates** - Modular templates render device configs
- **ContainerLab** - Quick lab deployment with cEOS containers
- **Validation Testing** - Ansible playbooks verify the network works
- **Day 2 Operations** - Change in NetBox → Regenerate → Push → Validate

---

## Dual-DC AVD track

`labs/l3ls-dual-dc` is a second lab: two L3LS fabrics, EVPN gateway, and an FRR DCI router, generated by Arista AVD. It does not replace the NetBox flow above.

```powershell
.\run.ps1 dualdc build
.\run.ps1 dualdc start
.\run.ps1 dualdc apply
```

See [wiki/Dual-DC-L3LS.md](wiki/Dual-DC-L3LS.md).

## 📚 Resources

- [Full Workshop Wiki](https://github.com/AheadAviation/workshop_arista_automation/wiki)
- [NetBox Documentation](https://docs.netbox.dev/)
- [Arista EOS Documentation](https://www.arista.com/en/um-eos)
- [ContainerLab Documentation](https://containerlab.dev/)
- [Ansible Network Automation](https://docs.ansible.com/ansible/latest/network/index.html)

---

## 📝 License

MIT License - See LICENSE file for details.
