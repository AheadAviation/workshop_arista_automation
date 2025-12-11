# Template Reference

This page provides a detailed reference for all Jinja2 templates used to generate Arista EOS configurations.

## Template Structure

```
roles/arista_config/templates/
├── sections/                    # Modular template sections
│   ├── base.j2                 # Main template (orchestrates includes)
│   ├── mgmt.j2                 # Management VRF and hostname
│   ├── local_users.j2          # User accounts
│   ├── aaa.j2                  # Authentication/Authorization
│   ├── ntp.j2                  # NTP configuration
│   ├── logging.j2              # Syslog configuration
│   ├── stp.j2                  # Spanning Tree Protocol
│   ├── varp.j2                 # Virtual ARP
│   ├── mlag_domain.j2          # MLAG configuration
│   ├── prefix_lists.j2         # IP prefix lists
│   ├── route_maps.j2           # Route maps
│   ├── bgp_underlay.j2         # BGP underlay (eBGP for fabric)
│   ├── bgp_peer_groups.j2      # BGP peer group definitions
│   ├── evpn_overlay.j2         # EVPN overlay configuration
│   ├── interfaces.j2           # Physical interfaces
│   ├── port_interfaces.j2      # Port-specific config
│   ├── portchannel_interfaces.j2  # Port-channels
│   ├── vlan_interfaces.j2      # SVI configuration
│   └── services.j2             # DNS, banners, misc
│
└── calculate_bgp_neighbors.j2  # BGP neighbor calculation helper
```

---

## Main Template: base.j2

**Purpose:** Orchestrates all other templates with conditional includes.

```jinja2
!
! ============================================================================
! {{ inventory_hostname }} - Arista EOS Configuration
! Generated from NetBox - Source of Truth
! ============================================================================
!
{% include 'sections/mgmt.j2' %}
!
{% include 'sections/aaa.j2' %}
!
{% if ntp is defined %}
{% include 'sections/ntp.j2' %}
{% endif %}
!
{% if log_hosts is defined %}
{% include 'sections/logging.j2' %}
{% endif %}
!
{% include 'sections/stp.j2' %}
!
{% if varp_mac is defined %}
{% include 'sections/varp.j2' %}
{% endif %}
!
{% if mlag_domain is defined %}
{% include 'sections/mlag_domain.j2' %}
{% endif %}
!
{% if prefix_lists is defined and prefix_lists | length > 0 %}
{% include 'sections/prefix_lists.j2' %}
{% endif %}
!
{% if route_maps is defined and route_maps | length > 0 %}
{% include 'sections/route_maps.j2' %}
{% endif %}
!
{% if device_role == 'leaf' or device_role == 'spine' %}
{% include 'sections/bgp_underlay.j2' %}
{% endif %}
!
{% if device_role == 'leaf' and tenants is defined %}
{% include 'sections/evpn_overlay.j2' %}
{% endif %}
!
{% if local_interfaces is defined %}
{% include 'sections/interfaces.j2' %}
{% endif %}
!
{% include 'sections/services.j2' %}
!
! ============================================================================
! End of Configuration
! ============================================================================
end
!
```

**Key Logic:**
- All devices get: mgmt, aaa, stp, services
- Conditional: ntp, logging, varp, mlag, prefix_lists, route_maps
- Role-based: bgp_underlay (spine/leaf), evpn_overlay (leaf only)

---

## Management Templates

### mgmt.j2

**Purpose:** Hostname, management VRF, API access.

```jinja2
! # START mgmt.j2
!
hostname {{ inventory_hostname }}
!
{% include 'sections/local_users.j2' %}
!
no aaa root
!
vrf instance {{ mgmt_vrf | default('MGMT') }}
ip routing vrf {{ mgmt_vrf | default('MGMT') }}
!
vlan internal order ascending range 4000 4079
!
service routing protocols model multi-agent
!
interface Management0
   description OoB MGMT
   vrf {{ mgmt_vrf | default('MGMT') }}
   ip address {{ primary_ip | default('dhcp') }}
!
!
ip route vrf {{ mgmt_vrf | default('MGMT') }} 0.0.0.0/0 {{ mgmt_gateway }} name MGMT_DEFAULT_ROUTE
!
management api http-commands
   protocol unix-socket
   no shutdown
   !
   vrf {{ mgmt_vrf | default('MGMT') }}
      no shutdown
!
! # END mgmt.j2
```

