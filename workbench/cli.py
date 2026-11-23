"""The workbench command line (INIT-001/SPEC-008).

Four commands, and only four, because a presenter under pressure at 8am should not have
to read a manual:

    python -m workbench.cli serve        # start the page
    python -m workbench.cli check        # the morning rehearsal, under ten minutes
    python -m workbench.cli reset        # put the lab back, keep the audit
    python -m workbench.cli overview     # the fabric, in a terminal, for a smoke test

`check` is the important one. It is the difference between "I think it will work" and
"I watched it work ten minutes ago".
"""

from __future__ import annotations

import argparse
import sys

from workbench.gate import AuditLog
from workbench.scenarios import DEMO_DEVICES, Lab

OK = "  ok    "
WARN = "  warn  "
FAIL = "  FAIL  "


def _print(status: str, label: str, detail: str = "") -> None:
    line = f"{status}{label}"
    if detail:
        line += f": {detail}"
    print(line)


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #


def check(*, verbose: bool = False, lab: Lab | None = None) -> int:
    """The rehearsal. Every step is something that would embarrass us if it failed live.

    Returns 0 when the demo is ready, 1 otherwise. `warn` never fails the run: a warning
    means "know about this", not "cancel the meeting".
    """
    failures = 0
    warnings = 0

    print("Rehearsal: NetBox Automation Workbench")
    print("=" * 60)

    # 1. NetBox intent
    lab = lab or Lab.real()
    try:
        intended = lab.read_intent()
    except Exception as exc:
        _print(FAIL, "NetBox intended state", str(exc)[:160])
        print("\n  The page will still load, but it will show 'Could not read intended "
              "state'. Start the stack with: .\\run.ps1 up")
        return 1

    _print(OK, "NetBox intended state", f"{len(intended)} devices")
    if not intended:
        _print(FAIL, "fabric devices", "none found; run .\\run.ps1 import")
        failures += 1

    # 2. Each device reachable and readable
    reachable = 0
    for device in DEMO_DEVICES:
        actual = lab.read_actual(device)
        if actual.reachable:
            reachable += 1
            if verbose:
                _print(OK, f"{device}", f"vlan {len(actual.vlans)}, os {actual.os_version or '?'}")
        else:
            _print(FAIL, device, actual.error[:120])
            failures += 1

    if reachable:
        _print(OK, "devices answering", f"{reachable} of {len(DEMO_DEVICES)}")

    # 3. The trunk overlay reaches real ports
    leaves = [d for d in intended.values() if d.trunks]
    if leaves:
        _print(OK, "demo trunk overlay", f"{len(leaves)} leaf devices with server trunks")
    else:
        _print(FAIL, "demo trunk overlay", "no leaf has trunks; the change story needs them")
        failures += 1

    # 4. A preview works, which is the thing the button does.
    # Goes through the lab's own preview seam, not the real executor directly, so this
    # check exercises the same path the page does — and so it can be rehearsed without a
    # lab standing up.
    if reachable:
        from workbench.eos_executor import EosApplyError, vlan_across_trunks_plan

        device = leaves[0].device if leaves else DEMO_DEVICES[0]
        intent = intended[device]
        if intent.trunks:
            plan = vlan_across_trunks_plan(device, 150, "REHEARSAL", intent.trunks)
            try:
                result = lab.preview(plan)
                _print(
                    OK,
                    "dry-run preview",
                    f"{result.changed_lines} line(s) would change on {device}"
                    if not result.is_empty
                    else f"{device} already matches; nothing would change",
                )
            except EosApplyError as exc:
                _print(FAIL, "dry-run preview", str(exc)[:160])
                failures += 1
        else:
            _print(WARN, "dry-run preview", f"{device} has no trunks to preview against")
            warnings += 1

    # 5. The audit log is writable — a change that cannot be recorded must not be applied
    audit = AuditLog()
    try:
        audit.path.parent.mkdir(parents=True, exist_ok=True)
        with audit.path.open("a", encoding="utf-8"):
            pass
        _print(OK, "audit log writable", str(audit.path))
    except OSError as exc:
        _print(FAIL, "audit log", f"cannot write {audit.path}: {exc}")
        failures += 1

    # 6. The page's static files are present
    from workbench.web import STATIC

    missing = [n for n in ("index.html", "workbench.css", "workbench.js") if not (STATIC / n).exists()]
    if missing:
        _print(FAIL, "page assets", f"missing {', '.join(missing)}")
        failures += 1
    else:
        _print(OK, "page assets", "index.html, workbench.css, workbench.js")

    print("=" * 60)
    if failures:
        print(f"NOT READY: {failures} problem(s), {warnings} warning(s).")
        print("Fix the FAIL lines above, then run this again.")
        return 1
    print(f"READY. {warnings} warning(s).")
    print("Start the page with:  python -m workbench.cli serve")
    return 0


