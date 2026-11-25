"""Template extensions injecting Demo Source-of-Record actions onto core NetBox pages.

Auto-discovered by NetBox via `template_content.template_extensions`
(per netbox-plugin-development skill §7).
"""

from __future__ import annotations

from netbox.plugins import PluginTemplateExtension

from . import design, showcase


class DemoDeviceCard(PluginTemplateExtension):
    """Show quick demo links (Preview Config, Live Config, Drift, Topology) on demo Devices."""

    models = ["dcim.device"]

    def right_page(self):
        device = self.context.get("object")
        if device is None:
            return ""
        is_demo_switch = device.name in design.ALL_DEVICES
        in_demo_site = bool(device.site and device.site.slug == design.SITE_SLUG)
        if not (is_demo_switch or in_demo_site):
            return ""

        extra = {
            "device": device,
            "is_demo_switch": is_demo_switch,
            "role_name": design.role_for(device.name).capitalize() if is_demo_switch else "",
            "asn": design.asn_for(device.name) if is_demo_switch else None,
            "router_id": design.ROUTER_IDS.get(device.name, ""),
            "vtep_ip": design.VTEP_IPS.get(device.name, ""),
            "topology_available": showcase.topology_plugin_available(),
            "site_id": device.site_id,
        }
        return self.render("netbox_demo/inc/device_card.html", extra_context=extra)


template_extensions = [DemoDeviceCard]
