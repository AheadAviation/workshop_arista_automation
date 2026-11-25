"""Tests for the deterministic fabric design and the EOS renderer.

No NetBox, no database, no switches: these cover the arithmetic and the exact
bytes, which is where a demo silently breaks.
"""

from __future__ import annotations

import ipaddress
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "netbox_demo"))

from netbox_demo import design, renderer  # noqa: E402
from netbox_demo.fabric import (  # noqa: E402
    DeviceIntent,
    FabricInterface,
    MlagDomain,
    Peer,
    ServerPort,
    TenantVlan,
)


# --------------------------------------------------------------------------- #
# Design / addressing
# --------------------------------------------------------------------------- #


def test_link_allocation_is_deterministic():
    assert design.FABRIC_LINKS == design._build_fabric_links()


def test_every_device_has_two_spine_links():
    for leaf in design.LEAVES:
        leaf_links = [link for link in design.FABRIC_LINKS if link.right == leaf]
        assert len(leaf_links) == len(design.SPINES) == 2
        assert {link.left for link in leaf_links} == set(design.SPINES)


def test_link_count_is_full_mesh_between_spines_and_leaves():
    assert len(design.FABRIC_LINKS) == len(design.SPINES) * len(design.LEAVES) == 8


def test_link_addresses_are_distinct_and_inside_underlay():
    block = ipaddress.ip_network(design.UNDERLAY_SUBNET)
    seen: set[str] = set()
    for link in design.FABRIC_LINKS:
        for addr in (link.left_address, link.right_address):
            ip = ipaddress.ip_interface(addr)
            assert ip.ip in block, addr
            assert ip.network.prefixlen == 31
            assert str(ip.ip) not in seen, f"duplicate address {ip.ip}"
            seen.add(str(ip.ip))
    assert len(seen) == len(design.FABRIC_LINKS) * 2


def test_both_ends_of_a_link_share_a_subnet():
    for link in design.FABRIC_LINKS:
        left = ipaddress.ip_interface(link.left_address)
        right = ipaddress.ip_interface(link.right_address)
        assert left.network == right.network
        assert left.ip != right.ip


def test_asns_are_unique_per_leaf_and_shared_by_spines():
    spine_asns = {design.asn_for(s) for s in design.SPINES}
    assert spine_asns == {design.SPINE_ASN}
    leaf_asns = [design.asn_for(leaf) for leaf in design.LEAVES]
    assert len(set(leaf_asns)) == len(leaf_asns)
    assert not set(leaf_asns) & spine_asns


def test_router_ids_and_vteps_are_unique():
    assert len(set(design.ROUTER_IDS.values())) == len(design.ALL_DEVICES)
    assert len(set(design.VTEP_IPS.values())) == len(design.LEAVES)
    assert not set(design.VTEP_IPS.values()) & set(design.ROUTER_IDS.values())


def test_spines_have_no_vtep():
    for spine in design.SPINES:
        assert spine not in design.VTEP_IPS


def test_mgmt_net_does_not_collide_with_the_workshop_lab():
    assert not ipaddress.ip_network(design.MGMT_SUBNET).overlaps(
        ipaddress.ip_network("172.30.30.0/24")
    )


def test_mlag_pairs_are_symmetric():
    for device in design.LEAVES:
        pair = design.mlag_for(device)
        assert pair is not None, f"{device} has no MLAG pair"
        domain_id, local, peer = pair
        peer_pair = design.mlag_for(
            next(
                other
                for a, b, _ in design.MLAG_PAIRS
                for other in (a, b)
                if other != device and device in (a, b)
            )
        )
        assert peer_pair is not None
        assert peer_pair[0] == domain_id
        # Each side points at the other side's address.
        assert peer_pair[1].split("/")[0] == peer
        assert peer_pair[2] == local.split("/")[0]


def test_vni_derivation():
    assert design.vni_for_vlan(100) == 10100
    assert design.vni_for_vlan(999) == design.BASE_L2_VNI + 999


