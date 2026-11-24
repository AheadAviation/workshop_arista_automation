"""Walk the deliver-a-service workflow over HTTP, exactly as the page does.

This is the rehearsal for the day-two story specifically: plan a service, approve it once, and
prove the VLAN landed on **every** switch in the pair — read from the switches themselves, not
from the API's own return value.

    python scripts/verify_service_flow.py
    python scripts/verify_service_flow.py --service pharmacy --pair pair-1
    python scripts/verify_service_flow.py --clean     # remove what it applied
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

WORKBENCH = "http://127.0.0.1:8099"
CONTAINER_PREFIX = "clab-netbox-arista-lab-"
TRUNK = "Ethernet5"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{(': ' + detail) if detail else ''}")


def post(path: str, payload: dict, timeout: int = 240) -> dict:
    request = urllib.request.Request(
        WORKBENCH + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except ValueError:
            return {"ok": False, "headline": f"HTTP {exc.code}", "detail": body[:200]}


def trunk_vlans(device: str) -> list[int]:
    """The VLANs allowed on the server trunk, read from the switch itself.

    Uses the engine's own `parse_trunks` + `find_trunk`, deliberately. A hand-rolled version of
    this in the verifier was wrong twice over: it looked for the line after the interface in the
    status table (where `Po2000` sits in between), and it compared against `Ethernet5` when the
    switch prints `Et5`. Both made a change that *had* applied look like it had not — a test bug
    that cries wolf on a good result, which is worse than no test.

    The normalisation rule already exists in `workbench/drift.py`. Reusing it means the verifier
    and the page can never disagree about whether a trunk carries a VLAN.
    """
    from workbench.device_reader import parse_trunks
    from workbench.drift import find_trunk

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
    trunks = parse_trunks(proc.stdout or "")
    _found, allowed = find_trunk(trunks, TRUNK)
    return sorted(set(allowed))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--service", default="pharmacy")
    parser.add_argument("--scope", default="mlag-pair")
    parser.add_argument("--pair", default="pair-1")
    parser.add_argument("--clean", action="store_true", help="only remove the service VLAN")
    args = parser.parse_args(argv)

    from workbench import catalogue

    try:
        svc = catalogue.service(args.service)
        switches = catalogue.targets(args.scope, pair=args.pair)
    except Exception as exc:
        print(f"bad arguments: {exc}")
        return 1

    if args.clean:
        print(f"Removing VLAN {svc.vlan} from {', '.join(switches)}")
        for device in switches:
            script = (
                "configure\n"
                f"interface {TRUNK}\n"
                f"   switchport trunk allowed vlan remove {svc.vlan}\n"
                f"no vlan {svc.vlan}\n"
                "end\nwrite memory\n"
            )
            subprocess.run(  # noqa: S603 - argument list, no shell
                ["docker", "exec", "-i", f"{CONTAINER_PREFIX}{device}", "Cli", "-p", "15"],
                input=script, capture_output=True, text=True, timeout=90, check=False,
            )
            print(f"  {device}: removed")
        return 0

    print("Deliver a service, end to end, over HTTP")
    print("=" * 68)

    # Refusals first: the gate is what makes the rest safe to show.
    nameless = post("/api/service/apply", {"change_id": "x", "approver": "  "})
    check("applying without a name is refused", nameless.get("ok") is False,
          nameless.get("headline", ""))

    unstaged = post("/api/service/apply", {"change_id": "service:not:real", "approver": "Brian"})
    check("applying a change that was never staged is refused",
          unstaged.get("ok") is False and unstaged.get("refused") is True,
          unstaged.get("headline", ""))

    bogus = post("/api/service/plan", {"service": "not-a-service", "scope": "all-leaves"})
    check("an unknown service is refused, not guessed",
          bogus.get("ok") is False, bogus.get("headline", ""))

    # Now the real thing.
    before = {d: trunk_vlans(d) for d in switches}
    check(f"VLAN {svc.vlan} starts absent everywhere",
          all(svc.vlan not in v for v in before.values()),
          "; ".join(f"{d}={v}" for d, v in before.items()))

    plan = post(
        "/api/service/plan",
        {"service": args.service, "scope": args.scope, "pair": args.pair},
    )
    check("the plan is produced", plan.get("ok") is True, plan.get("headline", ""))
    if not plan.get("ok") or plan.get("no_drift"):
        print(json.dumps(plan, indent=2)[:800])
        return 1

    sentence = plan.get("headline") or ""
    check("the change reads as a sentence, not a VLAN number",
          svc.name in sentence and "switch" in sentence, sentence)
    check("every switch in scope is covered",
          sorted(plan.get("to_change") or []) == sorted(switches),
          f"{plan.get('to_change')}")

    change_id = plan.get("change_id") or ""
    applied = post("/api/service/apply", {"change_id": change_id, "approver": "Brian Nelson"})
    check("one approval applies to every switch", applied.get("ok") is True,
          applied.get("headline", ""))
    check("nothing failed",
          not applied.get("failures"), str(applied.get("failures"))[:120])

    # Prove it on the hardware, one switch at a time.
    for device in switches:
        after = trunk_vlans(device)
        check(f"VLAN {svc.vlan} is on {device}'s {TRUNK} trunk", svc.vlan in after, f"{after}")

    replay = post("/api/service/apply", {"change_id": change_id, "approver": "Brian Nelson"})
    check("the same change cannot be applied twice",
          replay.get("ok") is False and replay.get("refused") is True,
          replay.get("headline", ""))

    print("=" * 68)
    failed = [r for r in RESULTS if not r[1]]
    if failed:
        print(f"FAILED: {len(failed)} of {len(RESULTS)}")
        for name, _ok, detail in failed:
            print(f"  - {name}: {detail}")
        return 1

    print(f"PASS — {len(RESULTS)} checks. One sentence, one approval, {len(switches)} switches.")
    print(f"\nUndo with: python scripts/verify_service_flow.py --clean --service {args.service} --pair {args.pair}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
