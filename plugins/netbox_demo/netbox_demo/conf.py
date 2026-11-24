"""Plugin settings access (INIT-001/SPEC-002).

One accessor per setting, so a missing or blank value fails with a message that
names the setting rather than a KeyError three frames deep.
"""

from __future__ import annotations

from django.conf import settings


def _plugin_settings() -> dict:
    return getattr(settings, "PLUGINS_CONFIG", {}).get("netbox_demo", {}) or {}


def _get(key: str, default=None):
    plugins = getattr(settings, "PLUGINS_CONFIG", {}) or {}
    if key in plugins:
        return plugins[key]
    return _plugin_settings().get(key, default)


def lab_agent_url() -> str:
    """Base URL of the local lab agent, e.g. http://100.89.183.114:8085."""
    return str(_get("lab_agent_url", "")).rstrip("/")


def lab_agent_token() -> str:
    return str(_get("lab_agent_token", ""))


def lab_agent_timeout() -> int:
    try:
        return int(_get("lab_agent_timeout", 120))
    except (TypeError, ValueError):
        return 120


def site_slug() -> str:
    return str(_get("site_slug", "netbox-demo"))


def assurance_deviations_url() -> str:
    return str(_get("assurance_deviations_url", "/plugins/assurance/deviations/"))


def verify_tls() -> bool:
    value = _get("verify_tls", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off")
    return bool(value)


def eapi_vrf() -> str:
    """VRF eAPI is bound to. Empty means the default VRF (required on c9s)."""
    value = _get("eapi_vrf", "MGMT")
    return "" if value is None else str(value).strip()


def document_mgmt_interface() -> bool:
    """Whether EOS adopts the management interface on this platform.

    False on c9s, where cEOS binds eth0 and never adopts Management0. Documenting
    an interface the switch will never report creates drift that can never clear.
    """
    value = _get("document_mgmt_interface", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off")
    return bool(value)


def is_configured() -> tuple[bool, str]:
    """Whether the plugin can reach a lab agent, and why not when it cannot."""
    if not lab_agent_url():
        return False, "lab_agent_url is not set in PLUGINS_CONFIG['netbox_demo']"
    if not lab_agent_token():
        return False, "lab_agent_token is not set in PLUGINS_CONFIG['netbox_demo']"
    return True, ""
