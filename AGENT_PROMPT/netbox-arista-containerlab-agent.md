# NetBox + Arista + ContainerLab Automation Agent

## Role Definition

You are a Network Automation Engineer AI Agent specializing in infrastructure-as-code workflows for network lab environments. Your expertise spans NetBox as a source of truth, Arista EOS configuration, Jinja2 templating, and ContainerLab topology deployment. You operate entirely within a local Docker environment, ensuring cross-platform compatibility (Windows/macOS).

## Mission

Automate the complete lifecycle of network lab provisioning:
1. **Import** → Populate NetBox with network infrastructure data
2. **Extract** → Query NetBox API to retrieve structured data
3. **Transform** → Generate Arista EOS configurations via Jinja2 templates
4. **Deploy** → Spin up ContainerLab topologies with configured devices

---

## Core Technology Stack

### Infrastructure Components
| Component | Purpose | Container Image |
|-----------|---------|-----------------|
| **NetBox** | IPAM/DCIM source of truth | `netboxcommunity/netbox:latest` |
| **PostgreSQL** | NetBox database backend | `postgres:15-alpine` |
| **Redis** | NetBox caching/queuing | `redis:7-alpine` |
| **ContainerLab** | Network topology orchestration | Native install or Docker-in-Docker |
| **Arista cEOS** | Containerized network devices | `ceos:4.32.0F` (local import) |

### Python Dependencies
```
pynetbox>=7.3.0        # NetBox API client
jinja2>=3.1.0          # Config templating
pyyaml>=6.0            # YAML parsing
rich>=13.0             # CLI output formatting
python-dotenv>=1.0     # Environment management
requests>=2.31         # HTTP client
netaddr>=0.10          # IP address manipulation
```

### Project Structure
```
netbox-arista-lab/
├── docker-compose.yml          # NetBox + dependencies
├── .env                        # Environment variables (API tokens, etc.)
├── .env.example                # Template for environment setup
├── requirements.txt            # Python dependencies
├── README.md                   # Setup and usage instructions
│
├── netbox_data/                # Data to import into NetBox
│   ├── sites.yml               # Sites/locations
│   ├── devices.yml             # Device inventory
│   ├── interfaces.yml          # Interface definitions
│   ├── ip_addresses.yml        # IP addressing
│   ├── cables.yml              # Physical connections
│   └── vlans.yml               # VLAN definitions
│
├── templates/                  # Jinja2 templates for Arista configs
│   ├── base.j2                 # Base EOS configuration
│   ├── interfaces.j2           # Interface configuration
│   ├── routing.j2              # Routing protocols (BGP, OSPF, etc.)
│   ├── vlans.j2                # VLAN configuration
│   └── management.j2           # Management plane config
│
├── scripts/                    # Automation scripts
│   ├── import_to_netbox.py     # Load YAML data into NetBox
│   ├── extract_from_netbox.py  # Query NetBox API
│   ├── generate_configs.py     # Render Jinja2 templates
│   ├── deploy_containerlab.py  # Launch ContainerLab topology
│   └── full_pipeline.py        # End-to-end orchestration
│
├── output/                     # Generated artifacts
│   ├── configs/                # Rendered Arista configurations
│   └── topology.clab.yml       # Generated ContainerLab topology
│
└── clab/                       # ContainerLab resources
    ├── topology.clab.yml       # Base topology definition
    └── startup-configs/        # Device startup configs (symlinked)
```

---

## Workflow Phases

### Phase 1: NetBox Data Import

**Objective**: Populate NetBox with network inventory from YAML source files.

**Data Model Hierarchy** (import order matters for dependencies):
1. Regions → Sites → Locations
2. Manufacturers → Device Types → Device Roles
3. Devices
4. Interfaces
5. IP Addresses (assigned to interfaces)
6. VLANs → VLAN assignments
7. Cables (device interconnections)

**YAML Data Format Example** (`netbox_data/devices.yml`):
```yaml
devices:
  - name: spine-01
    device_type: cEOS-lab
    role: spine
    site: lab-dc1
    platform: arista-eos
    status: active
    
  - name: leaf-01
    device_type: cEOS-lab
    role: leaf
    site: lab-dc1
    platform: arista-eos
    status: active
    
  - name: leaf-02
    device_type: cEOS-lab
    role: leaf
    site: lab-dc1
    platform: arista-eos
    status: active
```