**Variables:**
| Variable | Source | Default |
|----------|--------|---------|
| `inventory_hostname` | NetBox device name | - |
| `mgmt_vrf` | Config context | `MGMT` |
| `primary_ip` | NetBox primary IP | `dhcp` |
| `mgmt_gateway` | Config context | - |

### local_users.j2

**Purpose:** Local user accounts.

```jinja2
! # START local_users.j2
! Default lab user
username admin privilege 15 role network-admin nopassword
!
! # END local_users.j2
```

---

## Authentication Templates

### aaa.j2

**Purpose:** AAA configuration.

```jinja2
! # START aaa.j2
!
!
aaa authentication login default local
aaa authentication login console local
!
!
aaa authorization exec default local
aaa authorization commands all default local
!
!
! # END aaa.j2
```

---

## Services Templates

### ntp.j2

**Purpose:** NTP time synchronization.

```jinja2
! # START ntp.j2
{% if ntp.interface is defined %}
ntp local-interface vrf {{ ntp.vrf | default('MGMT') }} {{ ntp.interface }}
{% endif %}
!
{% for server in ntp.servers | default([]) %}
ntp server vrf {{ ntp.vrf | default('MGMT') }} {{ server }} iburst
{% endfor %}
!
! # END ntp.j2
```

**Variables:**
| Variable | Source | Example |
|----------|--------|---------|
| `ntp.servers` | Config context | `["172.30.30.1"]` |
| `ntp.vrf` | Config context | `MGMT` |
| `ntp.interface` | Config context | `Management0` |

### logging.j2

**Purpose:** Syslog configuration.

```jinja2
! # START logging.j2
{% for host in log_hosts | default([]) %}
logging host {{ host.ip }} {{ host.port | default(514) }} protocol {{ host.protocol | default('udp') }}
{% endfor %}
!
logging facility local7
logging source-interface {{ log_source_interface | default('Management0') }}
!
! # END logging.j2
```

### services.j2

**Purpose:** DNS, banners, and miscellaneous services.

```jinja2
! # START services.j2
!
! Management and monitoring services
!
{% for server in dns.servers | default([]) %}
ip name-server vrf {{ mgmt_vrf | default('MGMT') }} {{ server }}
{% endfor %}
!
!
banner login
***************************************************************************
* This system is for authorized use only.                                  *
* All access is logged and monitored.                                      *
***************************************************************************
EOF
!
! # END services.j2
```

---

## Spanning Tree Template

### stp.j2

**Purpose:** Spanning Tree Protocol configuration.

```jinja2
! # START stp.j2
spanning-tree mode {{ stp_mode | default('mstp') }}
{% if device_role == 'spine' %}
spanning-tree priority 4096
{% elif device_role == 'leaf' %}
spanning-tree priority 24576
{% else %}
spanning-tree priority 32768
{% endif %}
!
! # END stp.j2
```

**Logic:** Spines get lowest priority (4096), leaves get higher (24576).

---

## MLAG Templates

### mlag_domain.j2

**Purpose:** Multi-Chassis Link Aggregation configuration.

