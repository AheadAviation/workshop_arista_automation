"""NetBox left-nav menu for the AHEAD ZTP Portal.

**Every item declares `permissions`.** NetBox's nav renderer does:

    if not user.has_perms(item.permissions):
        continue

and `PluginMenuItem.permissions` defaults to `[]`, for which `has_perms([])` is always `True`.
An item with no declared permission is shown to *everyone*, including an anonymous visitor. That
is why this menu was still visible on an unauthenticated login screen after the views themselves
began refusing — the views were fixed, the menu was not.

Every page in this portal writes devices into the source of truth, so every item requires
`dcim.change_device`, matching what the views require.
"""

from netbox.plugins import PluginMenu, PluginMenuItem

#: All four pages lead to writing fabrics/devices into NetBox.
_CHANGE = ["dcim.change_device"]

_items = (
    PluginMenuItem(
        link="plugins:netbox_ztp_portal:index",
        link_text="Overview",
        permissions=_CHANGE,
    ),
    PluginMenuItem(
        link="plugins:netbox_ztp_portal:architect",
        link_text="New Site (Fabric)",
        permissions=_CHANGE,
    ),
    PluginMenuItem(
        link="plugins:netbox_ztp_portal:datacenter",
        link_text="New Block (Onboard)",
        permissions=_CHANGE,
    ),
    PluginMenuItem(
        link="plugins:netbox_ztp_portal:tracking",
        link_text="Fleet Tracking",
        permissions=_CHANGE,
    ),
)

menu = PluginMenu(
    label="AHEAD ZTP",
    groups=(("Zero-Touch Provisioning", _items),),
    icon_class="mdi mdi-lan-connect",
)
