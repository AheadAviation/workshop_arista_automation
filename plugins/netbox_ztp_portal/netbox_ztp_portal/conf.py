"""Plugin configuration accessor.

Named `conf` (not `config`) because the package's __init__ exposes
`config = ZTPPortalConfig` - the PluginConfig NetBox requires - which would
otherwise shadow a submodule named `config` on `from . import config`.

Values come from PLUGINS_CONFIG['netbox_ztp_portal'] merged over the plugin's
default_settings, so a secret/token lives in NetBox's configuration.
"""
from netbox.plugins import get_plugin_config

_PLUGIN = "netbox_ztp_portal"


def _cfg(key):
    return get_plugin_config(_PLUGIN, key)


# Accessed as functions (not module constants) because plugin settings are only
# resolvable once Django/NetBox has finished loading.
def netbox_url():
    return (_cfg("netbox_url") or "").rstrip("/")


def netbox_token():
    return _cfg("netbox_token") or ""


def self_portal_tag():
    return _cfg("self_portal_tag") or "self-portal"


def containerlab_tag():
    return _cfg("containerlab_tag") or "containerlab"


def use_mock_infoblox():
    return bool(_cfg("use_mock_infoblox"))


def ceos_device_type_slug():
    return _cfg("ceos_device_type_slug") or "ceos-lab"


def ceos_device_type_model():
    return _cfg("ceos_device_type_model") or "cEOS-lab"


def manufacturer():
    return _cfg("manufacturer") or "Arista"


def netbox_configured():
    return bool(netbox_url() and netbox_token())