```jinja2
! # START mlag_domain.j2
{% if mlag_domain is defined %}
!
vlan {{ mlag_domain.peerlink_vlan | default(4094) }}
   name MLAG_PEER_LINK
   trunk group MLAG-PEER
!
{% for intf in mlag_domain.interfaces | default(['eth3', 'eth4']) %}
interface {{ intf | arista_interface_name }}
   channel-group {{ mlag_domain.peerlink_channel | default(2000) }} mode active
!
{% endfor %}
!
interface Port-Channel{{ mlag_domain.peerlink_channel | default(2000) }}
   description {{ mlag_domain.description | default('MLAG Peer Link') }}
   switchport mode trunk
   switchport trunk group MLAG-PEER
!
interface Vlan{{ mlag_domain.peerlink_vlan | default(4094) }}
   description MLAG Peer Link
   no autostate
{% if mlag_index | default(0) | int == 0 %}
   ip address {{ mlag_domain.peerlink_network | ansible.utils.ipaddr('address') }}/31
{% else %}
   ip address {{ mlag_domain.peerlink_network | ansible.utils.ipaddr('peer') }}/31
{% endif %}
!
mlag configuration
   domain-id {{ mlag_name | default('MLAG') }}
   local-interface Vlan{{ mlag_domain.peerlink_vlan | default(4094) }}
{% if mlag_index | default(0) | int == 0 %}
   peer-address {{ mlag_domain.peerlink_network | ansible.utils.ipaddr('peer') }}
{% else %}
   peer-address {{ mlag_domain.peerlink_network | ansible.utils.ipaddr('address') }}
{% endif %}
   peer-link Port-Channel{{ mlag_domain.peerlink_channel | default(2000) }}
   reload-delay mlag {{ mlag_domain.reload_delay_mlag | default(300) }}
   reload-delay non-mlag {{ mlag_domain.reload_delay_non_mlag | default(330) }}
!
{% endif %}
! # END mlag_domain.j2
```

**Variables:**
| Variable | Source | Example |
|----------|--------|---------|
| `mlag_domain.peerlink_vlan` | Config context | `4094` |
| `mlag_domain.peerlink_channel` | Config context | `2000` |
| `mlag_domain.peerlink_network` | Config context | `169.254.1.0/31` |
| `mlag_domain.interfaces` | Config context | `["eth3", "eth4"]` |
| `mlag_index` | Custom field | `0` or `1` |
| `mlag_name` | Custom field | `MLAG-PAIR-1` |

**Logic:** `mlag_index` determines which IP to use (0 = first, 1 = second in /31).

---

## BGP Templates

### bgp_underlay.j2

**Purpose:** BGP underlay for spine-leaf fabric.

```jinja2
! # START bgp_underlay.j2
!
! Underlay Loopbacks
!
interface Loopback0
   description Router ID / BGP Peering
   ip address {{ router_id }}/32
!
{% if device_role == 'leaf' and vtep_ip %}
interface Loopback1
   description VXLAN VTEP
   ip address {{ vtep_ip }}/32
!
{% endif %}
!
ip routing
!
router bgp {{ bgp_asn }}
   router-id {{ router_id }}
   update wait-install
   maximum-paths 8 ecmp 16
   no bgp default ipv4-unicast
   !
   ! Default peer groups for EVPN fabric
{% if device_role == 'leaf' %}
   neighbor SPINE_IPV4 peer group
   neighbor SPINE_IPV4 remote-as {{ fabric_defaults.spine_asn | default(65000) }}
   neighbor SPINE_IPV4 maximum-routes 12000
   neighbor SPINE_IPV4 send-community standard extended
   !
   neighbor SPINE_EVPN peer group
   neighbor SPINE_EVPN remote-as {{ fabric_defaults.spine_asn | default(65000) }}
   neighbor SPINE_EVPN update-source Loopback0
   neighbor SPINE_EVPN ebgp-multihop 3
   neighbor SPINE_EVPN maximum-routes 0
   neighbor SPINE_EVPN send-community standard extended
{% else %}
   neighbor LEAF_IPV4 peer group
   neighbor LEAF_IPV4 maximum-routes 12000
   neighbor LEAF_IPV4 send-community standard extended
   !
   neighbor LEAF_EVPN peer group
   neighbor LEAF_EVPN update-source Loopback0
   neighbor LEAF_EVPN ebgp-multihop 3
   neighbor LEAF_EVPN next-hop-unchanged
   neighbor LEAF_EVPN maximum-routes 0
   neighbor LEAF_EVPN send-community standard extended
{% endif %}
   !
   redistribute connected route-map REDISTRIBUTE_CONNECTED
   !
   !
   ! Underlay BGP Neighbors
{% for neighbor in bgp_neighbors %}
{% if device_role == 'leaf' %}
   neighbor {{ neighbor.peer_ip }} peer group SPINE_IPV4
{% else %}
   neighbor {{ neighbor.peer_ip }} peer group LEAF_IPV4
   neighbor {{ neighbor.peer_ip }} remote-as {{ neighbor.peer_asn }}
{% endif %}
   !
{% endfor %}
   !
{% if device_role == 'leaf' %}
   ! Leaf EVPN overlay peerings to spines
   !
   address-family evpn
      neighbor SPINE_EVPN activate
   !
{% endif %}
   address-family ipv4
{% if device_role == 'leaf' %}
      neighbor SPINE_IPV4 activate
      no neighbor SPINE_EVPN activate
{% else %}
      neighbor LEAF_IPV4 activate
      no neighbor LEAF_EVPN activate
{% endif %}
   !
!
! Route map for redistribution
route-map REDISTRIBUTE_CONNECTED permit 10
   match interface Loopback0
!
{% if device_role == 'leaf' %}
route-map REDISTRIBUTE_CONNECTED permit 20
   match interface Loopback1
!
{% endif %}
! # END bgp_underlay.j2
```

