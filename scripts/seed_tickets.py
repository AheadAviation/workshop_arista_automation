"""Seed a realistic ticket history for the briefing (INIT-002/SPEC-006).

The Tickets tab is a demo surface, and an almost-empty list makes the integration look
theoretical. This creates the tickets a hospital's network team would actually have open while
the executives walk in — a mix of states and priorities, each in the same group the plugin
writes to, so the page shows a real queue.

    python scripts/seed_tickets.py            # create them
    python scripts/seed_tickets.py --list     # show what is there
    python scripts/seed_tickets.py --reset    # close and retag the demo tickets

Idempotent: tickets are identified by title, so running it twice does not duplicate them.

All of this goes through Zammad's REST API with the plugin's own token, which means the seed
also proves the token works for writes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

NETBOX = "workshop_arista_automation-netbox-1"

#: (title, body, priority, desired_state)
#: "open" tickets are work in flight; "closed" ones are history the audience can scroll.
SEED: list[tuple[str, str, str, str]] = [
    (
        "[CHANGE] Add VLAN 220 (PHILIPS_MONITORING) to leaf-02 and leaf-03",
        "New Philips patient-monitoring rollout in the East wing.\n\n"
        "Two new trunk memberships required, one on each MLF pair:\n"
        "  leaf-02 Ethernet5  + VLAN 220\n"
        "  leaf-03 Ethernet5  + VLAN 220\n\n"
        "Requested by Clinical Systems. Window: Sunday 02:00-04:00.\n"
        "Rollback: remove VLAN 220 from both trunks; no host impact expected.",
        "2 high",
        "open",
    ),
    (
        "[CHANGE] Raise MTU on the storage trunk - STORAGE VLAN 120",
        "Storage vendor reports jumbo frames rejected at the leaf.\n\n"
        "Requires MTU 9214 on both storage trunk legs plus the VLAN 120 SVI.\n"
        "Change-freeze exception requested by the storage team.\n"
        "Verification: extended ping at 9000 bytes from both legs before closing.",
        "2 high",
        "open",
    ),
    (
        "[INCIDENT] Intermittent loss on leaf-04 Ethernet5",
        "Reported by the Radiology PACS team: 0.2% loss during evening batch.\n\n"
        "Current evidence:\n"
        "  - interface counters show no CRC or runts\n"
        "  - the trunk allows VLAN 140 (VOIP) and VLAN 312 (RADIOLOGY)\n"
        "  - leaf-04 is a member of MLAG pair 3\n\n"
        "Next: compare intended state in NetBox against the live config before touching "
        "anything.",
        "2 high",
        "new",
    ),
    (
        "[CHANGE] Retire VLAN 151 (legacy imaging) from the fabric",
        "The legacy imaging segment is no longer in use after the PACS migration.\n\n"
        "Scope: remove VLAN 151 from every leaf trunk, then from NetBox intent so the model "
        "stops asserting it.\n\n"
        "Blocked until Clinical Systems confirm no remaining hosts. Do not action yet.",
        "1 low",
        "pending reminder",
    ),
    (
        "[CHANGE] Add VLAN 312 (RADIOLOGY) to leaf-04 - COMPLETED",
        "Change applied and verified.\n\n"
        "  intended : vlan 312 / name RADIOLOGY / trunk Ethernet5\n"
        "  applied  : leaf-04\n"
        "  verified : read back from the device, compliant with intent\n"
        "  approved : Network Automation (via NetBox Ops)\n\n"
        "Kept as an example of the closure record.",
        "3 normal",
        "closed",
    ),
    (
        "[CHANGE] Add VLAN 333 (PHARMACY) to leaf-02 - ROLLED BACK",
        "Change was approved and pushed, then reverted at the request of Pharmacy IT pending "
        "a new go-live date.\n\n"
        "Rollback performed by removing VLAN 333 from Ethernet5 and re-reading the trunk.\n"
        "Ticket closed as not required.",
        "3 normal",
        "closed",
    ),
]


def netbox(code: str, timeout: int = 300) -> str:
    proc = subprocess.run(  # noqa: S603 - argument list, no shell
        ["docker", "exec", NETBOX, "/opt/netbox/netbox/manage.py", "shell", "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return f"{proc.stdout or ''}\n{proc.stderr or ''}"


#: The seeding runs inside NetBox, because that is where the token and network access are.
SEED_SCRIPT = '''
import json
from netbox_ops import itsm

seeds = json.loads(SEED_JSON)
created, existing, failed = 0, 0, 0

listing = itsm.tickets(limit=100)
have = {t.get("title") for t in (listing.get("tickets") or [])}

for title, body, priority, state in seeds:
    if title in have:
        print("SKIP", title[:60])
        existing += 1
        continue
    out = itsm.create_change_ticket(title, body)
    if not out.get("ok"):
        print("FAIL", title[:60], "|", out.get("headline"), out.get("detail", "")[:80])
        failed += 1
        continue
    print("MADE", out.get("number"), title[:60])
    created += 1

print("SUMMARY", created, existing, failed)
'''

#: Set the states the seed asks for. Zammad always creates a ticket in `new`, so the desired
#: state has to be applied afterwards — the first version of this script ignored the state
#: column entirely and left ten tickets all sitting in `new`, which reads as a queue nobody has
#: ever worked. States are resolved by name against the install's own list, not hardcoded.
STATE_SCRIPT = '''
import json
from netbox_ops import engine, itsm
from netbox_ops.itsm import _session_and_base, _state_names

wanted = json.loads(WANTED_JSON)
session, base = _session_and_base()
names = _state_names(session, base)
by_name = {v.lower(): k for k, v in names.items()}

listing = engine.itsm_tickets(limit=100).to_dict()
titles = {t.get("title"): t for t in (listing.get("tickets") or [])}

changed, missing = 0, 0
for title, state in wanted.items():
    item = titles.get(title)
    if not item:
        print("MISSING", title[:60])
        missing += 1
        continue
    target = by_name.get(state.lower())
    if target is None:
        print("NOSTATE", state, "for", title[:50])
        missing += 1
        continue
    if item.get("state_id") == target:
        print("ALREADY", item.get("number"), state)
        continue

    payload = {"state_id": target}
    # Pending states are refused without a due time (422). Zammad needs `pending_time` or the
    # ticket has no "until when", which is the whole meaning of the state. Set it a week out.
    if state.startswith("pending"):
        import datetime
        payload["pending_time"] = (datetime.datetime.now(datetime.timezone.utc)
                                   + datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    r = session.put(f"{base}/api/v1/tickets/{int(item['id'])}", json=payload, timeout=20)
    ok = r.status_code < 400
    print("STATE", item.get("number"), "->", state, "ok" if ok else f"failed {r.status_code}")
    changed += 0 if ok else 1
print("STATES", changed, missing)
'''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--list", action="store_true", help="show the current tickets")
    parser.add_argument("--reset", action="store_true", help="close the demo tickets")
    args = parser.parse_args(argv)

    if args.list:
        out = netbox(
            "from netbox_ops import engine\n"
            "t = engine.itsm_tickets(limit=60).to_dict()\n"
            "print('HEADLINE', t.get('headline'))\n"
            "for x in (t.get('tickets') or []):\n"
            "    print('T', x.get('number'), '|', x.get('state'), '|', (x.get('title') or '')[:64])\n"
        )
        for line in out.splitlines():
            if line.startswith(("HEADLINE", "T ")):
                print(line)
        return 0

    if args.reset:
        print("Closing the demo tickets (this keeps their history)")
        out = netbox(
            "from netbox_ops import engine, itsm\n"
            "t = engine.itsm_tickets(limit=60).to_dict()\n"
            "n = 0\n"
            "for x in (t.get('tickets') or []):\n"
            "    title = x.get('title') or ''\n"
            "    if not title.startswith(('[CHANGE]', '[INCIDENT]')):\n"
            "        continue\n"
            "    r = itsm.close_ticket(int(x['id']), 'Housekeeping: demo history reset')\n"
            "    print('CLOSED', x.get('number'), r.get('ok'))\n"
            "    n += 1\n"
            "print('TOTAL', n)\n"
        )
        for line in out.splitlines():
            if line.startswith(("CLOSED", "TOTAL")):
                print("  " + line)
        return 0

    print("Seeding demo tickets through Zammad's API")
    code = f"SEED_JSON = {json.dumps(json.dumps(SEED))}\n" + SEED_SCRIPT
    out = netbox(code)

    for line in out.splitlines():
        if line.startswith(("MADE", "SKIP", "FAIL", "SUMMARY")):
            print("  " + line)

    if "SUMMARY" not in out:
        print("\nThe seed did not complete. Last output:")
        print(out[-1200:])
        return 1

    # Now apply the states the seed describes. A separate call so a failure here does not
    # prevent the tickets from existing.
    print("\nApplying the intended states")
    wanted = {title: state for title, _body, _priority, state in SEED}
    state_code = f"WANTED_JSON = {json.dumps(json.dumps(wanted))}\n" + STATE_SCRIPT
    state_out = netbox(state_code)
    for line in state_out.splitlines():
        if line.startswith(("STATE", "ALREADY", "NOSTATE", "MISSING", "STATES")):
            print("  " + line)

    summary = next(ln for ln in out.splitlines() if ln.startswith("SUMMARY")).split()
    failed = int(summary[3]) if len(summary) > 3 else 0
    if "STATES" in state_out:
        state_parts = next(ln for ln in state_out.splitlines() if ln.startswith("STATES")).split()
        failed += int(state_parts[1]) if len(state_parts) > 1 else 0
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
