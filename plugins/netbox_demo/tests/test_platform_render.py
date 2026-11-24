"""Platform-dependent rendering (INIT-001/SPEC-005).

Two settings exist because the same fabric runs on two platforms whose kernels
disagree with EOS about what a management interface is. Both bugs they prevent
were found in the field, not by reading code:

* eAPI bound into `vrf MGMT` becomes unreachable on Kubernetes (c9s does not
  interpose the VRF namespace), while SSH on 22 keeps working. Pushing that config
  would kill the demo's own control channel mid-run.
* cEOS on c9s binds eth0 and never adopts Management0, so a rendered config for
  Management0 targets an interface the switch does not have.

These tests pin the rendering for both platforms.
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "netbox_demo"))

from netbox_demo import design, renderer  # noqa: E402
from netbox_demo.fabric import DeviceIntent, FabricInterface, Peer  # noqa: E402


def _intent(hostname: str = "demo-spine-01") -> DeviceIntent:
    return DeviceIntent(
        hostname=hostname,
        role=design.role_for(hostname),
        asn=design.asn_for(hostname),
        router_id=design.ROUTER_IDS[hostname],
        mgmt_address=f"{design.MGMT_IPS[hostname]}/24",
        mgmt_gateway=design.MGMT_GATEWAY,
        fabric_interfaces=[
            FabricInterface(
                name=link.left_port,
                description=link.description,
                address=link.left_address,
            )
            for link in design.FABRIC_LINKS
            if link.left == hostname
        ],
        peers=[
            Peer(
                address=link.right_address.split("/")[0],
                remote_as=design.asn_for(link.right),
                description=f"to {link.right}",
                peer_group="LEAF_IPV4",
            )
            for link in design.FABRIC_LINKS
            if link.left == hostname
        ],
    )


# --------------------------------------------------------------------------- #
# eAPI VRF binding
# --------------------------------------------------------------------------- #


def test_local_platform_binds_eapi_into_mgmt_vrf():
    """The production posture, and what local ContainerLab needs."""
    cfg = renderer.render_device(_intent(), eapi_vrf="MGMT")
    block = _management_api_block(cfg)
    assert "vrf MGMT" in block


def test_kubernetes_platform_leaves_eapi_in_the_default_vrf():
    """c9s does not interpose the VRF namespace, so 443 stops answering."""
    cfg = renderer.render_device(_intent(), eapi_vrf="")
    block = _management_api_block(cfg)
    assert "vrf MGMT" not in block
    assert "protocol https" in block
    assert "no shutdown" in block


def _management_api_block(cfg: str) -> str:
    """The lines belonging to `management api http-commands`, exclusively."""
    lines = cfg.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "management api http-commands")
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line.startswith(" "):
            block.append(line)
        else:
            break
    return "\n".join(block)


def test_eapi_vrf_setting_does_not_leak_into_the_management_route():
    """Only the eAPI listener moves; management reachability still uses the VRF."""
    cfg = renderer.render_device(_intent(), eapi_vrf="")
    assert f"ip route vrf MGMT 0.0.0.0/0 {design.MGMT_GATEWAY}" in cfg
    assert "vrf instance MGMT" in cfg


def test_default_arguments_preserve_the_local_behaviour():
    """Existing local labs must render byte-identically after this change."""
    explicit = renderer.render_device(_intent(), eapi_vrf="MGMT", mgmt_interface=True)
    default = renderer.render_device(_intent())
    assert explicit == default


# --------------------------------------------------------------------------- #
# Management interface ownership
# --------------------------------------------------------------------------- #


def test_local_platform_configures_management0():
    cfg = renderer.render_device(_intent(), mgmt_interface=True)
    assert "interface Management0" in cfg
    assert f"ip address {design.MGMT_IPS['demo-spine-01']}/24" in cfg


def test_kubernetes_platform_does_not_configure_management0():
    """cEOS binds eth0 there; Management0 is never adopted, so it is not rendered."""
    cfg = renderer.render_device(_intent(), mgmt_interface=False)
    assert "interface Management0" not in cfg
    assert f"ip address {design.MGMT_IPS['demo-spine-01']}/24" not in cfg


def test_kubernetes_platform_explains_the_omission_in_the_config():
    """A reader of the generated config should know why it is absent."""
    cfg = renderer.render_device(_intent(), mgmt_interface=False)
    assert "platform-managed" in cfg


def test_management_route_survives_interface_omission():
    cfg = renderer.render_device(_intent(), mgmt_interface=False)
    assert f"ip route vrf MGMT 0.0.0.0/0 {design.MGMT_GATEWAY}" in cfg


def test_omitting_management0_does_not_affect_fabric_config():
    """Only the management section changes; the fabric is identical."""
    with_iface = renderer.render_device(_intent(), mgmt_interface=True)
    without = renderer.render_device(_intent(), mgmt_interface=False)
    for expected in ("router bgp", "interface Loopback0", "snmp-server community"):
        assert expected in with_iface
        assert expected in without


# --------------------------------------------------------------------------- #
# Both settings together (the c9s profile)
# --------------------------------------------------------------------------- #


def test_kubernetes_profile_passes_the_sanity_check():
    cfg = renderer.render_device(_intent(), eapi_vrf="", mgmt_interface=False)
    problems = renderer.sanity_check({"demo-spine-01": cfg})
    assert problems == []


def test_kubernetes_profile_still_has_eapi_and_bgp():
    """The switch must remain reachable and configurable after the push."""
    cfg = renderer.render_device(_intent(), eapi_vrf="", mgmt_interface=False)
    assert "management api http-commands" in cfg
    assert "protocol https" in cfg
    assert "router bgp" in cfg


def test_render_all_threads_the_platform_options():
    intents = {name: _intent(name) for name in design.SPINES}
    configs = renderer.render_all(intents, eapi_vrf="", mgmt_interface=False)
    assert set(configs) == set(design.SPINES)
    for cfg in configs.values():
        assert "vrf MGMT" not in _management_api_block(cfg)
        assert "interface Management0" not in cfg


def test_ssh_stays_bound_to_mgmt_even_on_kubernetes():
    """Only eAPI moves. SSH in MGMT is what still works on c9s (22 is interposed),
    so it must not be collateral damage of the eAPI fix."""
    cfg = renderer.render_device(_intent(), eapi_vrf="", mgmt_interface=False)
    ssh_block = cfg.split("management ssh", 1)[1].split("management console", 1)[0]
    assert "vrf MGMT" in ssh_block
