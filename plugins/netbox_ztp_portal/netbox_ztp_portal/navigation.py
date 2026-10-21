"""NetBox left-nav menu for the AHEAD ZTP Portal."""
from netbox.plugins import PluginMenu, PluginMenuItem

_items = (
    PluginMenuItem(
        link="plugins:netbox_ztp_portal:index",
        link_text="Overview",
    ),
    PluginMenuItem(
        link="plugins:netbox_ztp_portal:architect",
        link_text="New Site (Fabric)",
    ),
    PluginMenuItem(
        link="plugins:netbox_ztp_portal:datacenter",
        link_text="New Block (Onboard)",
    ),
    PluginMenuItem(
        link="plugins:netbox_ztp_portal:tracking",
        link_text="Fleet Tracking",
    ),
)

menu = PluginMenu(
    label="AHEAD ZTP",
    groups=(("Zero-Touch Provisioning", _items),),
    icon_class="mdi mdi-lan-connect",
)