**Variables:**
| Variable | Source | Example |
|----------|--------|---------|
| `bgp_asn` | Custom field | `65001` |
| `router_id` | Custom field | `10.255.0.11` |
| `vtep_ip` | Custom field | `10.255.1.1` |
| `bgp_neighbors` | Calculated | See below |
| `device_role` | NetBox device role | `leaf` |

### bgp_neighbors Calculation

The `calculate_bgp_neighbors.j2` template computes peer IPs from interface IPs:

```jinja2
{% set neighbors = [] %}
{% for intf_name, intf_ip in interface_ips.items() %}
{% if intf_name is match('^eth[0-9]+$') %}
{% set ip_parts = intf_ip.split('/')[0].split('.') %}
{% set last_octet = ip_parts[3] | int %}
{# For /31 links: if even, peer is +1; if odd, peer is -1 #}
{% if last_octet % 2 == 0 %}
{% set peer_octet = last_octet + 1 %}
{% else %}
{% set peer_octet = last_octet - 1 %}
{% endif %}
{% set peer_ip = ip_parts[0] ~ '.' ~ ip_parts[1] ~ '.' ~ ip_parts[2] ~ '.' ~ peer_octet | string %}
{% set _ = neighbors.append({
    'peer_ip': peer_ip,
    'peer_asn': peer_asn,
    'local_interface': intf_name
}) %}
{% endif %}
{% endfor %}
{{ neighbors | to_yaml }}
```

---

## EVPN Overlay Template

### evpn_overlay.j2

**Purpose:** EVPN/VXLAN configuration for leaves.

```jinja2
! # START evpn_overlay.j2
{% if device_role == 'leaf' and tenants is defined %}
!
! VXLAN Interface
!
interface Vxlan1
   vxlan source-interface Loopback1
   vxlan udp-port 4789
{% if mlag_name is defined %}
   vxlan virtual-router encapsulation mac-address mlag-system-id
{% endif %}
{% for tenant in tenants %}
{% for segment in tenant.l2_segments | default([]) %}
   vxlan vlan {{ segment.vlan_number }} vni {{ (tenant.id | int * 10000) + segment.vlan_number }}
{% endfor %}
{% endfor %}
!
! Tenant VRFs
{% for tenant in tenants %}
!
vrf instance {{ tenant.name }}
   rd {{ router_id }}:{{ tenant.id }}
!
ip routing vrf {{ tenant.name }}
!
{% endfor %}
!
! Tenant VLANs and SVIs
{% for tenant in tenants %}
{% for segment in tenant.l2_segments | default([]) %}
!
vlan {{ segment.vlan_number }}
   name {{ segment.vlan_name }}
!
interface Vlan{{ segment.vlan_number }}
   description {{ segment.description | default(segment.vlan_name) }}
   vrf {{ tenant.name }}
   ip address virtual {{ segment.vlan_ip }}
{% if segment.mtu is defined %}
   mtu {{ segment.mtu }}
{% endif %}
!
{% endfor %}
{% endfor %}
!
! Anycast Gateway MAC
ip virtual-router mac-address {{ anycast_gateway_mac | default('00:1c:73:00:00:99') }}
!
{% endif %}
! # END evpn_overlay.j2
```