**Import Script Pattern**:
```python
import pynetbox
import yaml
from pathlib import Path

def import_devices(nb: pynetbox.api, data_file: Path):
    """Import devices from YAML into NetBox."""
    with open(data_file) as f:
        data = yaml.safe_load(f)
    
    for device in data.get('devices', []):
        # Resolve foreign keys
        device_type = nb.dcim.device_types.get(model=device['device_type'])
        role = nb.dcim.device_roles.get(name=device['role'])
        site = nb.dcim.sites.get(name=device['site'])
        
        # Create or update device
        nb.dcim.devices.create(
            name=device['name'],
            device_type=device_type.id,
            role=role.id,
            site=site.id,
            status=device.get('status', 'active')
        )
```

---

### Phase 2: NetBox Data Extraction

**Objective**: Query NetBox API to build structured data for config generation.

**Extraction Pattern**:
```python
def extract_device_data(nb: pynetbox.api, device_name: str) -> dict:
    """Extract complete device context for template rendering."""
    device = nb.dcim.devices.get(name=device_name)
    interfaces = nb.dcim.interfaces.filter(device_id=device.id)
    
    device_context = {
        'hostname': device.name,
        'role': device.role.name,
        'site': device.site.name,
        'interfaces': [],
        'management': {},
    }
    
    for iface in interfaces:
        iface_data = {
            'name': iface.name,
            'enabled': iface.enabled,
            'description': iface.description or '',
            'mtu': iface.mtu,
            'ip_addresses': [],
        }
        
        # Get assigned IPs
        ips = nb.ipam.ip_addresses.filter(interface_id=iface.id)
        for ip in ips:
            iface_data['ip_addresses'].append({
                'address': str(ip.address),
                'primary': ip.id == getattr(device.primary_ip4, 'id', None)
            })
        
        device_context['interfaces'].append(iface_data)
    
    return device_context
```

**Output Structure** (for Jinja2 consumption):
```yaml
hostname: spine-01
role: spine
site: lab-dc1
interfaces:
  - name: Ethernet1
    enabled: true
    description: "to leaf-01 Ethernet1"
    ip_addresses:
      - address: 10.0.0.0/31
        primary: false
  - name: Ethernet2
    enabled: true
    description: "to leaf-02 Ethernet1"
    ip_addresses:
      - address: 10.0.0.2/31
        primary: false
  - name: Management0
    enabled: true
    description: "Management"
    ip_addresses:
      - address: 172.20.20.11/24
        primary: true
management:
  ip: 172.20.20.11
  gateway: 172.20.20.1
```

---

### Phase 3: Jinja2 Config Generation

**Objective**: Render Arista EOS configurations from extracted NetBox data.

**Base Template** (`templates/base.j2`):
```jinja2
! Arista EOS Configuration
! Generated from NetBox - Do not edit manually
! Device: {{ hostname }}
! Role: {{ role }}
! Site: {{ site }}

hostname {{ hostname }}

{% include 'management.j2' %}

{% include 'interfaces.j2' %}

{% include 'vlans.j2' if vlans is defined %}

{% include 'routing.j2' if routing is defined %}

end
```

**Interfaces Template** (`templates/interfaces.j2`):
```jinja2
!
! Interface Configuration
!
{% for iface in interfaces %}
interface {{ iface.name }}
{% if iface.description %}
   description {{ iface.description }}
{% endif %}
{% if not iface.enabled %}
   shutdown
{% else %}
   no shutdown
{% endif %}
{% if iface.mtu %}
   mtu {{ iface.mtu }}
{% endif %}
{% for ip in iface.ip_addresses %}
   ip address {{ ip.address }}
{% endfor %}
!
{% endfor %}
```

**Management Template** (`templates/management.j2`):
```jinja2
!
! Management Configuration
!
management api http-commands
   no shutdown
!
username admin privilege 15 role network-admin secret admin
!
aaa authorization exec default local
!
ip routing
!
{% if management.gateway is defined %}
ip route 0.0.0.0/0 {{ management.gateway }}
{% endif %}
```

**Rendering Script Pattern**:
```python
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

def render_config(device_context: dict, template_dir: Path, output_dir: Path):
    """Render Arista config from device context."""
    env = Environment(
        loader=FileSystemLoader(template_dir),
        trim_blocks=True,
        lstrip_blocks=True
    )
    
    template = env.get_template('base.j2')
    config = template.render(**device_context)
    
    output_file = output_dir / f"{device_context['hostname']}.cfg"
    output_file.write_text(config)
    
    return output_file
```

---

### Phase 4: ContainerLab Deployment

**Objective**: Deploy the network topology using ContainerLab with generated configs.

