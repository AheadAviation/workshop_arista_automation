"""NetBox Ops — the day-two operations plugin (INIT-002).

Puts the whole day-two story on one page inside NetBox: devices and whether they match
intent, the container labs, the change flow with its approval and audit, the AWX jobs, and
the ITSM tickets.

The engine is `workbench/`, which already does the comparison, the preview-without-apply
executor, the approval gate and the audit record. This package is the surface; it does not
reimplement any of that.

Named `netbox_ops` and **not** `config.py`-adjacent: the module that holds settings is
`conf.py`, because a file called `config.py` shadows the `config = PluginConfig` line below and
NetBox will refuse to load the plugin with a confusing `AttributeError`.
"""

from netbox.plugins import PluginConfig

__version__ = "0.1.0"


class NetBoxOpsConfig(PluginConfig):
    name = "netbox_ops"
    verbose_name = "Network Ops"
    description = "Day-two operations: devices, labs, changes, approvals and audit, in NetBox"
    version = __version__
    author = "AHEAD"
    base_url = "ops"

    # NetBox 4.1.x. Widened upward only after testing on a later image; see COMPATIBILITY.
    min_version = "4.1.0"
    max_version = "4.2.99"

    default_settings = {
        # Where the workbench engine finds its lab. Matches the repo's own defaults so the
        # plugin and `python -m workbench.cli` see the same world.
        "netbox_url": "http://localhost:8080",
        "netbox_token": "0123456789abcdef0123456789abcdef01234567",
        # The scoped Docker proxy. Never a raw socket path -- see SPEC-002 and the security
        # note in the initiative: a hospital CISO should not watch NetBox hold the host keys.
        "docker_proxy_url": "http://docker-proxy:2375",
        # The containerlab topology the lab page drives.
        "lab_topology": "clab/topology-demo.clab.yml",
        # AWX and Zammad. Empty means "not configured", and the page says so rather than
        # showing an empty list that reads like "nothing to see".
        "awx_url": "",
        "awx_token": "",
        "itsm_url": "",
        "itsm_token": "",
        # Default VLAN the day-two demo adds. 150 is the Philips change from the briefing.
        "demo_vlan": 150,
        "demo_vlan_name": "PHILIPS_MEDICAL",
    }


config = NetBoxOpsConfig
