"""Navigation menu for netbox_demo (INIT-001/SPEC-002).

Every `link` here must be a URL name that `urls.py` actually registers, and the
reverse happens at import time, so a typo fails loudly rather than rendering a
dead link.
"""

from netbox.plugins import PluginMenu, PluginMenuItem

menu = PluginMenu(
    label="NetBox Demo",
    groups=(
        (
            "Demo",
            (
                PluginMenuItem(
                    link="plugins:netbox_demo:runbook",
                    link_text="Runbook",
                    permissions=["netbox_demo.view_labjob"],
                ),
                PluginMenuItem(
                    link="plugins:netbox_demo:drift",
                    link_text="Drift",
                    permissions=["netbox_demo.view_labjob"],
                ),
                PluginMenuItem(
                    link="plugins:netbox_demo:day2",
                    link_text="Day 2 changes",
                    permissions=["netbox_demo.view_stagedchange"],
                ),
            ),
        ),
        (
            "Fabric",
            (
                PluginMenuItem(
                    link="plugins:netbox_demo:preview",
                    link_text="Config preview",
                    permissions=["netbox_demo.view_labjob"],
                ),
                PluginMenuItem(
                    link="plugins:netbox_demo:live",
                    link_text="Live config",
                    permissions=["netbox_demo.view_labjob"],
                ),
                PluginMenuItem(
                    link="plugins:netbox_demo:job_list",
                    link_text="Jobs",
                    permissions=["netbox_demo.view_labjob"],
                ),
            ),
        ),
    ),
    icon_class="mdi mdi-flask-outline",
)