**Variables:**
| Variable | Source | Example |
|----------|--------|---------|
| `tenants` | Config context | See below |
| `router_id` | Custom field | `10.255.0.11` |
| `anycast_gateway_mac` | Config context | `00:1c:73:00:00:99` |

**Tenant Structure:**
```yaml
tenants:
  - name: "CORE"
    id: 1
    l2_segments:
      - vlan_number: 100
        vlan_name: "SERVERS"
        vlan_ip: "10.100.100.1/24"
        mtu: 9000
```

**VNI Calculation:** `(tenant.id * 10000) + vlan_number`
- Tenant 1, VLAN 100 → VNI 10100

---

## Interface Templates

### interfaces.j2

**Purpose:** Physical/logical interface configuration.

```jinja2
! # START interfaces.j2
{% for intf_name, intf_data in local_interfaces | default({}) | dictsort %}
!
interface {{ intf_name | arista_interface_name }}
{% if intf_data.description is defined %}
   description {{ intf_data.description }}
{% endif %}
{% if intf_data.mtu is defined %}
   mtu {{ intf_data.mtu }}
{% endif %}
{% if intf_data.switchport is defined and not intf_data.switchport %}
   no switchport
{% endif %}
{% if intf_data.ip_address is defined %}
   ip address {{ intf_data.ip_address }}
{% endif %}
!
{% endfor %}
! # END interfaces.j2
```

---

## Prefix Lists and Route Maps

### prefix_lists.j2

```jinja2
! # START prefix_lists.j2
{% for pl in prefix_lists | default([]) %}
!
ip prefix-list {{ pl.name }}
{% for entry in pl.entries | default([]) %}
   seq {{ entry.seq | default(loop.index * 10) }} {{ entry.action }} {{ entry.prefix }}{% if entry.le is defined %} le {{ entry.le }}{% endif %}{% if entry.ge is defined %} ge {{ entry.ge }}{% endif %}

{% endfor %}
!
{% endfor %}
! # END prefix_lists.j2
```

### route_maps.j2

```jinja2
! # START route_maps.j2
{% for rm in route_maps | default([]) %}
!
{% for entry in rm.entries | default([]) %}
route-map {{ rm.name }} {{ entry.action }} {{ entry.seq | default(loop.index * 10) }}
{% if entry.match is defined %}
{% for match in entry.match %}
   match {{ match }}
{% endfor %}
{% endif %}
{% if entry.set is defined %}
{% for set_cmd in entry.set %}
   set {{ set_cmd }}
{% endfor %}
{% endif %}
!
{% endfor %}
{% endfor %}
! # END route_maps.j2
```

---

## Custom Filters

### arista_interface_name

**Location:** `filter_plugins/arista_filters.py`

Converts short names to Arista format:

| Input | Output |
|-------|--------|
| `eth1` | `Ethernet1` |
| `eth10` | `Ethernet10` |
| `po1` | `Port-Channel1` |
| `lo0` | `Loopback0` |

```python
def arista_interface_name(name):
    if name.startswith('eth'):
        return 'Ethernet' + name[3:]
    if name.startswith('po'):
        return 'Port-Channel' + name[2:]
    if name.startswith('lo'):
        return 'Loopback' + name[2:]
    return name
```

---

**Next:** [[Troubleshooting]] - Common issues and solutions

