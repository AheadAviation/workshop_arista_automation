"""AHEAD ZTP Portal - NetBox plugin.

Self-service Zero-Touch Provisioning portal for Arista EVPN/VXLAN fabrics.
Ported from the "Bloom Mock Ups" Flask prototype and re-pointed to Arista cEOS.
"""
from netbox.plugins import PluginConfig

__version__ = "0.1.0"


class ZTPPortalConfig(PluginConfig):
    name = "netbox_ztp_portal"
    verbose_name = "AHEAD ZTP Portal"
    description = "Self-service Zero-Touch Provisioning for Arista EVPN/VXLAN fabrics"
    version = __version__
    author = "AHEAD"
    base_url = "ztp"
    min_version = "4.1.0"

    # Overridable via PLUGINS_CONFIG['netbox_ztp_portal'] in configuration/plugins.py.
    default_settings = {
        # The portal talks to NetBox's own REST API. Inside the netbox container
        # the app listens on :8080, so localhost:8080 is the loopback to self.
        "netbox_url": "http://localhost:8080",
        "netbox_token": "0123456789abcdef0123456789abcdef01234567",
        # Tag applied to everything the portal creates (so it can be listed/torn down).
        "self_portal_tag": "self-portal",
        # Tag the Ansible dynamic inventory + topology generator filter on.
        "containerlab_tag": "containerlab",
        # Serve mock IPAM instead of a real Infoblox WAPI.
        "use_mock_infoblox": True,
        # DeviceType / roles used for generated fabric nodes (must exist post-bootstrap).
        "ceos_device_type_slug": "ceos-lab",
        "ceos_device_type_model": "cEOS-lab",
        "manufacturer": "Arista",
    }


config = ZTPPortalConfig
