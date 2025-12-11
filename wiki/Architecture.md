# Architecture

This page explains the system design and data flow of the NetBox + Arista Lab Workshop.

## High-Level Architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│                              Docker Compose Stack                          │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                  │
│  │  PostgreSQL │────▶│   NetBox    │◀────│    Redis    │                  │
│  │   (Data)    │     │  (Web/API)  │     │   (Cache)   │                  │
│  └─────────────┘     └──────┬──────┘     └─────────────┘                  │
│                             │                                              │
│                        REST API                                            │
│                             │                                              │
│                    ┌────────▼────────┐                                     │
│                    │     Ansible     │                                     │
│                    │   Container     │                                     │
│                    │  ┌───────────┐  │                                     │
│                    │  │ pynetbox  │  │                                     │
│                    │  │ Jinja2    │  │                                     │
│                    │  │ arista.eos│  │                                     │
│                    │  └───────────┘  │                                     │
│                    └────────┬────────┘                                     │
│                             │                                              │
│                    ┌────────▼────────┐                                     │
│                    │  ContainerLab   │                                     │
│                    │    (clab)       │                                     │
│                    │  ┌───────────┐  │                                     │
│                    │  │ Docker-in │  │                                     │
│                    │  │  -Docker  │  │                                     │
│                    │  └───────────┘  │                                     │
│                    └────────┬────────┘                                     │
│                             │                                              │
│              ┌──────────────┼──────────────┐                               │
│              │              │              │                               │
│         ┌────▼────┐    ┌────▼────┐    ┌────▼────┐                         │
│         │spine-01 │    │spine-02 │    │  ...    │                         │
│         │ (cEOS)  │    │ (cEOS)  │    │         │                         │
│         └─────────┘    └─────────┘    └─────────┘                         │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Source Data Definition

Network topology is defined in YAML:

```
netbox_data/fabric_topology.yml
     │
     │  Defines:
     │  - Device names, roles, platforms
     │  - IP addresses (management, loopbacks, fabric links)
     │  - BGP ASNs, Router IDs
     │  - Cable connections between devices
     │
     ▼
```

### 2. NetBox Population

Python scripts import the data:

```
scripts/bootstrap_netbox.py    scripts/import_topology.py
     │                              │
     │  Creates:                    │  Creates:
     │  - Custom fields             │  - Devices
     │  - Device roles              │  - Interfaces
     │  - Device types              │  - IP addresses
     │  - Config contexts           │  - Cables
     │                              │
     └───────────┬──────────────────┘
                 │
                 ▼
           ┌──────────┐
           │  NetBox  │  ← Source of Truth
           │   API    │
           └────┬─────┘
                │
```

### 3. Config Generation

Ansible pulls data and renders templates:

```
                │
                ▼
    ┌───────────────────────┐
    │    Ansible Playbook   │
    │  generate_configs.yml │
    └───────────┬───────────┘
                │
                ▼
    ┌───────────────────────┐
    │  netbox.netbox plugin │
    │  - Inventory          │
    │  - Custom fields      │
    │  - Config contexts    │
    └───────────┬───────────┘
                │
                ▼
    ┌───────────────────────┐
    │  arista_config role   │
    │  - Extract facts      │
    │  - Calculate BGP      │
    │  - Render templates   │
    └───────────┬───────────┘
                │
                ▼
    ┌───────────────────────┐
    │   Jinja2 Templates    │
    │  - base.j2            │
    │  - bgp_underlay.j2    │
    │  - mlag_domain.j2     │
    │  - evpn_overlay.j2    │
    └───────────┬───────────┘
                │
                ▼
         output/configs/
         ├── spine-01.cfg
         ├── spine-02.cfg
         ├── leaf-01.cfg
         └── ...
```

### 4. Lab Deployment

ContainerLab creates the network:

```
    ┌───────────────────────────────┐
    │  generate_clab_topology.py    │
    │  - Query NetBox for devices   │
    │  - Query NetBox for cables    │
    │  - Generate topology YAML     │
    └───────────────┬───────────────┘
                    │
                    ▼
         topology-docker.clab.yml
                    │
                    ▼
    ┌───────────────────────────────┐
    │       ContainerLab            │
    │  containerlab deploy          │
    │  - Create Docker containers   │
    │  - Create veth links          │
    │  - Set management IPs         │
    └───────────────┬───────────────┘
                    │
                    ▼
         6 x cEOS containers
```

### 5. Config Push

Configs are pushed to running devices:

```
         output/configs/*.cfg
                    │
                    ▼
    ┌───────────────────────────────┐
    │      push_configs.ps1/.sh     │
    │  - docker exec for each dev   │
    │  - Copy to /mnt/flash/        │
    │  - configure replace          │
    └───────────────────────────────┘
                    │
                    ▼
         Running cEOS switches
         (configured from NetBox)
```

## Component Details

### Docker Services

