"""NetBox left-nav menu for the Ops plugin.

Every link here must reverse. A `PluginMenuItem` pointing at a name that does not resolve
raises `NoReverseMatch` and breaks **every** page in NetBox, not just this plugin's — so each
tab has its own named route in `urls.py` rather than a query string or a route argument.
NetBox 4.1's `PluginMenuItem` signature is `(link, link_text, staff_only, permissions,
buttons)`: there is no `link_args`, so a parameterised route cannot be linked from here.
"""
from netbox.plugins import PluginMenu, PluginMenuItem

_menu_items = (
    PluginMenuItem(link="plugins:netbox_ops:home", link_text="Devices"),
    PluginMenuItem(link="plugins:netbox_ops:live", link_text="Live config"),
    PluginMenuItem(link="plugins:netbox_ops:change", link_text="Day-two change"),
    PluginMenuItem(link="plugins:netbox_ops:labs", link_text="Container labs"),
    PluginMenuItem(link="plugins:netbox_ops:lifecycle", link_text="Lifecycle"),
    PluginMenuItem(link="plugins:netbox_ops:awx", link_text="AWX jobs"),
    PluginMenuItem(link="plugins:netbox_ops:tickets", link_text="ITSM tickets"),
    PluginMenuItem(link="plugins:netbox_ops:audit", link_text="Audit trail"),
)

menu = PluginMenu(
    label="Network Ops",
    groups=(("Operations", _menu_items),),
    icon_class="mdi mdi-cog-sync",
)
