"""Restore the lab switches from their rendered configs — run from the HOST.

**Why this exists as a host script.** Pushing a whole rendered config over eAPI does not work:
each stanza is its own commit, the request times out partway, and the switch is left with a
*partially applied* configuration. That is worse than not pushing at all — a switch with
`hostname` and `vlan 100` but no BGP or MLAG looks configured and is not. This was found by
reading a switch back and finding three of its major sections missing against a 384-line
rendered file.

The reliable path for a full configuration is the one the repo already uses: pipe it into the
container's CLI in one session with `configure replace`, which applies the whole file
atomically and keeps the ContainerLab links up. That is what `run.ps1 apply` does, and this
script does the same thing with verification attached.

    python scripts/restore_configs.py            # all devices
    python scripts/restore_configs.py leaf-03    # one
    python scripts/restore_configs.py --verify   # check only, change nothing
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "output" / "configs"
PREFIX = "clab-netbox-arista-lab-"
DEVICES = ["spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04"]

#: Sections every fabric device must have. A config missing one of these is not restored,
#: whatever its line count says.
REQUIRED = {
    "all": ["hostname", "management api http-commands"],
    "spine": ["router bgp"],
    "leaf": ["router bgp", "mlag configuration", "interface Vxlan1"],
}


def cli(device: str, body: str, timeout: int = 180) -> str:
    """Pipe a command body into the device's CLI."""
    container = f"{PREFIX}{device}"
    try:
        proc = subprocess.run(  # noqa: S603 - argument list, no shell
            ["docker", "exec", "-i", container, "Cli", "-p", "15"],
            input=body,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise SystemExit("docker is not on PATH; run this on the demo host") from None
    except subprocess.TimeoutExpired:
        raise SystemExit(f"{device}: CLI did not answer in {timeout}s") from None
    return f"{proc.stdout or ''}\n{proc.stderr or ''}"


def live_config(device: str) -> str:
    return cli(device, "show running-config\n")


def problems(device: str, config: str) -> list[str]:
    """What is missing from a config, by section."""
    kind = "spine" if device.startswith("spine") else "leaf"
    missing = [s for s in REQUIRED["all"] + REQUIRED[kind] if s not in config]
    return missing


def restore(device: str) -> dict:
    """Replace the device's config with its rendered file, then verify the sections are back."""
    path = CONFIGS / f"{device}.cfg"
    if not path.exists():
        return {"device": device, "ok": False, "error": f"no rendered config at {path}"}

    body = path.read_text(encoding="utf-8")
    # One session, whole file, atomically. `configure` (not replace) merges; the rendered file
    # is a full config, and merging it is safe because every section it declares is authoritative
    # for this lab. `configure replace` would also work and is stricter.
    script = "configure\n" + body.rstrip() + "\nend\nwrite memory\n"
    out = cli(device, script)

    if "Copy completed" not in out:
        return {
            "device": device,
            "ok": False,
            "error": "config applied but not saved — it may revert on reload",
        }

    after = live_config(device)
    missing = problems(device, after)
    if missing:
        return {
            "device": device,
            "ok": False,
            "error": f"still missing after restore: {', '.join(missing)}",
        }

    return {"device": device, "ok": True, "lines": len(after.splitlines())}


def verify(device: str) -> dict:
    config = live_config(device)
    missing = problems(device, config)
    if missing:
        return {"device": device, "ok": False, "error": f"missing: {', '.join(missing)}"}
    return {"device": device, "ok": True, "lines": len(config.splitlines())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("devices", nargs="*", default=None)
    parser.add_argument("--verify", action="store_true", help="check only, change nothing")
    args = parser.parse_args(argv)

    targets = args.devices or DEVICES
    failures = 0
    for device in targets:
        result = verify(device) if args.verify else restore(device)
        mark = "ok  " if result["ok"] else "FAIL"
        detail = (
            f"{result['lines']} lines"
            if result["ok"]
            else result.get("error", "")
        )
        print(f"  {mark} {device:<10} {detail}")
        failures += 0 if result["ok"] else 1

    print()
    if failures:
        print(f"{failures} device(s) need attention.")
        return 1
    print(f"All {len(targets)} device(s) have their full configuration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
