"""Bootstrap NetBox so a fresh stack is demo-ready with no manual steps.

Runs as the `netbox-bootstrap` one-shot service, after NetBox is healthy. Two jobs, in this
order, because the second depends on the first:

1. **Import the fabric**, so the devices exist. Done by calling the repo's own importer rather
   than reimplementing it — the same code path `run.ps1 import` uses.
2. **Install and assign the ConfigTemplate**, so `Render Config` works on those devices.

Order matters and was the actual bug: run in the other order, the template is created but there
are no devices to assign it to, and `Render Config` still says "No configuration template has
been assigned for this device" on a fresh stack — which looks like the feature is broken.

Idempotent, so it is safe on every `docker compose up`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: Where the repo is mounted in this container.
REPO = Path("/opt/netbox/repo")

#: The importer, run the same way `run.ps1 import` runs it.
IMPORTER = REPO / "scripts" / "full_import.py"

#: The ConfigTemplate installer, baked into the image.
INSTALLER = Path("/opt/netbox/templates/install.py")


def run(args: list[str], *, timeout: int = 900) -> tuple[str, int]:
    try:
        proc = subprocess.run(  # noqa: S603 - argument list, no shell
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return f"not available: {exc}", 127
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout}s", 124
    return f"{proc.stdout or ''}\n{proc.stderr or ''}".strip(), proc.returncode


def main() -> int:
    print("=== netbox-bootstrap ===", flush=True)

    # 1. Devices. Skip cleanly when the importer is not present, so a minimal checkout still
    #    bootstraps the template rather than failing everything.
    if IMPORTER.exists():
        print(f"importing the fabric from {IMPORTER} ...", flush=True)
        out, code = run([sys.executable, str(IMPORTER)])
        if code == 0:
            print("  import ok", flush=True)
        else:
            # Not fatal: the template still installs, and a presenter can import by hand.
            tail = "\n".join(out.splitlines()[-6:])
            print(f"  import FAILED (rc={code}):\n{tail}", flush=True)
    else:
        print(f"  no importer at {IMPORTER}; skipping device import", flush=True)

    # 2. The config template, now that there are devices to assign it to.
    if INSTALLER.exists():
        print("installing the config template ...", flush=True)
        out, code = run(
            [
                "/opt/netbox/venv/bin/python",
                "/opt/netbox/netbox/manage.py",
                "shell",
                "-c",
                f"exec(open('{INSTALLER}').read())",
            ]
        )
        for line in out.splitlines():
            if line.startswith(("ConfigTemplate", "Assigned", "(no devices")):
                print("  " + line, flush=True)
        if code != 0:
            print(f"  template install FAILED (rc={code})", flush=True)
            return 1
    else:
        print(f"  no installer at {INSTALLER}", flush=True)
        return 1

    # 3. Topology view defaults, so the fabric appears on first open rather than an empty
    #    canvas. See the script's own header for why the defaults matter that much.
    topology_installer = Path("/opt/netbox/templates/topology_defaults.py")
    if topology_installer.exists():
        print("configuring topology view defaults ...", flush=True)
        out, code = run(
            [
                "/opt/netbox/venv/bin/python",
                "/opt/netbox/netbox/manage.py",
                "shell",
                "-c",
                f"exec(open('{topology_installer}').read())",
            ]
        )
        for line in out.splitlines():
            if line.startswith("Topology defaults"):
                print("  " + line, flush=True)
        if code != 0:
            print(f"  topology defaults FAILED (rc={code})", flush=True)

    print("=== ready ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
