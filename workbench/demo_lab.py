"""Bring up the demo lab: topology plus the server trunk config (INIT-001/SPEC-008).

The shipped fabric has no server-facing ports, so the demo's topology adds a fifth data port
on each leaf (`Ethernet5`) with a server dual-homed across each MLAG pair. That port has to
be *configured* as a trunk carrying the tenant VLANs before the demo can show a VLAN crossing
it — and the shipped Ansible render path deliberately does not know about it (it models the
fabric, not the demo's overlay).

So this module does the one thing the render path cannot: it declares the demo trunk in EOS's
own idiom, applies it once, and verifies it landed. It is idempotent — running it twice changes
nothing the second time.

    python -m workbench.demo_lab            # deploy topology + configure trunks
    python -m workbench.demo_lab --trunks   # only (re)configure the trunks
    python -m workbench.demo_lab --down     # destroy the demo topology
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from workbench.contracts import Plan
from workbench.device_reader import PORT_RE
from workbench.eos_executor import EosApplyError, apply_change, preview
from workbench.intent_source import BASE_VLANS, DEMO_TRUNKS

#: The tenant VLANs the trunk must carry, in the fabric's own VLAN set.
TRUNK_VLANS: tuple[int, ...] = BASE_VLANS

#: Human names for the tenant VLANs, matching the fabric's own CORE tenant.
VLAN_NAMES: dict[int, str] = {
    100: "SERVERS",
    110: "MGMT_SERVERS",
    120: "STORAGE",
    130: "BACKUP",
    140: "VOIP",
}

TOPOLOGY = "clab/topology-demo.clab.yml"


def trunk_plan(device: str) -> Plan:
    """The EOS config that makes `Ethernet5` a trunk carrying the tenant VLANs.

    Declares the VLANs themselves as well as the trunk membership. The rendered fabric config
    references the tenant VLANs inside the EVPN overlay, but never declares them as VLANs, so
    `show vlan` had only VLAN 1 — a trunk allowing VLANs that do not exist is not a tenant
    segment, and the live check caught exactly that.

    Declared as `switchport trunk allowed vlan <list>` (a full statement, not `add`) because
    this is the *initial* declaration of the trunk, so it should be authoritative. The demo's
    own VLAN change later uses `add`, which is idempotent and cannot drop anything.
    """
    ports = DEMO_TRUNKS.get(device, ())
    if not ports:
        raise ValueError(f"{device} has no demo trunk declared")
    for port in ports:
        if not PORT_RE.match(port):
            raise ValueError(f"unsafe port name {port!r}")

    lines: list[str] = []
    for vlan in TRUNK_VLANS:
        lines += [f"vlan {vlan}", f"   name {VLAN_NAMES.get(vlan, f'VLAN{vlan}')}"]
    vlan_list = ",".join(str(v) for v in TRUNK_VLANS)
    for port in ports:
        lines += [
            f"interface {port}",
            "   description Server trunk (dual-homed across the MLAG pair)",
            # ORDER MATTERS. A spare cEOS port comes up routed (`no switchport`), and
            # `switchport` is what converts it back to a switchport. Clearing IPv6 first
            # leaves the port routed, so `switchport mode trunk` is accepted but the port
            # stays `Vlan: routed` and no trunk ever exists -- which is what happened on the
            # first two attempts against the live lab. Put `switchport` first.
            "   switchport",
            "   switchport mode trunk",
            "   switchport trunk allowed vlan " + vlan_list,
            "   no ipv6 address auto-config",
            "   no shutdown",
        ]
    return Plan(
        device=device,
        platform="eos",
        usecase="demo-lab-trunk",
        fmt="text",
        config="\n".join(lines),
        # MUST be `merge`. `replace` starts the session from an empty config, which would
        # replace the switch's entire running configuration with these lines and take the
        # fabric down. That is the exact failure the executor's default mode exists to
        # prevent, and setting `replace` here (by mistake, during development) is how the
        # live lab caught it.
        mode="merge",
        reason=f"declare server trunk on {', '.join(ports)} carrying VLANs {vlan_list}",
    )


def disable_zerotouch(device: str, *, container_prefix: str = "clab-netbox-arista-lab-") -> bool:
    """Turn ZeroTouch off, so the switch will accept `write memory`.

    A cEOS switch that begins its ZeroTouch cycle before its startup-config lands gets stuck
    with ZeroTouch "Active", and an active ZeroTouch refuses to save configuration at all
    ("Cannot copy to startup-config when ZeroTouch is enabled"). The result is a switch that
    accepts a change, reports success, and silently reverts it minutes later -- which is
    exactly how the demo appeared to work and then undid itself on the live lab.

    Disabling it is a no-op on a switch where it is already off, so this is safe to run
    every time. Returns True when the switch reports ZeroTouch disabled afterwards.
    """
    import subprocess

    container = f"{container_prefix}{device}"
    try:
        proc = subprocess.run(  # noqa: S603 - argument list, no shell
            ["docker", "exec", "-i", container, "Cli", "-p", "15"],
            input="configure\nzerotouch disable\nend\nshow zerotouch\n",
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

    blob = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    return proc.returncode == 0 and "ZeroTouch Mode: Disabled" in blob


def configure_trunks(*, verbose: bool = True) -> int:
    """Apply the trunk config to every leaf that has one. Idempotent."""
    failures = 0
    for device in DEMO_TRUNKS:
        # ZeroTouch has to be off before anything can be saved; see `disable_zerotouch`.
        if not disable_zerotouch(device):
            if verbose:
                print(f"  {device}: WARNING could not confirm ZeroTouch is disabled")
            failures += 1
            continue

        try:
            plan = trunk_plan(device)
        except ValueError as exc:
            print(f"  {device}: {exc}")
            failures += 1
            continue

        try:
            current = preview(plan)
            if current.is_empty:
                if verbose:
                    print(f"  {device}: trunk already configured")
                continue
            if verbose:
                print(f"  {device}: configuring trunk ({current.changed_lines} line(s) change)")
            apply_change(plan)
            if verbose:
                print(f"  {device}: trunk configured")
        except EosApplyError as exc:
            print(f"  {device}: FAILED — {exc}")
            failures += 1
    return failures


def verify_trunks() -> int:
    """Read each switch back and confirm the trunk really carries the tenant VLANs.

    This is the check that matters: the eariler version of the demo configured
    `Ethernet6`, an interface that does not exist, and EOS accepted the config silently.
    Reading it back is the only way to know the change reached hardware.
    """
    from workbench.device_reader import read_device

    failures = 0
    for device, ports in DEMO_TRUNKS.items():
        actual = read_device(device)
        if not actual.reachable:
            print(f"  {device}: not reachable — {actual.error}")
            failures += 1
            continue
        for port in ports:
            allowed = _trunk_vlans_for(actual.trunk_allowed, port)
            missing = sorted(set(TRUNK_VLANS) - set(allowed))
            if missing:
                print(f"  {device}/{port}: MISSING VLANs {missing} (has {allowed or 'none'})")
                failures += 1
            else:
                print(f"  {device}/{port}: trunk carries {len(allowed)} VLAN(s)")
    return failures


def _trunk_vlans_for(trunk_allowed: dict[str, tuple[int, ...]], port: str) -> tuple[int, ...]:
    """Find a port's allowed VLANs however the switch spells the name.

    Delegates to the drift engine, which owns the name-normalisation rule, so `verify_trunks`
    and the page can never disagree about whether a trunk is correct.
    """
    from workbench.drift import find_trunk

    _found, vlans = find_trunk(trunk_allowed, port)
    return vlans


def deploy_topology() -> int:
    # The `clab` service mounts `./clab` at `/clab/topology`, and the generated configs at
    # `/clab/configs`, so the demo topology is addressed through the mount it actually has.
    result = subprocess.run(
        [
            "docker", "compose", "run", "--rm", "clab",
            "containerlab", "deploy", "--reconfigure",
            "--topo", "/clab/topology/topology-demo.clab.yml",
        ],
        capture_output=True,
        text=True,
    )
    print(result.stdout[-2000:] if result.stdout else "")
    if result.returncode != 0:
        print(result.stderr[-1000:] if result.stderr else "")
    return result.returncode


def destroy_topology() -> int:
    result = subprocess.run(
        [
            "docker", "compose", "run", "--rm", "clab",
            "containerlab", "destroy", "--cleanup",
            "--topo", "/clab/topology/topology-demo.clab.yml",
        ],
        capture_output=True,
        text=True,
    )
    print(result.stdout[-1000:] if result.stdout else "")
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m workbench.demo_lab",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--trunks", action="store_true", help="only (re)configure the trunks")
    parser.add_argument("--verify", action="store_true", help="only verify the trunks")
    parser.add_argument("--down", action="store_true", help="destroy the demo topology")
    parser.add_argument("--no-deploy", action="store_true", help="skip the topology deploy")
    args = parser.parse_args(argv)

    if args.down:
        return 0 if destroy_topology() == 0 else 1

    if args.verify:
        print("Verifying server trunks")
        failures = verify_trunks()
        return 0 if failures == 0 else 1

    if not args.trunks and not args.no_deploy:
        print("Deploying the demo topology (adds server-facing ports)")
        if deploy_topology() != 0:
            print("topology deploy failed")
            return 1
        print("Waiting for the switches to settle...")
        import time

        time.sleep(45)

    print("Configuring server trunks")
    failures = configure_trunks()
    if failures:
        print(f"{failures} device(s) failed")
        return 1

    print("Verifying")
    verify_failures = verify_trunks()
    return 0 if verify_failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
