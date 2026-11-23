"""Read what a device actually is, over its own CLI (INIT-001/SPEC-002).

The demo reads actual state the same way a person would: ask the switch and parse the
answer. It does not use eAPI, NAPALM or Ansible, because during a briefing the last
thing anyone wants is a job scheduler between the question and the answer — and the
parsers being plain text means they can be tested against captured output with nothing
running.

Two hard rules, both learned from how this lab actually works:

1. **Nothing here builds a shell string from unvalidated input.** The device name and
   the port names reach a subprocess argument list, never a shell, and the device name
   is checked against a strict pattern first.
2. **The command runner is injectable.** Tests pass a fake that returns captured
   output; the demo passes the real one that talks to the running container. That is
   the whole reason this module is testable without a lab.

Provenance: the CLI access pattern (`docker exec <node> Cli -p 15`) is the one this
repository already uses in `scripts/config_diff.ps1` and `run.ps1`.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

from workbench.contracts import Actual

#: Matches the containerlab container names this repo already creates.
DEVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

#: A port name as EOS renders it. Deliberately narrow: this value reaches a command
#: argument list, so anything unusual is refused rather than passed through.
PORT_RE = re.compile(r"^(?:Ethernet|Et|Port-Channel|Po)\d+(?:/\d+)?$")

#: The runner signature: take a command list, return (stdout, stderr, returncode).
Runner = Callable[[Sequence[str]], "tuple[str, str, int]"]


class DeviceReadError(RuntimeError):
    """The device could not be read. Carries a reason fit to show a person."""


# --------------------------------------------------------------------------- #
# The real runner
# --------------------------------------------------------------------------- #


def container_runner(container_prefix: str = "clab-netbox-arista-lab-") -> Runner:
    """Run a command inside the device's container, the way this lab already does.

    Returns the (stdout, stderr, code) triple rather than raising, so a dead device
    becomes an `Actual(reachable=False)` instead of an exception in the page.
    """

    def run(args: Sequence[str]) -> tuple[str, str, int]:
        try:
            proc = subprocess.run(  # noqa: S603 - argument list, no shell
                list(args),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as exc:  # docker not installed
            return "", f"docker is not available: {exc}", 127
        except subprocess.TimeoutExpired:
            return "", "the device did not answer in 30 seconds", 124
        return proc.stdout or "", proc.stderr or "", proc.returncode

    return run


def read_device(
    device: str,
    *,
    platform: str = "eos",
    runner: Runner | None = None,
    container_prefix: str = "clab-netbox-arista-lab-",
) -> Actual:
    """Read one device and return its actual state.

    Never raises for a network reason: an unreachable or misbehaving device comes back
    as an `Actual` with `reachable=False` and the reason, which the page shows as a
    finding. It raises only for a programming error (an invalid device name).
    """
    if not DEVICE_RE.match(device):
        raise DeviceReadError(f"invalid device name {device!r}")

    run = runner or container_runner(container_prefix)
    container = f"{container_prefix}{device}"

    hostname = _cli(run, container, "show hostname")
    if hostname is None:
        return Actual(
            device=device,
            platform=platform,
            reachable=False,
            error=_last_error(run, container),
        )

    vlans_out = _cli(run, container, "show vlan") or ""
    trunk_out = _cli(run, container, "show interfaces trunk") or ""
    version_out = _cli(run, container, "show version") or ""
    run_cfg = _cli(run, container, "show running-config section router bgp") or ""

    return Actual(
        device=device,
        platform=platform,
        reachable=True,
        hostname=parse_hostname(hostname),
        asn=parse_asn(run_cfg),
        vlans=parse_vlans(vlans_out),
        trunk_allowed=parse_trunks(trunk_out),
        os_version=parse_os_version(version_out),
    )


def _cli(run: Runner, container: str, command: str) -> str | None:
    """One CLI command, returning None when it did not work.

    EOS prints its complaints to stdout rather than to stderr, so a return code of 0
    is not enough on its own; the callers' parsers tolerate junk, and `_last_error`
    is what produces the message a person sees.
    """
    out, err, code = run(["docker", "exec", container, "Cli", "-p", "15", "-c", command])
    if code != 0:
        return None
    # EOS writes "% Invalid input" to stdout with a zero exit; treat that as failure.
    if "% Invalid" in out or "% Error" in out:
        return None
    return out


def _last_error(run: Runner, container: str) -> str:
    """The most useful one-line reason we can give for a device we could not read."""
    out, err, code = run(["docker", "exec", container, "Cli", "-p", "15", "-c", "show hostname"])
    blob = (err or out or "").strip()
    if code == 127:
        return "docker is not available on this machine"
    if code == 124:
        return "the device did not answer in 30 seconds"
    if "No such container" in blob or "is not running" in blob:
        return f"container {container} is not running (deploy the lab first)"
    if blob:
        # Keep it short: this string is rendered inside a card on screen.
        first = blob.splitlines()[0][:200]
        return first
    return "no response from the device"


# --------------------------------------------------------------------------- #
# Parsers. Pure, and each one is pinned to captured output in the tests.
# --------------------------------------------------------------------------- #


def parse_hostname(output: str) -> str:
    """`show hostname` → the hostname.

    EOS prints a single line. Some versions print `Hostname: x`; both are handled.
    """
    for line in output.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.lower().startswith("hostname:"):
            return text.split(":", 1)[1].strip()
        return text.split()[0]
    return ""


def parse_vlans(output: str) -> tuple[int, ...]:
    """`show vlan` table → the VLAN ids present.

    The table looks like:

        VLAN  Name                             Status    Ports
        ----  -------------------------------- --------- -------------------------------
        100   SERVERS                          active    Et5, Et6
        4094  MLAG_PEER                        active

    Only the id column is read. Lines that do not start with a number are skipped, so
    the header, the dashes and any trailing summary are all ignored.
    """
    found: list[int] = []
    for line in output.splitlines():
        match = re.match(r"^\s*(\d{1,4})\s+\S", line)
        if not match:
            continue
        vlan = int(match.group(1))
        if 1 <= vlan <= 4094 and vlan not in found:
            found.append(vlan)
    return tuple(found)


def parse_trunks(output: str) -> dict[str, tuple[int, ...]]:
    """`show interfaces trunk` → {port: (allowed vlan ids)}.

    Real output has a port table then several per-port blocks. Only the **first**
    allow-list block is read, because it is the only one that answers "can this VLAN
    cross this trunk":

        Port        Mode             Status        Native vlan
        Et5         on               trunking      1

        Port        Vlans allowed on trunk
        Et5         100,110,120,130,140

        Port        Vlans allowed and active in management domain
        Et5         100,110,120,130,140

    The later blocks are narrower views (`and active`, `not pruned`) and would silently
    overwrite the real allow-list, which is exactly the bug this function was written
    to avoid: a VLAN that is allowed but not yet active would vanish from the answer.

    Note `Port-Channel2000` starts with "Port", so header detection compares the whole
    first token rather than using a prefix test.
    """
    allowed: dict[str, tuple[int, ...]] = {}
    in_allowed_block = False

    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue

        lowered = line.lower()
        if "vlans allowed on trunk" in lowered:
            in_allowed_block = True
            continue

        first = line.split()[0]
        # A section header is a line whose first token is the literal word "Port".
        # Anything else with a port-shaped first token is data.
        if first.lower() == "port":
            if in_allowed_block and allowed:
                break  # the allow-list block has ended; later blocks are narrower
            continue

        if in_allowed_block and PORT_RE.match(first):
            parts = line.split(None, 1)
            allowed[first] = parse_vlan_list(parts[1] if len(parts) > 1 else "")

    return allowed


def parse_vlan_list(text: str) -> tuple[int, ...]:
    """'100,110,120-140' → (100, 110, 120, ..., 140). Ignores anything unparseable."""
    found: list[int] = []
    for token in re.split(r"[,\s]+", text.strip()):
        if not token:
            continue
        if "-" in token:
            lo, _, hi = token.partition("-")
            if not (lo.isdigit() and hi.isdigit()):
                continue
            start, end = int(lo), int(hi)
            if start > end or end > 4094:
                continue
            found.extend(list(range(start, end + 1)))
        elif token.isdigit():
            value = int(token)
            if 1 <= value <= 4094:
                found.append(value)
    return tuple(sorted(set(found)))


def parse_asn(output: str) -> int:
    """`show running-config section router bgp` → the local ASN, or 0."""
    match = re.search(r"^router bgp (\d+)", output, re.MULTILINE)
    return int(match.group(1)) if match else 0


def parse_os_version(output: str) -> str:
    """`show version` → the software image version, or ''.

    This is what the lifecycle view reports, so it has to be the real string the
    switch prints rather than a normalised guess.
    """
    match = re.search(r"Software image version:\s*(\S+)", output)
    if match:
        return match.group(1)
    match = re.search(r"^vEOS.*version\s+(\S+)", output, re.MULTILINE | re.IGNORECASE)
    return match.group(1) if match else ""


__all__ = [
    "DeviceReadError",
    "container_runner",
    "parse_asn",
    "parse_hostname",
    "parse_os_version",
    "parse_trunk_ports",
    "parse_trunks",
    "parse_vlan_list",
    "parse_vlans",
    "read_device",
]


def parse_trunk_ports(output: str) -> tuple[str, ...]:
    """The ports `show interfaces trunk` lists as trunking, in output order."""
    ports: list[str] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and PORT_RE.match(parts[0]) and parts[2].startswith("trunk"):
            if parts[0] not in ports:
                ports.append(parts[0])
    return tuple(ports)
