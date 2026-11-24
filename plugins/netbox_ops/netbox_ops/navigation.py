"""NetBox left-nav menu for the Ops plugin.

**Every item declares `permissions`.** NetBox's nav renderer does:

    if not user.has_perms(item.permissions):
        continue

and `PluginMenuItem.permissions` defaults to `[]`, for which `has_perms([])` is always `True`.
An item with no declared permission is therefore shown to *everyone*, including an anonymous
visitor — which is why this menu was visible on an unauthenticated login screen even after the
views themselves started refusing.

Declaring the permission is what removes it from that screen. It is also the honest declaration:
these are device operations, and `dcim.view_device` / `dcim.change_device` are the rights NetBox
already uses for the same actions.
"""

from netbox.plugins import PluginMenu, PluginMenuItem

#: Reads: the device pages, live config, and the records.
_VIEW = ["dcim.view_device"]

#: Writes: anything that changes a device, a ticket or the lab.
_CHANGE = ["dcim.change_device"]

_menu_items = (
    PluginMenuItem(link="plugins:netbox_ops:home", link_text="Devices", permissions=_VIEW),
    PluginMenuItem(link="plugins:netbox_ops:live", link_text="Live config", permissions=_VIEW),
    PluginMenuItem(link="plugins:netbox_ops:change", link_text="Day-two change", permissions=_CHANGE),
    PluginMenuItem(link="plugins:netbox_ops:labs", link_text="Container labs", permissions=_CHANGE),
    PluginMenuItem(link="plugins:netbox_ops:lifecycle", link_text="Lifecycle", permissions=_VIEW),
    PluginMenuItem(link="plugins:netbox_ops:awx", link_text="AWX jobs", permissions=_VIEW),
    PluginMenuItem(link="plugins:netbox_ops:tickets", link_text="ITSM tickets", permissions=_VIEW),
    PluginMenuItem(link="plugins:netbox_ops:audit", link_text="Audit trail", permissions=_VIEW),
)

menu = PluginMenu(
    label="Network Ops",
    groups=(("Operations", _menu_items),),
    icon_class="mdi mdi-cog-sync",
)
