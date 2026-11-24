"""Return the demo trunks to their declared starting state (INIT-001).

Why this exists, separately from `workbench.cli reset`: reset *adds* whatever intended state
requires and is deliberately additive, so it will never take anything away. That is the right
behaviour for a remediation tool — removing VLANs nobody asked it to remove would be a second,
riskier change.

But it means a lab that has been used for testing accumulates residue: test VLANs declared on
switches and allowed on trunks, none of which the demo intends. `workbench.cli check` calls the
fabric compliant anyway, because "does every intended VLAN exist" is the question drift answers,
not "are there extras". Both behaviours are correct and together they hide the mess, which is why
this script is explicit about what it does rather than folded into reset.

    python scripts/clean_demo_fabric.py            # report only
    python scripts/clean_demo_fabric.py --apply    # make the changes

Safe to run repeatedly. Touches only `Ethernet5` and only the VLANs listed here, so a real
fabric's own VLANs are never at risk.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Run from anywhere: the repo root has to be importable so this reuses the engine's own
# trunk parsing rather than inventing a second interpretation of `show interfaces trunk`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONTAINER_PREFIX = "clab-netbox-arista-lab-"
LEAVES = ("leaf-01", "leaf-02", "leaf-03", "leaf-04")
TRUNK = "Ethernet5"

#: The demo's declared starting VLANs: the tenant set the trunk must carry, and nothing else.
KEEP = (100, 110, 120, 130, 140)

#: VLANs that legitimately exist on a cEOS fabric node and are not tenant segments.
#: 1 is the default; 4080/4081/4094 are EOS internals (MLAG peer, VXLAN, reserved).
SYSTEM = (1, 4080, 4081, 4094)


def cli(device: str, script: str, timeout: int = 90) -> str:
    import subprocess as sp

    proc = sp.run(  # noqa: S603 - argument list, no shell
        ["docker", "exec", "-i", f"{CONTAINER_PREFIX}{device}", "Cli", "-p", "15"],
        input=script,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return f"{proc.stdout or ''}\n{proc.stderr or ''}"


def declared_vlans(device: str) -> list[int]:
    out = cli(device, "show vlan\n")
    found: list[int] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit():
            vlan = int(parts[0])
            if 1 <= vlan <= 4094 and vlan not in SYSTEM:
                found.append(vlan)
    return sorted(set(found))


def trunk_vlans(device: str) -> list[int]:
    """The VLANs currently allowed on the trunk, read from `show interfaces trunk`."""
    from workbench.device_reader import parse_trunks
    from workbench.drift import find_trunk

    out = cli(device, "show interfaces trunk\n")
    trunks = parse_trunks(out)
    _found, allowed = find_trunk(trunks, TRUNK)
    return sorted(set(allowed))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="make the changes")
    args = parser.parse_args(argv)

    problems = 0
    for device in LEAVES:
        print(f"--- {device} ---")

        declared = declared_vlans(device)
        extra_declared = [v for v in declared if v not in KEEP]
        on_trunk = trunk_vlans(device)
        extra_trunk = [v for v in on_trunk if v not in KEEP]

        print(f"  declared   : {declared}")
        print(f"  on {TRUNK:<8}: {on_trunk}")

        commands: list[str] = []
        # The trunk statement is authoritative here (`allowed vlan`, not `add`), because the
        # point is to state exactly what this trunk carries — that is what the demo intends.
        if extra_trunk or any(v not in on_trunk for v in KEEP):
            commands.append(f"interface {TRUNK}")
            commands.append("   switchport trunk allowed vlan " + ",".join(str(v) for v in KEEP))
        # Remove the declarations too, so `show vlan` is not carrying phantom VLANs that no
        # trunk uses. A VLAN left declared is how the residue survived the last cleanup.
        for vlan in extra_declared:
            commands.append(f"no vlan {vlan}")

        if not commands:
            print("  already clean")
            continue

        print(f"  to change  : {'; '.join(commands)}")
        if not args.apply:
            problems += 1
            continue

        out = cli(device, "configure\n" + "\n".join(commands) + "\nend\nwrite memory\n")
        if "% Invalid" in out or "% Error" in out:
            first = next(
                (ln.strip() for ln in out.splitlines() if "% Invalid" in ln or "% Error" in ln),
                "unknown",
            )
            print(f"  FAILED: {first}")
            problems += 1
            continue

        # Read back: the same rule as everywhere else, a change is not done until the device
        # agrees it is.
        after_declared = declared_vlans(device)
        after_trunk = trunk_vlans(device)
        ok = after_trunk == sorted(KEEP) and not [v for v in after_declared if v not in KEEP]
        print(f"  after      : declared {after_declared} / trunk {after_trunk}")
        print("  ok" if ok else "  FAILED to converge")
        if not ok:
            problems += 1

    print()
    if problems and not args.apply:
        print(f"{problems} device(s) need cleaning. Re-run with --apply.")
        return 1
    if problems:
        print(f"{problems} problem(s).")
        return 1
    print("Fabric trunks are at their declared starting state.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