# --------------------------------------------------------------------------- #
# reset
# --------------------------------------------------------------------------- #


def reset(*, assume_yes: bool = False, keep_audit: bool = True, lab: Lab | None = None) -> int:
    """Put the intended-state picture back, without touching the audit record.

    What it does: applies the intended state to every demo trunk device, additively, via
    the same executor the demo uses — so a reset is itself an audited change.

    What it does *not* do: clear the audit log. A reset puts the lab back; it does not
    rewrite history. Erasing records in front of a CISO would be the most alarming thing
    this demo could do, so it is not offered. `keep_audit` exists only so a caller can see
    plainly that the option is deliberately absent rather than forgotten.
    """
    if not keep_audit:
        print(
            "Refusing: this tool never clears the audit log. A reset returns the lab to "
            "its starting state; it does not rewrite what happened.",
            file=sys.stderr,
        )
        return 2

    from workbench.scenarios import remediate

    lab = lab or Lab.real()
    print("Resetting the demo fabric to intended state")
    print("  (this applies real changes to the switches, recorded in the audit log)")
    print("  (the audit log is NOT cleared)")

    if not assume_yes:
        try:
            reply = input("Continue? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            reply = ""
        if reply not in ("y", "yes"):
            print("Nothing was changed.")
            return 0

    try:
        intended = lab.read_intent()
    except Exception as exc:
        print(f"  could not read intended state: {exc}")
        return 1

    problems = 0
    for device, intent in sorted(intended.items()):
        if not intent.trunks:
            continue
        result = remediate(lab, device, approver="demo reset")
        if result.get("ok"):
            state = "already compliant" if result.get("no_drift") else "remediated"
            print(f"  {device}: {state}")
        else:
            print(f"  {device}: FAILED — {result.get('headline', '')} {result.get('detail', '')}")
            problems += 1

    # Record the reset itself, so the record shows the demo being put back.
    from workbench.contracts import AuditEntry
    from workbench.gate import append_entry

    append_entry(
        lab.audit,
        AuditEntry(
            kind="reset",
            device="fabric",
            summary=f"demo reset: {len(intended)} devices returned to intended state",
            autonomy="assist",
            approver="demo reset",
        ),
    )

    print("\nDone." if not problems else f"\nDone, with {problems} problem(s).")
    return 0 if not problems else 1


# --------------------------------------------------------------------------- #
# overview
# --------------------------------------------------------------------------- #


def overview(*, as_json: bool = False, lab: Lab | None = None) -> int:
    """The fabric in a terminal, for a fast smoke test before opening the page."""
    import json

    from workbench.scenarios import fabric_overview

    result = fabric_overview(lab or Lab.real())
    if as_json:
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if not result.get("ok"):
        print(f"{result.get('headline')}: {result.get('detail')}")
        return 1

    print(result["headline"])
    print()
    for device in result["devices"]:
        verdict = device["verdict"]
        mark = "ok  " if verdict["state"] == "compliant" else "DRIFT"
        print(f"  {mark}  {device['device']:<10} {verdict['headline']}")
    return 0


# --------------------------------------------------------------------------- #
# serve
# --------------------------------------------------------------------------- #


def serve() -> int:
    from workbench.web import main

    return main()


def walkthrough() -> int:
    """The whole demo, against a fake lab. Proves the flow without a real network."""
    from workbench.rehearse import walk

    return walk()


# --------------------------------------------------------------------------- #
# the parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m workbench.cli",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="start the page on http://127.0.0.1:8099")

    sub.add_parser(
        "walkthrough",
        help="walk the whole demo against a fake lab, with no lab running (proves the flow)",
    )

    chk = sub.add_parser("check", help="the morning rehearsal; exits 1 if not ready")
    chk.add_argument("--verbose", action="store_true", help="show each device")

    rst = sub.add_parser("reset", help="put the lab back to intended state (keeps the audit)")
    rst.add_argument("--yes", action="store_true", help="do not ask")

    ov = sub.add_parser("overview", help="the fabric in the terminal, for a smoke test")
    ov.add_argument("--json", action="store_true", help="machine-readable output")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return serve()
    if args.command == "walkthrough":
        return walkthrough()
    if args.command == "check":
        return check(verbose=args.verbose)
    if args.command == "reset":
        return reset(assume_yes=args.yes)
    if args.command == "overview":
        return overview(as_json=args.json)
    return 1


if __name__ == "__main__":
    sys.exit(main())
