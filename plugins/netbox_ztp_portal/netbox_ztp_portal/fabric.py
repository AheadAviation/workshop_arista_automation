"""Arista EVPN/VXLAN spine-leaf fabric generator.

Pure, deterministic computation (no NetBox calls): given a fabric spec, produce
the full set of devices, interfaces, IP assignments and cables for a spine-leaf
fabric with MLAG leaf pairs - mirroring the schema the workshop's
`netbox_data/fabric_topology.yml` / `scripts/bootstrap_netbox.py` establishes,
so the existing Ansible pipeline renders configs for these devices unchanged.

Everything is offset by a per-fabric sequence number `seq` (DC1 is the reserved
seq=1; portal-built fabrics start at seq=2) so a new fabric never collides with
DC1's management IPs, loopbacks, point-to-point /31s, ASNs, or switch IDs.

The `custom_fields` emitted per device are exactly the ones
`inventory/netbox.yml` composes and `roles/arista_config` consumes:
bgp_asn, router_id, vtep_ip, switch_id, mlag_index, mlag_name, mlag_domain_id,
mlag_peer, fabric_name.
"""
import ipaddress

MGMT_PREFIXLEN = 24
FABRIC_TAGS = ("evpn-fabric",)  # containerlab + role tag added by siteplan


def _p2p_blocks(seq, count):
    """`count` successive /31s carved from 10.0.<seq-1>.0/24."""
    net = ipaddress.ip_network(f"10.0.{seq - 1}.0/24")
    subnets = list(net.subnets(new_prefix=31))
    if count > len(subnets):
        raise ValueError(f"fabric seq={seq} needs {count} /31 links, only {len(subnets)} available")
    return subnets[:count]


