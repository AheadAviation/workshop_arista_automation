"""Remove the change tickets left behind by earlier testing (housekeeping, not demo content).

Three tickets in the queue are artefacts of building this, not of a network team's work: the
duplicate VLAN 210 pair from two verification runs, and the VLAN 346 change made while proving
the ITSM setup. They are harmless but on a projected screen they read as noise — and worse, the
duplicate pair looks like the tool created the same change twice.

    python scripts/clean_tickets.py           # list what would go
    python scripts/clean_tickets.py --apply   # delete them

Only touches tickets whose title matches exactly, so it cannot remove seeded demo content.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NETBOX = "workshop_arista_automation-netbox-1"

#: Leftovers from verification runs. Exact titles, so a seeded ticket is never at risk.
STALE = (
    "[CHANGE] Add VLAN 210 (PHILIPS_MEDICAL) to leaf-01",
    "[CHANGE] Add VLAN 346 (PHARMACY) to leaf-04",
)

SCRIPT = '''
import json
from netbox_ops import engine
from netbox_ops.itsm import _session_and_base

titles = json.loads(TITLES_JSON)
apply = APPLY

listing = engine.itsm_tickets(limit=100).to_dict()
items = [t for t in (listing.get("tickets") or []) if t.get("title") in titles]

if not items:
    print("NOTHING to remove")
else:
    session, base = _session_and_base()
    for item in items:
        if not apply:
            print("WOULD_DELETE", item.get("number"), (item.get("title") or "")[:60])
            continue
        r = session.delete(f"{base}/api/v1/tickets/{int(item['id'])}", timeout=20)
        print("DELETED" if r.status_code < 400 else f"FAILED {r.status_code}",
              item.get("number"), (item.get("title") or "")[:50])
print("DONE")
'''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="actually delete them")
    args = parser.parse_args(argv)

    code = (
        f"TITLES_JSON = {json.dumps(json.dumps(list(STALE)))}\n"
        f"APPLY = {args.apply}\n" + SCRIPT
    )
    proc = subprocess.run(  # noqa: S603 - argument list, no shell
        ["docker", "exec", NETBOX, "/opt/netbox/netbox/manage.py", "shell", "-c", code],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    out = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    for line in out.splitlines():
        if line.startswith(("WOULD_DELETE", "DELETED", "FAILED", "NOTHING")):
            print(line)
    if "DONE" not in out:
        print("did not complete:")
        print(out[-600:])
        return 1
    if not args.apply:
        print("\nRe-run with --apply to remove them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