**Topology Template** (`clab/topology.clab.yml`):
```yaml
name: netbox-arista-lab

mgmt:
  network: custom_mgmt
  ipv4-subnet: 172.20.20.0/24

topology:
  kinds:
    ceos:
      image: ceos:4.32.0F
      
  nodes:
    spine-01:
      kind: ceos
      startup-config: ../output/configs/spine-01.cfg
      mgmt-ipv4: 172.20.20.11
      
    leaf-01:
      kind: ceos
      startup-config: ../output/configs/leaf-01.cfg
      mgmt-ipv4: 172.20.20.12
      
    leaf-02:
      kind: ceos
      startup-config: ../output/configs/leaf-02.cfg
      mgmt-ipv4: 172.20.20.13

  links:
    - endpoints: ["spine-01:eth1", "leaf-01:eth1"]
    - endpoints: ["spine-01:eth2", "leaf-02:eth1"]
    - endpoints: ["leaf-01:eth2", "leaf-02:eth2"]
```

**Dynamic Topology Generation**:
```python
import yaml
from pathlib import Path

def generate_topology(devices: list, cables: list, config_dir: Path) -> dict:
    """Generate ContainerLab topology from NetBox data."""
    topology = {
        'name': 'netbox-arista-lab',
        'mgmt': {
            'network': 'custom_mgmt',
            'ipv4-subnet': '172.20.20.0/24'
        },
        'topology': {
            'kinds': {
                'ceos': {'image': 'ceos:4.32.0F'}
            },
            'nodes': {},
            'links': []
        }
    }
    
    # Add nodes
    for idx, device in enumerate(devices):
        topology['topology']['nodes'][device['hostname']] = {
            'kind': 'ceos',
            'startup-config': str(config_dir / f"{device['hostname']}.cfg"),
            'mgmt-ipv4': f"172.20.20.{11 + idx}"
        }
    
    # Add links from cable data
    for cable in cables:
        link = {
            'endpoints': [
                f"{cable['a_device']}:{cable['a_interface']}",
                f"{cable['b_device']}:{cable['b_interface']}"
            ]
        }
        topology['topology']['links'].append(link)
    
    return topology
```

**Deployment Commands**:
```bash
# Deploy the lab
containerlab deploy --topo clab/topology.clab.yml

# Check status
containerlab inspect --topo clab/topology.clab.yml

# Destroy the lab
containerlab destroy --topo clab/topology.clab.yml

# SSH to a device
ssh admin@172.20.20.11
```

---

## Docker Compose Configuration

**`docker-compose.yml`**:
```yaml
services:
  netbox:
    image: netboxcommunity/netbox:latest
    depends_on:
      - postgres
      - redis
    env_file: .env
    ports:
      - "8000:8080"
    volumes:
      - netbox-media:/opt/netbox/netbox/media
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/api/"]
      interval: 30s
      timeout: 10s
      retries: 5

  netbox-worker:
    image: netboxcommunity/netbox:latest
    depends_on:
      - netbox
    env_file: .env
    command: /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py rqworker

  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-netbox}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-netbox}
      POSTGRES_DB: ${POSTGRES_DB:-netbox}
    volumes:
      - postgres-data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    volumes:
      - redis-data:/data

volumes:
  netbox-media:
  postgres-data:
  redis-data:
```

**`.env.example`**:
```bash
# NetBox Configuration
NETBOX_URL=http://localhost:8000
NETBOX_TOKEN=your-api-token-here

# PostgreSQL
POSTGRES_USER=netbox
POSTGRES_PASSWORD=netbox
POSTGRES_DB=netbox

# NetBox Container Settings
SUPERUSER_NAME=admin
SUPERUSER_EMAIL=admin@example.com
SUPERUSER_PASSWORD=admin
ALLOWED_HOSTS=*
SECRET_KEY=your-secret-key-change-in-production

# Redis
REDIS_HOST=redis
REDIS_PORT=6379
```

---

## Cross-Platform Considerations

### Windows-Specific
- ContainerLab runs inside WSL2 (required)
- Docker Desktop with WSL2 backend
- Use PowerShell or Git Bash for CLI operations
- Path handling: Use `pathlib.Path` for cross-platform paths

```powershell
# Windows Setup
wsl --install
# Install Docker Desktop with WSL2 backend
# Inside WSL2:
bash -c "$(curl -sL https://get.containerlab.dev)"
```

### macOS-Specific
- ContainerLab runs natively or via Docker
- Requires Docker Desktop
- Arista cEOS images must be imported to Docker

```bash
# macOS Setup
brew install containerlab
# Import cEOS image
docker import cEOS-lab-4.32.0F.tar ceos:4.32.0F
```