# --------------------------------------------------------------------------- #
# Intent fixtures
# --------------------------------------------------------------------------- #


def _leaf_intent(hostname: str = "demo-leaf-01") -> DeviceIntent:
    mlag = design.mlag_for(hostname)
    domain_id, local, peer = mlag
    return DeviceIntent(
        hostname=hostname,
        role="leaf",
        asn=design.asn_for(hostname),
        router_id=design.ROUTER_IDS[hostname],
        mgmt_address=f"{design.MGMT_IPS[hostname]}/24",
        mgmt_gateway=design.MGMT_GATEWAY,
        vtep_address=design.VTEP_IPS[hostname],
        fabric_interfaces=[
            FabricInterface(
                name=link.right_port,
                description=link.description,
                address=link.right_address,
                mtu=design.UNDERLAY_MTU,
            )
            for link in design.FABRIC_LINKS
            if link.right == hostname
        ],
        peers=[
            Peer(
                address=link.left_address.split("/")[0],
                remote_as=design.SPINE_ASN,
                description=f"to {link.left}",
                peer_group="SPINE_IPV4",
            )
            for link in design.FABRIC_LINKS
            if link.right == hostname
        ],
        vlans=[
            TenantVlan(
                vlan_id=seed.vlan_id,
                name=seed.name,
                vni=seed.vni,
                svi_address=seed.svi,
                mtu=seed.mtu,
            )
            for seed in design.SEED_VLANS
        ],
        server_ports=[
            ServerPort(
                name=design.SERVER_PORT,
                description="Server-Port",
                mode="trunk",
                trunk_vlans=(100, 110),
            )
        ],
        mlag=MlagDomain(
            domain_id=domain_id,
            peer_address=peer,
            local_address=local,
            peer_link_members=design.MLAG_PEER_PORTS[hostname],
        ),
    )


def _spine_intent() -> DeviceIntent:
    host = design.SPINES[0]
    return DeviceIntent(
        hostname=host,
        role="spine",
        asn=design.asn_for(host),
        router_id=design.ROUTER_IDS[host],
        mgmt_address=f"{design.MGMT_IPS[host]}/24",
        mgmt_gateway=design.MGMT_GATEWAY,
        fabric_interfaces=[
            FabricInterface(
                name=link.left_port,
                description=link.description,
                address=link.left_address,
                mtu=design.UNDERLAY_MTU,
            )
            for link in design.FABRIC_LINKS
            if link.left == host
        ],
        peers=[
            Peer(
                address=link.right_address.split("/")[0],
                remote_as=design.asn_for(link.right),
                description=f"to {link.right}",
                peer_group="LEAF_IPV4",
            )
            for link in design.FABRIC_LINKS
            if link.left == host
        ],
    )


# --------------------------------------------------------------------------- #
# Renderer: leaf
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def leaf_config() -> str:
    return renderer.render_device(_leaf_intent())


def test_leaf_sets_identity(leaf_config):
    assert "hostname demo-leaf-01" in leaf_config
    assert f"router bgp {design.asn_for('demo-leaf-01')}" in leaf_config
    assert f"router-id {design.ROUTER_IDS['demo-leaf-01']}" in leaf_config
    assert "end\n" in leaf_config


def test_leaf_has_management_and_eapi(leaf_config):
    assert "interface Management0" in leaf_config
    assert "ip address 172.31.30.21/24" in leaf_config
    assert "management api http-commands" in leaf_config


def test_leaf_has_loopbacks_including_vtep_source(leaf_config):
    assert "interface Loopback0" in leaf_config
    assert "ip address 10.255.0.11/32" in leaf_config
    assert "interface Loopback1" in leaf_config
    assert "ip address 10.255.1.1/32" in leaf_config


def test_leaf_has_vxlan_with_every_vlan(leaf_config):
    assert "interface Vxlan1" in leaf_config
    assert "vxlan source-interface Loopback1" in leaf_config
    for seed in design.SEED_VLANS:
        assert f"vxlan vlan {seed.vlan_id} vni {seed.vni}" in leaf_config


