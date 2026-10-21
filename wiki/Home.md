# NetBox + Arista Lab Workshop

Welcome to the **NetBox + Arista Automation Workshop** — a complete, hands-on demonstration of using NetBox as a Source of Truth to automate Arista network deployment.

## What You'll Build

A fully functional 2-spine, 4-leaf EVPN/VXLAN fabric running entirely in Docker:

```
              ┌────────────┐        ┌────────────┐
              │  spine-01  │◄──────►│  spine-02  │
              │ ASN 65000  │        │ ASN 65000  │
              └─────┬──────┘        └──────┬─────┘
         eth1/2/3/4 │                      │ eth1/2/3/4
                    │    (eBGP + EVPN)     │
          ┌─────────┼──────────────────────┤
          │         │                      │
    eth1 ─┤   eth1 ─┤                eth1 ─┤   eth1 ─┤
  ┌───────▼┐  ┌──────▼─┐          ┌───────▼┐  ┌──────▼─┐
  │ leaf-01│  │ leaf-02│          │ leaf-03│  │ leaf-04│
  │ ASN    │  │ ASN    │          │ ASN    │  │ ASN    │
  │ 65001  │  │ 65001  │          │ 65002  │  │ 65002  │
  └────────┘  └────────┘          └────────┘  └────────┘
   ◄── MLAG Pair 1 ──►             ◄── MLAG Pair 2 ──►
```

| Device | Role | BGP ASN | Router ID | VTEP IP | Management IP |
|--------|------|---------|-----------|---------|---------------|
| spine-01 | Spine | 65000 | 10.255.0.1 | — | 172.30.30.11 |
| spine-02 | Spine | 65000 | 10.255.0.2 | — | 172.30.30.12 |
| leaf-01 | Leaf | 65001 | 10.255.0.11 | 10.255.1.11 | 172.30.30.21 |
| leaf-02 | Leaf | 65001 | 10.255.0.12 | 10.255.1.12 | 172.30.30.22 |
| leaf-03 | Leaf | 65002 | 10.255.0.13 | 10.255.1.13 | 172.30.30.23 |
| leaf-04 | Leaf | 65002 | 10.255.0.14 | 10.255.1.14 | 172.30.30.24 |

---

## The Automation Pipeline

```
netbox_data/fabric_topology.yml
          │
          ▼ python scripts/full_import.py
     ┌─────────┐
     │  NetBox │  ← single source of truth (devices, IPs, cables,
     │         │    custom fields, config contexts, tags)
     └────┬────┘
          │
          ├──► generate_clab_topology_docker.py ──► topology-docker.clab.yml
          │                                                │
          │                                         containerlab deploy
          │                                         (6 cEOS containers)
          │
          └──► ansible-playbook generate_configs.yml
                        │  (Jinja2 templates, per-device, role-based)
                        ▼
                 output/configs/*.cfg
                        │
                        ▼ push_configs.ps1 (configure replace)
                 Running network
                        │
                        ▼ playbooks/test_all.yml
                 Validated: BGP ✓  MLAG ✓  VXLAN ✓
```

---

## What You'll Learn

| Concept | Details |
|---------|---------|
| **NetBox as Source of Truth** | All network data — IPs, BGP ASNs, MLAG settings, tenant VLANs — lives in NetBox, not in flat files or templates |
| **NetBox REST API** | Python scripts interact with the API to populate and query data |
| **Ansible + NetBox inventory plugin** | Dynamic inventory pulls host variables directly from NetBox, including config contexts |
| **Jinja2 templating** | Modular templates render per-role configs (spine vs leaf); data-driven, no hardcoding |
| **ContainerLab** | Lightweight cEOS lab deployment in Docker with virtual Ethernet links |
| **EVPN/VXLAN fabric** | BGP EVPN control plane, VXLAN data plane, MLAG for leaf redundancy |
| **Automated validation** | Ansible playbooks verify BGP, MLAG, VXLAN, and end-to-end reachability |
| **Day 2 operations** | Change in NetBox → regenerate → push → validate — the complete workflow |

---

## Workshop Sections

### Start Here
- [[Quick Start]] — Complete step-by-step walkthrough from zero to validated fabric
- [[Workshop 2 Walkthrough]] — Set up the sample manufacturer and run the six ODL acts

### Understand the System
- [[Architecture]] — System design, data flow, and component interactions

### Dive Into the Code
- [[Code Walkthrough: NetBox Import]] — How the Python scripts populate NetBox
- [[Code Walkthrough: Config Generation]] — Ansible roles and Jinja2 templates
- [[Code Walkthrough: ContainerLab]] — Dynamic topology generation

### Reference
- [[Template Reference]] — All Jinja2 config templates explained
- [[Troubleshooting]] — Common issues and their solutions
- [[Day 2 Operations]] — Making changes and pushing updates to a running fabric

---

## Prerequisites

- **Docker Desktop** (Windows/macOS) or Docker Engine (Linux) — must be running
- **Git**
- **Arista cEOS image** — download from [arista.com](https://www.arista.com/en/user-registration) (free account)
  - Software Downloads → EOS → cEOS-lab → `cEOS-lab-4.32.0F.tar.xz`
  - Import once: `docker import cEOS-lab-4.32.0F.tar.xz ceos:4.32.0F`

---

## External Resources

- [NetBox Documentation](https://docs.netbox.dev/)
- [Arista EOS Documentation](https://www.arista.com/en/um-eos)
- [ContainerLab Documentation](https://containerlab.dev/)
- [Ansible Network Automation](https://docs.ansible.com/ansible/latest/network/index.html)