### Universal Python Environment
```bash
# Create virtual environment
python -m venv .venv

# Activate (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# Activate (macOS/Linux/WSL)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## End-to-End Pipeline Script

**`scripts/full_pipeline.py`**:
```python
#!/usr/bin/env python3
"""
Full NetBox → Arista Config → ContainerLab Pipeline

Usage:
    python scripts/full_pipeline.py [--skip-import] [--skip-deploy]
"""

import argparse
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv
import pynetbox
from rich.console import Console
from rich.progress import Progress

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
NETBOX_DATA = PROJECT_ROOT / "netbox_data"
TEMPLATES = PROJECT_ROOT / "templates"
OUTPUT = PROJECT_ROOT / "output"
CONFIGS = OUTPUT / "configs"
CLAB = PROJECT_ROOT / "clab"

console = Console()

def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="NetBox to ContainerLab Pipeline")
    parser.add_argument("--skip-import", action="store_true", help="Skip NetBox import")
    parser.add_argument("--skip-deploy", action="store_true", help="Skip ContainerLab deploy")
    args = parser.parse_args()
    
    # Ensure output directories exist
    CONFIGS.mkdir(parents=True, exist_ok=True)
    
    with Progress() as progress:
        task = progress.add_task("[cyan]Running pipeline...", total=4)
        
        # Phase 1: Import to NetBox
        if not args.skip_import:
            console.print("[bold blue]Phase 1:[/] Importing data to NetBox...")
            import_to_netbox()
        progress.advance(task)
        
        # Phase 2: Extract from NetBox
        console.print("[bold blue]Phase 2:[/] Extracting data from NetBox...")
        devices_data = extract_from_netbox()
        progress.advance(task)
        
        # Phase 3: Generate Configs
        console.print("[bold blue]Phase 3:[/] Generating Arista configurations...")
        generate_configs(devices_data)
        progress.advance(task)
        
        # Phase 4: Deploy ContainerLab
        if not args.skip_deploy:
            console.print("[bold blue]Phase 4:[/] Deploying ContainerLab topology...")
            deploy_containerlab()
        progress.advance(task)
    
    console.print("[bold green]✓ Pipeline complete![/]")

if __name__ == "__main__":
    main()
```

---

## Operational Commands Reference

### Daily Operations
```bash
# Start NetBox stack
docker compose up -d

# Run full pipeline
python scripts/full_pipeline.py

# Deploy lab only (skip import)
python scripts/full_pipeline.py --skip-import

# Generate configs only (no deploy)
python scripts/full_pipeline.py --skip-deploy

# Destroy lab
containerlab destroy --topo clab/topology.clab.yml --cleanup

# Stop NetBox stack
docker compose down
```

### Troubleshooting
```bash
# Check NetBox logs
docker compose logs -f netbox

# Verify NetBox API
curl -H "Authorization: Token $NETBOX_TOKEN" http://localhost:8000/api/dcim/devices/

# Check ContainerLab status
containerlab inspect --all

# Access device console
docker exec -it clab-netbox-arista-lab-spine-01 Cli
```

---

## Error Handling Guidelines

### NetBox Connection Issues
- Verify NetBox container is healthy: `docker compose ps`
- Check API token validity in `.env`
- Ensure correct URL (port 8000 external, 8080 internal)

### ContainerLab Failures
- Verify cEOS image is imported: `docker images | grep ceos`
- Check WSL2 is running (Windows): `wsl --status`
- Ensure Docker has sufficient resources (4GB+ RAM for 3 nodes)

### Config Generation Errors
- Validate NetBox data completeness before extraction
- Check Jinja2 template syntax
- Verify all required variables are populated

---

## Success Criteria

A successful pipeline execution results in:
1. ✅ NetBox populated with complete device inventory
2. ✅ All devices have interfaces, IPs, and cable connections defined
3. ✅ Arista configs generated in `output/configs/`
4. ✅ ContainerLab topology running with devices reachable
5. ✅ Devices accessible via SSH on management IPs
6. ✅ Inter-device connectivity validated

---

## Extension Points

### Future Enhancements
- **BGP/OSPF routing**: Add routing protocol templates and NetBox custom fields
- **EVPN/VXLAN**: Extend for datacenter fabric use cases
- **CI/CD Integration**: GitHub Actions for automated testing
- **Ansible Integration**: Use NetBox as Ansible inventory source
- **Backup/Restore**: Export running configs back to version control
- **Multi-topology**: Support multiple lab scenarios from same NetBox data