def test_leaf_has_anycast_gateway_on_each_svi(leaf_config):
    for seed in design.SEED_VLANS:
        assert f"interface Vlan{seed.vlan_id}" in leaf_config
        assert f"ip address virtual {seed.svi}" in leaf_config
    assert f"ip virtual-router mac-address {design.ANYCAST_GATEWAY_MAC}" in leaf_config


def test_leaf_has_mlag(leaf_config):
    assert "mlag configuration" in leaf_config
    assert "domain-id NETBOX-DEMO-1" in leaf_config
    assert "peer-address 169.254.1.2" in leaf_config
    assert "interface Port-Channel2000" in leaf_config


def test_leaf_has_evpn_route_targets(leaf_config):
    assert "address-family evpn" in leaf_config
    for seed in design.SEED_VLANS:
        assert f"route-target both {seed.vni}:{seed.vlan_id}" in leaf_config


def test_leaf_has_standard_services(leaf_config):
    assert "snmp-server community FABRIC-RO ro" in leaf_config
    assert "logging host 172.31.30.1 514" in leaf_config
    assert "ntp server vrf MGMT 172.31.30.1" in leaf_config
    assert "lldp run" in leaf_config
    assert "banner login" in leaf_config


def test_leaf_config_is_deterministic(leaf_config):
    assert leaf_config == renderer.render_device(_leaf_intent())
    assert leaf_config.endswith("\n")
    assert "\n\n\n" not in leaf_config
    assert "\t" not in leaf_config


# --------------------------------------------------------------------------- #
# Renderer: spine
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def spine_config() -> str:
    return renderer.render_device(_spine_intent())


def test_spine_has_no_vxlan_or_mlag_or_tenant_vlans(spine_config):
    assert "interface Vxlan1" not in spine_config
    assert "mlag configuration" not in spine_config
    assert "interface Vlan100" not in spine_config
    assert "address-family evpn" not in spine_config


def test_spine_has_loopback0_but_no_vtep_loopback(spine_config):
    assert "interface Loopback0" in spine_config
    assert "interface Loopback1" not in spine_config


def test_spine_peers_to_every_leaf(spine_config):
    for link in design.FABRIC_LINKS:
        if link.left == design.SPINES[0]:
            assert f"neighbor {link.right_address.split('/')[0]}" in spine_config


def test_spine_still_carries_standard_services(spine_config):
    assert "snmp-server community FABRIC-RO ro" in spine_config
    assert "lldp run" in spine_config


# --------------------------------------------------------------------------- #
# Sanity check
# --------------------------------------------------------------------------- #


def test_sanity_check_accepts_rendered_configs():
    configs = {
        "demo-leaf-01": renderer.render_device(_leaf_intent()),
        design.SPINES[0]: renderer.render_device(_spine_intent()),
    }
    assert renderer.sanity_check(configs) == []


def test_sanity_check_catches_wrong_hostname():
    problems = renderer.sanity_check({"demo-leaf-01": "hostname something-else\nrouter bgp 1\n"})
    assert any("does not set hostname" in p for p in problems)


def test_sanity_check_catches_empty_and_no_bgp():
    assert renderer.sanity_check({"a": "   "})
    assert any("no BGP" in p for p in renderer.sanity_check({"a": "hostname a\n"}))


def test_sanity_check_catches_tabs():
    problems = renderer.sanity_check({"a": "hostname a\nrouter bgp 1\n\tinterface x\n"})
    assert any("tab" in p for p in problems)


def test_render_all_keys_by_hostname():
    intents = {"demo-leaf-01": _leaf_intent(), design.SPINES[0]: _spine_intent()}
    configs = renderer.render_all(intents)
    assert set(configs) == set(intents)
    for host, cfg in configs.items():
        assert f"hostname {host}" in cfg
