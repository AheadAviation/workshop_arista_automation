"""Shared Cloud <-> Local API contract for netbox-demo (INIT-001/SPEC-001).

Single source of truth for:
  * allowlisted Arista cEOS fabric devices (namespaced so the demo fabric never
    collides with another lab already documented in the same NetBox instance),
  * management IP plan,
  * allowed actions,
  * reversible rogue-drift scenarios,
  * constant-time Bearer token verification and command validation.

Everything is environment-driven so the same image can drive a differently
named fabric without a code change.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
from typing import Any

AGENT_VERSION = "0.1.0"

# --------------------------------------------------------------------------- #
# Fabric naming & addressing
# --------------------------------------------------------------------------- #

#: Prefix applied to every switch hostname. NetBox device names are globally
#: unique, so a prefix keeps this demo fabric distinct from any other lab
#: (for example an existing `DC1` fabric using bare `spine-01`/`leaf-01` names).
DEVICE_PREFIX = os.environ.get("DEMO_DEVICE_PREFIX", "demo-")

#: Management network for the demo fabric. Deliberately a different /24 from the
#: workshop lab's 172.30.30.0/24 so both can exist on one host without confusion.
MGMT_SUBNET = os.environ.get("CLAB_MGMT_SUBNET", "172.31.30.0/24")
MGMT_GATEWAY = os.environ.get("CLAB_MGMT_GATEWAY", "172.31.30.1")

#: Host octet of each switch inside MGMT_SUBNET.
_HOST_OCTETS: dict[str, int] = {
    "spine-01": 11,
    "spine-02": 12,
    "leaf-01": 21,
    "leaf-02": 22,
    "leaf-03": 23,
    "leaf-04": 24,
}

#: Short names (no prefix) in stable fabric order.
_SHORT_NAMES: tuple[str, ...] = (
    "spine-01",
    "spine-02",
    "leaf-01",
    "leaf-02",
    "leaf-03",
    "leaf-04",
)

#: Full hostnames as they appear in ContainerLab, eAPI and NetBox.
FABRIC_DEVICES: tuple[str, ...] = tuple(f"{DEVICE_PREFIX}{name}" for name in _SHORT_NAMES)

SPINES: tuple[str, ...] = (FABRIC_DEVICES[0], FABRIC_DEVICES[1])
LEAVES: tuple[str, ...] = FABRIC_DEVICES[2:]

#: ContainerLab topology name (also the Docker network prefix).
TOPOLOGY_NAME = os.environ.get("CLAB_TOPOLOGY_NAME", "netbox-demo-fabric")

#: cEOS container image already imported on the lab host.
CEOS_IMAGE = os.environ.get("CEOS_IMAGE", "ceos:4.32.0F")


def _mgmt_host(short_name: str) -> str:
    """Return the management IP for a short device name inside MGMT_SUBNET."""
    network = ipaddress.ip_network(MGMT_SUBNET, strict=False)
    octet = _HOST_OCTETS[short_name]
    return str(network.network_address + octet)


#: Full-device-name -> management IP.
DEVICE_MGMT_IPS: dict[str, str] = {
    f"{DEVICE_PREFIX}{short}": _mgmt_host(short) for short in _SHORT_NAMES
}

#: Short name -> full device name, for topology rendering.
SHORT_TO_FULL: dict[str, str] = {short: f"{DEVICE_PREFIX}{short}" for short in _SHORT_NAMES}

#: Fabric links, expressed with short names so the prefix is applied once.
FABRIC_LINKS: tuple[tuple[str, str], ...] = (
    # Spine-01 to every leaf
    ("spine-01:eth1", "leaf-01:eth1"),
    ("spine-01:eth2", "leaf-02:eth1"),
    ("spine-01:eth3", "leaf-03:eth1"),
    ("spine-01:eth4", "leaf-04:eth1"),
    # Spine-02 to every leaf
    ("spine-02:eth1", "leaf-01:eth2"),
    ("spine-02:eth2", "leaf-02:eth2"),
    ("spine-02:eth3", "leaf-03:eth2"),
    ("spine-02:eth4", "leaf-04:eth2"),
    # MLAG peer links (leaf-01/leaf-02 and leaf-03/leaf-04)
    ("leaf-01:eth3", "leaf-02:eth3"),
    ("leaf-01:eth4", "leaf-02:eth4"),
    ("leaf-03:eth3", "leaf-04:eth3"),
    ("leaf-03:eth4", "leaf-04:eth4"),
)

# --------------------------------------------------------------------------- #
# Allowlisted actions
# --------------------------------------------------------------------------- #

ALLOWED_ACTIONS: tuple[str, ...] = (
    "destroy-lab",
    "deploy-bare-lab",
    "run-discovery",
    "push-configs",
    "validate-fabric",
    "eapi",
    "inject-rogue-drift",
    "remediate-drift",
)

# --------------------------------------------------------------------------- #
# Rogue CLI drift for the NetBox Assurance closed-loop demo
# --------------------------------------------------------------------------- #

#: Reversible drift scenarios. Each injects state that cannot exist in NetBox
#: intent (a stray VLAN, a stray loopback, a shut server port) so Assurance has
#: something concrete to flag, then removes it again.
ROGUE_DRIFT_SCENARIOS: dict[str, dict[str, Any]] = {
    "multi_drift": {
        "title": "Unauthorized VLAN 666 + rogue Loopback99 + server-port shutdown",
        "inject": [
            "vlan 666",
            "name ROGUE_DRIFT",
            "exit",
            "interface Loopback99",
            "description UNAUTHORIZED_LOOPBACK",
            "ip address 10.99.99.1/32",
            "exit",
            "interface Ethernet6",
            "description UNAUTHORIZED_MANUAL_CHANGE",
            "shutdown",
            "exit",
        ],
        "remediate": [
            "no vlan 666",
            "no interface Loopback99",
            "interface Ethernet6",
            "description Server-Port",
            "no shutdown",
            "exit",
        ],
    },
    "rogue_vlan": {
        "title": "Unauthorized VLAN 666 (ROGUE_DRIFT)",
        "inject": ["vlan 666", "name ROGUE_DRIFT", "exit"],
        "remediate": ["no vlan 666"],
    },
    "port_shutdown": {
        "title": "Unauthorized Ethernet6 shutdown",
        "inject": [
            "interface Ethernet6",
            "description UNAUTHORIZED_MANUAL_CHANGE",
            "shutdown",
            "exit",
        ],
        "remediate": [
            "interface Ethernet6",
            "description Server-Port",
            "no shutdown",
            "exit",
        ],
    },
}

#: Device that rogue drift is injected on (a leaf, so Assurance sees it on a VTEP).
ROGUE_DRIFT_DEVICE: str = SHORT_TO_FULL["leaf-01"]

# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #

#: Command prefixes that must never reach a switch through the eAPI proxy.
FORBIDDEN_COMMAND_PREFIXES: tuple[str, ...] = (
    "bash",
    "reload",
    "delete",
    "erase",
    "format",
    "copy",
    "write erase",
)


def verify_bearer_token(auth_header: str | None, expected_token: str) -> bool:
    """Validate `Authorization: Bearer <token>` in constant time."""
    if not expected_token or not auth_header:
        return False
    parts = auth_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return hmac.compare_digest(parts[1].strip(), expected_token)


def validate_device(device: str) -> str:
    """Validate a switch hostname against the fixed allowlist."""
    name = str(device or "").strip()
    if name not in FABRIC_DEVICES:
        raise ValueError(
            f"Unknown or disallowed device {name!r}; allowed: {', '.join(FABRIC_DEVICES)}"
        )
    return name


def validate_eapi_commands(commands: Any, mode: str) -> list[str]:
    """Validate a list of EOS CLI commands for the requested mode."""
    if mode not in ("show", "config"):
        raise ValueError(f"Invalid eAPI mode {mode!r}; expected 'show' or 'config'")
    if not isinstance(commands, list) or not commands:
        raise ValueError("commands must be a non-empty list of strings")

    cleaned: list[str] = []
    for raw in commands:
        cmd = str(raw).strip()
        if not cmd:
            continue
        lower = cmd.lower()
        for bad in FORBIDDEN_COMMAND_PREFIXES:
            if lower == bad or lower.startswith(f"{bad} "):
                raise ValueError(f"Forbidden command in {cmd!r}")
        if mode == "show" and not (lower.startswith("show ") or lower.startswith("ping ")):
            raise ValueError(
                f"Read-only show mode only permits 'show'/'ping' commands; got {cmd!r}"
            )
        cleaned.append(cmd)

    if not cleaned:
        raise ValueError("commands list contained no non-empty commands")
    return cleaned


def render_topology_yaml() -> str:
    """Render the bare ContainerLab topology for the configured device names."""
    lines: list[str] = [
        "# =============================================================================",
        "# NetBox Demo - bare ContainerLab topology (generated by lab-agent)",
        "# =============================================================================",
        "# Six Arista cEOS switches with only Day-0 management, eAPI, SNMP and LLDP",
        "# configured, so Orb Agent can discover them while NetBox intent is empty.",
        "",
        f"name: {TOPOLOGY_NAME}",
        "",
        "mgmt:",
        f"  network: {TOPOLOGY_NAME}_mgmt",
        f"  ipv4-subnet: {MGMT_SUBNET}",
        "",
        "topology:",
        "  kinds:",
        "    ceos:",
        f"      image: {CEOS_IMAGE}",
        "      image-pull-policy: never",
        "  nodes:",
    ]
    for short in _SHORT_NAMES:
        full = SHORT_TO_FULL[short]
        lines += [
            f"    {full}:",
            "      kind: ceos",
            f"      startup-config: /clab/bare-configs/{full}.cfg",
            f"      mgmt-ipv4: {DEVICE_MGMT_IPS[full]}",
        ]

    lines.append("  links:")
    for left, right in FABRIC_LINKS:
        ldev, lint = left.split(":")
        rdev, rint = right.split(":")
        lines.append(
            f'    - endpoints: ["{SHORT_TO_FULL[ldev]}:{lint}", "{SHORT_TO_FULL[rdev]}:{rint}"]'
        )
    lines.append("")
    return "\n".join(lines)
