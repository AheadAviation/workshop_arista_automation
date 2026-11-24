"""Settings for the Ops plugin, read from `PLUGINS_CONFIG['netbox_ops']`.

This module is deliberately called `conf.py` and not `config.py`. A file named `config.py`
inside the package shadows the `config = NetBoxOpsConfig` line in `__init__.py`, which is the
single most common way a NetBox plugin fails to load — and it fails with an `AttributeError`
that points nowhere useful.

Everything is read through `get_setting` so a missing key is a stated default rather than a
`KeyError` on a page the customer is watching.
"""

from __future__ import annotations

from typing import Any

from netbox.plugins import get_plugin_config

PLUGIN_NAME = "netbox_ops"


def get_setting(key: str, default: Any = None) -> Any:
    """Read one plugin setting, falling back to `default`.

    `get_plugin_config` returns `None` for a key that is absent from `PLUGINS_CONFIG` — it does
    not raise. An earlier version of this helper only caught an exception, so every unset key
    came back as `None` and, for example, the eAPI username became `None` and every request was
    rejected as a bad credential. Treating `None` as absent is the fix.
    """
    try:
        value = get_plugin_config(PLUGIN_NAME, key)
    except Exception:
        # A plugin that cannot read its own config must still render a page rather than a
        # 500. The page shows whatever it can and says what is missing.
        return default
    return default if value is None else value


def netbox_url() -> str:
    return str(get_setting("netbox_url", "http://localhost:8080")).rstrip("/")


def netbox_token() -> str:
    return str(get_setting("netbox_token", ""))


def docker_proxy_url() -> str:
    return str(get_setting("docker_proxy_url", "http://docker-proxy:2375")).rstrip("/")


def lab_runner_url() -> str:
    return str(get_setting("lab_runner_url", "http://lab-runner:8077")).rstrip("/")


def lab_topology() -> str:
    return str(get_setting("lab_topology", "clab/topology-demo.clab.yml"))


def awx_url() -> str:
    return str(get_setting("awx_url", "") or "").rstrip("/")


def awx_token() -> str:
    return str(get_setting("awx_token", "") or "")


def itsm_url() -> str:
    return str(get_setting("itsm_url", "") or "").rstrip("/")


def itsm_token() -> str:
    return str(get_setting("itsm_token", "") or "")


def demo_vlan() -> int:
    try:
        return int(get_setting("demo_vlan", 150))
    except (TypeError, ValueError):
        return 150


def eapi_username() -> str:
    return str(get_setting("eapi_username", "admin"))


def eapi_password() -> str:
    return str(get_setting("eapi_password", "admin"))


def device_addresses() -> dict[str, str]:
    configured = get_setting("device_addresses", {}) or {}
    return {str(k): str(v) for k, v in configured.items()} if isinstance(configured, dict) else {}


def device_address(device: str) -> str:
    """The management address for a device: explicit config first, then NetBox's own record.

    NetBox is the source of truth for device addressing, so asking it is the right default;
    the explicit map exists for a lab where a device's primary IP is not set.
    """
    explicit = device_addresses()
    if device in explicit:
        return explicit[device]

    try:
        import requests

        token = netbox_token()
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Token {token}"
        response = requests.get(
            f"{netbox_url()}/api/dcim/devices/?name={device}&limit=1",
            headers=headers,
            timeout=10,
        )
        if response.status_code == 200:
            results = (response.json() or {}).get("results") or []
            if results:
                primary = results[0].get("primary_ip4") or results[0].get("primary_ip") or {}
                address = str(primary.get("address") or "")
                if address:
                    return address.split("/")[0]
    except Exception:
        # Falling through to "" makes the caller raise a plain "no address known", which is
        # more useful on a page than a stack trace about NetBox being unreachable.
        return ""
    return ""


def demo_vlan_name() -> str:
    return str(get_setting("demo_vlan_name", "PHILIPS_MEDICAL"))


__all__ = [
    "PLUGIN_NAME",
    "awx_token",
    "awx_url",
    "demo_vlan",
    "demo_vlan_name",
    "device_address",
    "device_addresses",
    "docker_proxy_url",
    "eapi_password",
    "eapi_username",
    "get_setting",
    "itsm_token",
    "itsm_url",
    "lab_runner_url",
    "lab_topology",
    "netbox_token",
    "netbox_url",
]