def build_fabric(site_code, num_spines=2, num_leaf_pairs=2, seq=2):
    """Return {'devices': [...], 'cables': [...], 'summary': {...}}.

    device = {name, role, mgmt_interface, mgmt_ip, switch_id, custom_fields,
              interfaces:[{name, type, ip?, role?, description?}]}
    cable  = {a_device, a_interface, b_device, b_interface, type}
    """
    site_l = site_code.strip().lower()
    fabric_name = f"{site_l}-fabric"
    num_leaves = num_leaf_pairs * 2

    # ASN scheme matches the workshop design and the EOS templates' defaults
    # (roles/arista_config/calculate_bgp_neighbors.j2: spine_asn|default(65000),
    # leaf_asn_base|default(65001)). Spine AS 65000; leaf pairs 65001, 65002, ...
    # These are reused across fabrics - DC1/DC2/DC3 are independent BGP domains
    # (no inter-fabric peering), and NetBox associates one ASN object with many
    # sites. Everything that MUST stay unique per fabric (mgmt IPs, loopbacks,
    # p2p /31s, VTEPs, switch IDs) is offset by seq below.
    spine_asn = 65000
    leaf_asn_base = 65000               # leaf pair k -> 65000 + k
    rid_octet = 2 * (seq - 1)          # DC1 -> 0
    vtep_octet = 2 * (seq - 1) + 1     # DC1 -> 1
    mgmt_spine_base = 10 + (seq - 1) * 20
    mgmt_leaf_base = 20 + (seq - 1) * 20
    sid_base = (seq - 1) * 20

    # Deterministic /31 allocation: spine-major over every spine<->leaf link.
    blocks = _p2p_blocks(seq, num_spines * num_leaves)

    def block_for(spine_idx, leaf_idx):
        return blocks[(spine_idx - 1) * num_leaves + (leaf_idx - 1)]

    devices = []
    cables = []
    mlag_pairs = []

    # ---- Spines -----------------------------------------------------------
    for i in range(1, num_spines + 1):
        name = f"{site_l}-spine-{i:02d}"
        rid = f"10.255.{rid_octet}.{i}"
        sid = sid_base + i
        mgmt = f"172.30.30.{mgmt_spine_base + i}/{MGMT_PREFIXLEN}"
        interfaces = [
            {"name": "Loopback0", "type": "virtual", "ip": f"{rid}/32",
             "description": "Router ID / BGP"},
            {"name": "Management0", "type": "1000base-t", "ip": mgmt,
             "description": "OOB management"},
        ]
        for j in range(1, num_leaves + 1):
            blk = block_for(i, j)
            # /31: address [0] is the spine end, [1] is the leaf end.
            interfaces.append({
                "name": f"eth{j}", "type": "10gbase-x-sfpp", "ip": f"{blk[0]}/31",
                "description": f"To {site_l}-leaf-{j:02d} eth{i}",
            })
        devices.append({
            "name": name, "role": "spine", "mgmt_interface": "Management0",
            "mgmt_ip": mgmt, "switch_id": sid,
            "custom_fields": {
                "bgp_asn": spine_asn, "router_id": rid, "switch_id": sid,
                "fabric_name": fabric_name,
            },
            "interfaces": interfaces,
        })

    # ---- Leaves (MLAG pairs) ---------------------------------------------
    for pair in range(1, num_leaf_pairs + 1):
        pair_asn = leaf_asn_base + pair
        domain_id = (seq - 1) * 10 + pair
        vtep = f"10.255.{vtep_octet}.{pair}"
        mlag_name = f"{site_code.upper()}-MLAG-{pair}"
        # MLAG peer-link details, mirroring scripts/bootstrap_netbox.py's
        # per-pair config-context. Link-local + iBGP-over-peerlink networks are
        # isolated per pair, so the DC1 scheme is reused (offset only per pair).
        pair_tag = f"{site_l}-mlag-{pair}"
        peerlink_ifaces = [f"eth{num_spines + 1}", f"eth{num_spines + 2}"]
        mlag_pairs.append({
            "tag": pair_tag,
            "mlag_domain": {
                "description": "MLAG PeerLink",
                "peerlink_vlan": 4094,
                "peerlink_channel": 2000,
                "peerlink_network": f"169.254.1.{(pair - 1) * 2}/31",
                "underlay_peering_network": f"100.64.0.{(pair - 1) * 2}/31",
                "ibgp_vlan": 4080,
                "interfaces": peerlink_ifaces,
            },
        })
        leaf_a_idx = (pair - 1) * 2 + 1   # 1-based global leaf index
        leaf_b_idx = leaf_a_idx + 1
        pair_leaves = [(leaf_a_idx, 0), (leaf_b_idx, 1)]  # (global idx, mlag_index)
        names = {idx: f"{site_l}-leaf-{idx:02d}" for idx, _ in pair_leaves}

        for leaf_idx, mlag_index in pair_leaves:
            name = names[leaf_idx]
            peer_name = names[leaf_b_idx if leaf_idx == leaf_a_idx else leaf_a_idx]
            rid = f"10.255.{rid_octet}.{10 + leaf_idx}"
            sid = sid_base + 10 + leaf_idx
            mgmt = f"172.30.30.{mgmt_leaf_base + leaf_idx}/{MGMT_PREFIXLEN}"
            interfaces = [
                {"name": "Loopback0", "type": "virtual", "ip": f"{rid}/32",
                 "description": "Router ID / BGP"},
                {"name": "Loopback1", "type": "virtual", "ip": f"{vtep}/32",
                 "description": "Shared VTEP (MLAG)"},
                {"name": "Management0", "type": "1000base-t", "ip": mgmt,
                 "description": "OOB management"},
            ]
            # Uplinks to each spine: leaf EthernetN -> spine-N.
            for i in range(1, num_spines + 1):
                blk = block_for(i, leaf_idx)
                # /31: address [1] is the leaf end (spine took [0]).
                interfaces.append({
                    "name": f"eth{i}", "type": "10gbase-x-sfpp", "ip": f"{blk[1]}/31",
                    "description": f"To {site_l}-spine-{i:02d} eth{leaf_idx}",
                })
                cables.append({
                    "a_device": f"{site_l}-spine-{i:02d}", "a_interface": f"eth{leaf_idx}",
                    "b_device": name, "b_interface": f"eth{i}", "type": None,
                })
            # MLAG peer-links (no IP): eth{ns+1}, eth{ns+2}.
            for k in (1, 2):
                pl = num_spines + k
                interfaces.append({
                    "name": f"eth{pl}", "type": "10gbase-x-sfpp",
                    "role": "mlag-peer", "description": f"MLAG peer-link to {peer_name}",
                })
            # Cable the peer-link once (from the primary leaf only, to avoid dupes).
            if mlag_index == 0:
                for k in (1, 2):
                    pl = num_spines + k
                    cables.append({
                        "a_device": name, "a_interface": f"eth{pl}",
                        "b_device": peer_name, "b_interface": f"eth{pl}", "type": None,
                    })

            devices.append({
                "name": name, "role": "leaf", "mgmt_interface": "Management0",
                "mgmt_ip": mgmt, "switch_id": sid, "mlag_pair_tag": pair_tag,
                "custom_fields": {
                    "bgp_asn": pair_asn, "router_id": rid, "vtep_ip": vtep,
                    "switch_id": sid, "mlag_index": mlag_index, "mlag_name": mlag_name,
                    "mlag_domain_id": domain_id, "mlag_peer": peer_name,
                    "fabric_name": fabric_name,
                },
                "interfaces": interfaces,
            })

    summary = {
        "site_code": site_code.upper(), "fabric_name": fabric_name, "seq": seq,
        "num_spines": num_spines, "num_leaf_pairs": num_leaf_pairs,
        "num_leaves": num_leaves, "spine_asn": spine_asn,
        "leaf_asns": [leaf_asn_base + p for p in range(1, num_leaf_pairs + 1)],
        "mgmt_range": f"172.30.30.{mgmt_spine_base + 1}-{mgmt_leaf_base + num_leaves}",
        "p2p_network": f"10.0.{seq - 1}.0/24",
        "device_count": num_spines + num_leaves,
        "cable_count": len(cables),
    }
    return {"devices": devices, "cables": cables, "mlag_pairs": mlag_pairs, "summary": summary}
