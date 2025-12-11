# NetBox + Arista Lab Workshop

Welcome to the **NetBox + Arista Lab Workshop**! This hands-on workshop demonstrates how to use NetBox as a Source of Truth to automate Arista network configuration.

## 🎯 What You'll Learn

| Concept | Description |
|---------|-------------|
| **NetBox as Source of Truth** | Store all network data (devices, IPs, cables) in NetBox |
| **Dynamic Config Generation** | Use Ansible + Jinja2 to generate configs from NetBox data |
| **ContainerLab** | Spin up virtual Arista cEOS switches for testing |
| **Validation Testing** | Ansible playbooks to verify the network works |
| **Day 2 Operations** | Change in NetBox → Regenerate → Push → Validate |

## 🏗️ What You'll Build

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
┌──▼─┐┌─▼──┐┌▼──┐┌──▼┐
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

## 📚 Workshop Sections

### Getting Started
- [[Quick Start]] - Get the lab running in 30 minutes
- [[Architecture]] - Understand the system design and data flow

### Code Walkthroughs
- [[Code Walkthrough: NetBox Import]] - How the Python scripts populate NetBox
- [[Code Walkthrough: Config Generation]] - Ansible roles and Jinja2 templates
- [[Code Walkthrough: ContainerLab]] - Dynamic topology generation

### Reference
- [[Template Reference]] - All Jinja2 templates explained
- [[Troubleshooting]] - Common issues and solutions
- [[Day 2 Operations]] - Making changes and re-pushing configs

## 🚀 Quick Start

Ready to begin? Head to the [[Quick Start]] guide!

```bash
# Clone the repo
git clone https://github.com/AheadAviation/workshop_arista_automation.git
cd workshop_arista_automation

# Import cEOS image
docker import docker/cEOS-lab-4.32.0F.tar.xz ceos:4.32.0F

# Start NetBox
docker compose up -d

# Import data and deploy
docker compose run --rm ansible python scripts/full_import.py
```

## 📋 Prerequisites

- **Docker Desktop** (Windows/macOS) or Docker Engine (Linux)
- **Git**
- **Arista cEOS Image** (free with Arista account)

## 🔗 External Resources

- [NetBox Documentation](https://docs.netbox.dev/)
- [Arista EOS Documentation](https://www.arista.com/en/um-eos)
- [ContainerLab Documentation](https://containerlab.dev/)
- [Ansible Network Automation](https://docs.ansible.com/ansible/latest/network/index.html)

