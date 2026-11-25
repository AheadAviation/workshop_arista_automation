"""Test bootstrap: let the plugin's pure modules import without NetBox installed.

`netbox_demo/__init__.py` imports `netbox.plugins.PluginConfig` and `conf.py`
imports `django.conf.settings`, which only exist inside a NetBox installation.
Stubbing both lets design, renderer, Day 2, and Assurance unit tests run cleanly
without booting Django.
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

if "django" not in sys.modules:
    django = types.ModuleType("django")
    django_conf = types.ModuleType("django.conf")
    django_conf.settings = types.SimpleNamespace(PLUGINS_CONFIG={})
    django.conf = django_conf
    sys.modules["django"] = django
    sys.modules["django.conf"] = django_conf
