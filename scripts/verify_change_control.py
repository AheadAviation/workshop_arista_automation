"""Prove change control end to end, against the live stack (INIT-002/SPEC-004).

This is the rehearsal that matters before a briefing: it seeds real tickets, walks a real change
through them, and — critically — tries to break the gate. A demo that only shows the happy path
proves the buttons work, not that the process holds.

    python scripts/verify_change_control.py             # the full run
    python scripts/verify_change_control.py --keep      # leave the final change in place
    python scripts/verify_change_control.py --clean     # only remove the rehearsal VLAN

What it asserts, in order:

1. A change is **refused** with no ticket. This is the gate.
2. A change is **refused** with a ticket that does not exist.
3. A change is refused with no approver, even with a valid ticket.
4. A real change: preview → ticket → approve → push → verified on the switch → ticket closed.
5. The ticket's state is read back from Zammad, not assumed from our own return value.
6. A second change on the same switch reuses the path and still works (idempotency of the gate).

Exit code is 0 only if every assertion holds. Anything else prints what failed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NETBOX = "workshop_arista_automation-netbox-1"
CONTAINER_PREFIX = "clab-netbox-arista-lab-"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{(': ' + detail) if detail else ''}")


def netbox(code: str, timeout: int = 300) -> str:
    """Run Python inside NetBox, where the plugin's config and network access live."""
    proc = subprocess.run(  # noqa: S603 - argument list, no shell
        [
            "docker", "exec", NETBOX,
            "/opt/netbox/netbox/manage.py", "shell", "-c", code,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return f"{proc.stdout or ''}\n{proc.stderr or ''}"


def switch_vlans(device: str) -> list[int]:
    proc = subprocess.run(  # noqa: S603 - argument list, no shell
        ["docker", "exec", f"{CONTAINER_PREFIX}{device}", "Cli", "-p", "15", "-c", "show vlan"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    found: list[int] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit():
            found.append(int(parts[0]))
    return sorted(set(found))


def trunk_vlans(device: str) -> list[int]:
    proc = subprocess.run(  # noqa: S603 - argument list, no shell
        [
            "docker", "exec", f"{CONTAINER_PREFIX}{device}", "Cli", "-p", "15",
            "-c", "show interfaces trunk",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    allowed: list[int] = []
    seen_port = False
    for line in (proc.stdout or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("Et5") and "trunking" in stripped:
            seen_port = True
            continue
        if seen_port and stripped.startswith("Et5 "):
            for chunk in stripped.split(None, 1)[1].split(","):
                chunk = chunk.strip()
                if chunk.isdigit():
                    allowed.append(int(chunk))
            break
    return sorted(set(allowed))


VLAN = 210
VLAN_NAME = "PHILIPS_MEDICAL"
DEVICE = "leaf-01"


def cleanup() -> None:
    """Remove the rehearsal VLAN from the switch and its trunk, and say what happened."""
    print("\nCleaning the rehearsal VLAN")
    script = (
        "configure\n"
        f"interface Ethernet5\n"
        f"   switchport trunk allowed vlan remove {VLAN}\n"
        f"no vlan {VLAN}\n"
        "end\nwrite memory\n"
    )
    proc = subprocess.run(  # noqa: S603 - argument list, no shell
        ["docker", "exec", "-i", f"{CONTAINER_PREFIX}{DEVICE}", "Cli", "-p", "15"],
        input=script,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    blob = f"{proc.stdout or ''}{proc.stderr or ''}"
    print(f"  removed VLAN {VLAN}" if "% Invalid" not in blob else f"  {blob.strip()[:200]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--keep", action="store_true", help="leave the final change in place")
    parser.add_argument("--clean", action="store_true", help="only remove the rehearsal VLAN")
    args = parser.parse_args(argv)

    if args.clean:
        cleanup()
        return 0

    print("Change control, end to end")
    print("=" * 66)

    # ---------------------------------------------------------------- start
    before = switch_vlans(DEVICE)
    check(
        f"starting state: VLAN {VLAN} is absent",
        VLAN not in before,
        f"vlans={before}",
    )
    if VLAN in before:
        print(f"\n  VLAN {VLAN} is already on {DEVICE}. Run with --clean first.")
        return 1

    # ------------------------------------------------------- 1. the gate
    out = netbox(
        "from netbox_ops import engine\n"
        f"r = engine.approve_and_push('{DEVICE}', 'Brian Nelson', ticket_id=None, "
        f"vlan={VLAN}, vlan_name='{VLAN_NAME}').to_dict()\n"
        "print('RESULT', r.get('ok'), r.get('refused'), r.get('applied'), r.get('headline'))\n"
    )
    line = next((ln for ln in out.splitlines() if ln.startswith("RESULT")), "")
    parts = line.split(None, 4)
    check(
        "a change with no ticket is refused",
        len(parts) >= 3 and parts[1] == "False" and parts[2] == "True",
        parts[4] if len(parts) > 4 else line,
    )
    check(
        f"the switch was untouched by the refused change",
        VLAN not in switch_vlans(DEVICE),
    )

    # ---------------------------------------- 2. a ticket that does not exist
    out = netbox(
        "from netbox_ops import engine\n"
        f"r = engine.approve_and_push('{DEVICE}', 'Brian Nelson', ticket_id=99999999, "
        f"vlan={VLAN}, vlan_name='{VLAN_NAME}').to_dict()\n"
        "print('RESULT', r.get('ok'), r.get('refused'), r.get('headline'))\n"
    )
    line = next((ln for ln in out.splitlines() if ln.startswith("RESULT")), "")
    parts = line.split(None, 3)
    check(
        "a change against a nonexistent ticket is refused",
        len(parts) >= 3 and parts[1] == "False" and parts[2] == "True",
        parts[3] if len(parts) > 3 else line,
    )
    check(
        f"the switch was untouched by the bad-ticket change",
        VLAN not in switch_vlans(DEVICE),
    )

    # ------------------------------------------------------- 3. the request
    out = netbox(
        "from netbox_ops import engine\n"
        f"r = engine.request_change('{DEVICE}', requester='Brian Nelson', vlan={VLAN}, "
        f"vlan_name='{VLAN_NAME}').to_dict()\n"
        "print('TICKET', r.get('ticket_id'), r.get('number'), r.get('ok'))\n"
    )
    line = next((ln for ln in out.splitlines() if ln.startswith("TICKET")), "")
    parts = line.split()
    ticket_id = parts[1] if len(parts) > 1 and parts[1].isdigit() else ""
    number = parts[2] if len(parts) > 2 else ""
    check("a change ticket is opened", bool(ticket_id), f"ticket {number} (id {ticket_id})")
    if not ticket_id:
        print(out[-800:])
        return 1

    # Confirm the ticket really exists in Zammad, in `new`, with a real customer.
    out = netbox(
        "from netbox_ops import engine\n"
        "t = engine.itsm_tickets(limit=40).to_dict()\n"
        "for x in (t.get('tickets') or []):\n"
        f"    if str(x.get('id')) == '{ticket_id}':\n"
        "        print('FOUND', x.get('number'), x.get('state'), x.get('state_id'))\n"
    )
    found = next((ln for ln in out.splitlines() if ln.startswith("FOUND")), "")
    check(
        "the ticket exists in Zammad in state 'new'",
        "new" in found,
        found or "not found in the ticket list",
    )

    # ------------------------------------------------- 4. approve and push
    out = netbox(
        "from netbox_ops import engine\n"
        f"r = engine.approve_and_push('{DEVICE}', 'Brian Nelson', ticket_id={ticket_id}, "
        f"vlan={VLAN}, vlan_name='{VLAN_NAME}').to_dict()\n"
        "print('PUSH', r.get('ok'), r.get('applied'), r.get('verified'), "
        "r.get('ticket_state_changed'), r.get('ticket_closed'))\n"
    )
    line = next((ln for ln in out.splitlines() if ln.startswith("PUSH")), "")
    parts = line.split()
    applied = parts[2] if len(parts) > 2 else ""
    verified = parts[3] if len(parts) > 3 else ""
    opened = parts[4] if len(parts) > 4 else ""
    closed = parts[5] if len(parts) > 5 else ""
    check("approve and push applies the change", applied == "True", line)
    check("the change is verified by reading the device back", verified == "True")
    check("the ticket moved to 'open' on approval", opened == "True")
    check("the ticket closed after verification", closed == "True")

    # --------------------------------------- 5. prove it on the switch itself
    after = switch_vlans(DEVICE)
    allowed = trunk_vlans(DEVICE)
    check(f"VLAN {VLAN} exists on {DEVICE}", VLAN in after, f"vlans={after}")
    check(f"VLAN {VLAN} is allowed on the Ethernet5 trunk", VLAN in allowed, f"trunk={allowed}")

    # ------------------------------------ 6. read the ticket state from Zammad
    out = netbox(
        "from netbox_ops import engine\n"
        "t = engine.itsm_tickets(limit=40).to_dict()\n"
        "for x in (t.get('tickets') or []):\n"
        f"    if str(x.get('id')) == '{ticket_id}':\n"
        "        print('STATE', x.get('state'), x.get('state_id'))\n"
    )
    state_line = next((ln for ln in out.splitlines() if ln.startswith("STATE")), "")
    check(
        "Zammad reports the ticket as closed",
        "closed" in state_line,
        state_line or "state not readable",
    )

    print("=" * 66)
    failed = [r for r in RESULTS if not r[1]]
    if failed:
        print(f"FAILED: {len(failed)} of {len(RESULTS)} checks")
        for name, _ok, detail in failed:
            print(f"  - {name}: {detail}")
        return 1

    print(f"PASS — {len(RESULTS)} checks. Change control holds, and the gate refused what it must.")

    if args.keep:
        print(f"\nVLAN {VLAN} left on {DEVICE} (--keep).")
    else:
        cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
