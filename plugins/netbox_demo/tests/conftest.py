"""Test bootstrap: let the plugin's pure modules import without NetBox installed.

`netbox_demo/__init__.py` imports `netbox.plugins.PluginConfig`, which only exists
inside a NetBox installation. The design, renderer and Day 2 modules have no
NetBox dependency at all, and testing them without a NetBox container is the
point — so stub the one import that needs it.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "netbox" not in sys.modules:
    netbox = types.ModuleType("netbox")
    plugins = types.ModuleType("netbox.plugins")

    class PluginConfig:  # noqa: D401 - minimal stand-in
        """Stand-in for NetBox's PluginConfig."""

        def __init__(self, *args, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        def ready(self):
            pass

    plugins.PluginConfig = PluginConfig
    netbox.plugins = plugins
    sys.modules["netbox"] = netbox
    sys.modules["netbox.plugins"] = plugins
