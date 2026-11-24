"""NetBox Demo — a cloud-hosted NetBox driving a local ContainerLab fabric.

The plugin runs inside NetBox Cloud. It never holds Docker credentials and never
reaches a switch directly: every lab action goes to `lab-agent` on the local host
over Tailscale, and every device read goes through that same allowlisted proxy.
Discovery flows the other way, from a local Orb Agent into NetBox Cloud.

The module holding settings is `conf.py`, deliberately not `config.py`: a file
named `config.py` shadows the `config = NetBoxDemoConfig` line below and NetBox
then refuses to load the plugin with a confusing AttributeError.
"""

from netbox.plugins import PluginConfig

__version__ = "0.1.0"


class NetBoxDemoConfig(PluginConfig):
    name = "netbox_demo"
    verbose_name = "NetBox Demo"
    description = "Run a local ContainerLab fabric from NetBox Cloud, with discovery and assurance"
    version = __version__
    author = "AHEAD"
    base_url = "demo"

    # NetBox Cloud runs 4.6.x; the floor is 4.5 because Discovery and Assurance
    # both require it, and the ceiling is left open within 4.x.
    min_version = "4.5.0"
    max_version = "4.9.99"

    default_settings = {
        # The local lab agent, reached over Tailscale. Never a public address.
        "lab_agent_url": "http://100.64.0.1:8085",
        "lab_agent_token": "",
        "lab_agent_timeout": 120,
        # The site this plugin owns. Every destructive action is scoped to it.
        "site_slug": "netbox-demo",
        # Where to send an operator who wants to review drift.
        "assurance_deviations_url": "/plugins/assurance/deviations/",
        # Defaults the standard-baseline tab offers.
        "default_ssid_vlan": 0,
        "verify_tls": True,
        # VRF that eAPI is bound to. "MGMT" is the correct production posture and
        # what local ContainerLab uses. Set to "" when the fabric runs on
        # Kubernetes via c9s: eAPI bound to a VRF lands in a network namespace c9s
        # does not interpose, so the switch becomes unreachable on 443 while SSH
        # on 22 still works. That is a maddening symptom, and pushing a config
        # that re-binds eAPI into MGMT would break the demo's own control channel.
        "eapi_vrf": "MGMT",
        # True when the hosting platform owns the management interface and EOS
        # does not adopt it. On c9s, cEOS binds eth0 (MGMT_INTF=eth0) and never
        # adopts Management0, so `show ip interface brief` is empty and the
        # management address is invisible to EOS. Documenting that interface in
        # NetBox then creates drift nothing can ever clear, because the switch
        # will never report it. Set False (the platform-managed case) to leave it
        # out of NetBox, out of rendered config, and out of drift comparison.
        "document_mgmt_interface": True,
    }

    def ready(self):
        super().ready()
        # Importing here, not at module scope, keeps NetBox's app registry fully
        # populated when these modules register signals and template extensions.
        from . import signals  # noqa: F401


config = NetBoxDemoConfig