| Service | Purpose | Port |
|---------|---------|------|
| `netbox` | Web UI and REST API | 8888 (host) → 8080 (container) |
| `postgres` | Database | 5432 (internal) |
| `redis` | Cache and queues | 6379 (internal) |
| `ansible` | Automation engine | N/A (one-shot) |
| `clab` | ContainerLab runner | N/A (privileged) |

### NetBox Data Model

```
┌─────────────────────────────────────────────────────────────┐
│                         DCIM                                 │
├─────────────────────────────────────────────────────────────┤
│  Sites                                                       │
│  └── DC1                                                     │
│                                                              │
│  Device Roles                                                │
│  ├── spine                                                   │
│  ├── leaf                                                    │
│  └── border                                                  │
│                                                              │
│  Device Types                                                │
│  └── arista-ceos-lab                                         │
│                                                              │
│  Devices                                                     │
│  ├── spine-01, spine-02                                      │
│  └── leaf-01, leaf-02, leaf-03, leaf-04                     │
│                                                              │
│  Interfaces                                                  │
│  ├── Management0 (OOB management)                           │
│  ├── Loopback0 (Router ID)                                  │
│  ├── Loopback1 (VTEP IP)                                    │
│  └── eth1-eth8 (Fabric links)                               │
│                                                              │
│  Cables                                                      │
│  └── spine-01:eth1 <──> leaf-01:eth1                        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                         IPAM                                 │
├─────────────────────────────────────────────────────────────┤
│  IP Addresses                                                │
│  ├── 172.30.30.0/24 (Management)                            │
│  ├── 10.255.0.0/24 (Loopback0 - Router IDs)                 │
│  ├── 10.255.1.0/24 (Loopback1 - VTEPs)                      │
│  └── 10.0.0.0/24 (Fabric P2P links)                         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                        Extras                                │
├─────────────────────────────────────────────────────────────┤
│  Custom Fields                                               │
│  ├── bgp_asn (integer)                                      │
│  ├── router_id (text)                                       │
│  ├── vtep_ip (text)                                         │
│  ├── mlag_index (integer)                                   │
│  └── mlag_name (text)                                       │
│                                                              │
│  Config Contexts                                             │
│  ├── fabric-global (EVPN defaults)                          │
│  ├── role-spine (BGP route-server settings)                 │
│  ├── role-leaf (VXLAN/MLAG settings)                        │
│  ├── tenant-core (VLANs for tenant)                         │
│  └── mlag-pair-1/2 (MLAG domain config)                     │
│                                                              │
│  Tags                                                        │
│  ├── containerlab                                           │
│  ├── evpn-fabric                                            │
│  ├── spine, leaf, border                                    │
│  └── mlag-pair-1, mlag-pair-2                               │
└─────────────────────────────────────────────────────────────┘
```

### Ansible Role Structure

```
roles/arista_config/
├── tasks/
│   └── main.yml              # Task orchestration
│       ├── Set device role fact
│       ├── Extract custom fields
│       ├── Build interface IP mapping
│       ├── Calculate BGP neighbors
│       └── Generate config from template
│
├── templates/
│   ├── sections/             # Modular template sections
│   │   ├── base.j2          # Main template (includes others)
│   │   ├── mgmt.j2          # Management VRF, hostname
│   │   ├── aaa.j2           # Authentication
│   │   ├── ntp.j2           # Time sync
│   │   ├── stp.j2           # Spanning tree
│   │   ├── bgp_underlay.j2  # eBGP for fabric
│   │   ├── evpn_overlay.j2  # EVPN address family
│   │   ├── mlag_domain.j2   # MLAG config
│   │   └── services.j2      # DNS, banners
│   │
│   └── calculate_bgp_neighbors.j2  # BGP neighbor calculation
│
└── defaults/
    └── main.yml              # Default variables
```

## Network Topology

### Physical Connectivity

```
                spine-01                    spine-02
                   │                           │
    eth1 eth2 eth3 eth4            eth1 eth2 eth3 eth4
      │    │    │    │               │    │    │    │
      │    │    │    └───────────────┼────┼────┼────┘
      │    │    └────────────────────┼────┼────┘
      │    └─────────────────────────┼────┘
      └──────────────────────────────┘
      │    │    │    │               │    │    │    │
    eth1 eth1 eth1 eth1            eth2 eth2 eth2 eth2
      │    │    │    │               │    │    │    │
   leaf-01 leaf-02 leaf-03 leaf-04
```

### IP Addressing

| Subnet | Purpose |
|--------|---------|
| 172.30.30.0/24 | Management (OOB) |
| 10.255.0.0/24 | Loopback0 (Router IDs) |
| 10.255.1.0/24 | Loopback1 (VTEP IPs) |
| 10.0.0.0/24 | Fabric point-to-point links |

### BGP Design

| Device Type | ASN | Peers |
|-------------|-----|-------|
| Spines | 65000 | All leaves |
| Leaf-01/02 | 65001 | Both spines |
| Leaf-03/04 | 65002 | Both spines |

---

**Next:** [[Code Walkthrough: NetBox Import]] - See how the Python scripts work

