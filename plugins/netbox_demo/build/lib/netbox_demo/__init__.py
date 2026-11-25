"""NetBox Demo — NetBox Enterprise / Cloud driving a ContainerLab fabric.

The plugin runs inside NetBox Enterprise (or NetBox Cloud). It never holds
Docker or Kubernetes cluster-admin credentials and never reaches a switch
directly: every lab action goes to `lab-agent` over an allowlisted,
Bearer-token-authenticated HTTP boundary, and every device read goes through
that same proxy. Discovery flows the other way, from Orb Agent into Diode and
NetBox Assurance.

The module holding settings is `conf.py`, deliberately not `config.py`: a file
named `config.py` shadows the `config = NetBoxDemoConfig` line below and NetBox
then refuses to load the plugin with a confusing AttributeError.
"""

from netbox.plugins import PluginConfig

from .version import __version__


class NetBoxDemoConfig(PluginConfig):
    name = "netbox_demo"
    verbose_name = "NetBox Demo"
    description = "Run a ContainerLab / Clabernetes fabric from NetBox, with Discovery and Assurance"
    version = __version__
    author = "AHEAD"
    base_url = "demo"

    # Floor is 4.5 because Discovery and Assurance require it; ceiling matches
    # NetBox 4.7.x (NetBox Enterprise 2.5.x).
    min_version = "4.5.0"
    max_version = "4.7.99"

    default_settings = {
        # Lab agent base URL (in-cluster K8s Service or Tailscale host).
        "lab_agent_url": "http://100.64.0.1:8085",
        "lab_agent_token": "",
        # 300s accommodates full c9s deploy + cEOS eAPI readiness wait (~120-200s).
        "lab_agent_timeout": 300,
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
        # on 22 still works.
        "eapi_vrf": "MGMT",
        # True when EOS owns Management0. Set False on c9s where cEOS binds eth0
        # and never adopts Management0.
        "document_mgmt_interface": True,
    }

    def ready(self):
        super().ready()
        # Importing here, not at module scope, keeps NetBox's app registry fully
        # populated when these modules register signals and template extensions.
        from . import signals  # noqa: F401


config = NetBoxDemoConfig
