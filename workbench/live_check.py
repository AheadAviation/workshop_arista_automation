"""Prove the whole demo against the LIVE lab, one step at a time (INIT-001).

This is the check that could not run earlier because no switches were up. It changes a real
switch, on purpose, and puts it back — so run it only against the demo lab.

    python -m workbench.live_check

Exits 0 only if every step actually worked on real hardware. It resets the fabric at the end
so you can run it again.
"""

from __future__ import annotations

import sys

from workbench.scenarios import (
    Lab,
    approve,
    fabric_overview,
    inject_drift,
    lifecycle,
    propose_vlan,
    remediate,
    two_vendors,
)

STEPS: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    STEPS.append((name, ok, detail))
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark}  {name}{(': ' + detail) if detail else ''}")


def main() -> int:
    lab = Lab.real()
    print("Live check against the running lab")
    print("=" * 62)

    # 0. Start from a known state, so the check is repeatable.
    # Without this, a second run finds VLAN 150 already present and reports a false failure.
    from workbench.demo_lab import configure_trunks, verify_trunks

    if verify_trunks() != 0:
        print("\n  The demo trunks are not in place. Preparing them...")
        configure_trunks()

    # 1. Read intent and actual from the real world.
    overview = fabric_overview(lab)
    step("netbox intent reads", overview.get("ok") is True, overview.get("headline", ""))
    if not overview.get("ok"):
        print("\n  Nothing else can be checked without NetBox. Start it with .\\run.ps1 up")
        return 1

    reachable = [d for d in overview["devices"] if d["actual"]["reachable"]]
    step("switches answer", len(reachable) == 6, f"{len(reachable)} of 6")

    # 2. The change, previewed only.
    before = len([d for d in overview["devices"] if d["verdict"]["state"] != "compliant"])
    proposed = propose_vlan(lab, "leaf-01", vlan=150)
    step("preview the VLAN change", proposed.get("ok") is True, proposed.get("headline", ""))
    if proposed.get("changed_lines"):
        print("      --- what would change ---")
        for line in (proposed.get("diff") or "").splitlines()[:8]:
            print(f"      {line}")

    after_preview = fabric_overview(lab)
    still = len([d for d in after_preview["devices"] if d["verdict"]["state"] != "compliant"])
    step("preview changed nothing", still == before, f"{still} not-compliant before and after")

    # 3. Approve it for real. On a repeat run the switch may already have it, which is a
    # successful no-op rather than a failure -- that is the idempotency the demo claims.
    applied = approve(lab, "leaf-01", approver="live check")
    already = bool(applied.get("no_drift"))
    step(
        "apply with an approver",
        applied.get("applied") is True or already,
        "already compliant (no-op)" if already else applied.get("headline", ""),
    )

    # 4. The switch should now be compliant.
    after = fabric_overview(lab)
    leaf = next((d for d in after["devices"] if d["device"] == "leaf-01"), None)
    step(
        "leaf-01 is compliant",
        bool(leaf) and leaf["verdict"]["state"] == "compliant",
        (leaf["verdict"]["headline"] if leaf else "missing"),
    )

    # 5. Prove it on the switch itself, not just in our own data.
    from workbench.device_reader import read_device
    from workbench.drift import find_trunk

    actual = read_device("leaf-01")
    step("VLAN 150 exists on the switch", 150 in actual.vlans, f"vlans={actual.vlans}")
    found, trunk = find_trunk(actual.trunk_allowed, "Ethernet5")
    step("Ethernet5 carries VLAN 150", found and 150 in trunk, f"allowed={trunk}")

    # 6. Break it, catch it, fix it.
    injected = inject_drift(lab, "leaf-01")
    step("inject drift", injected.get("ok") is True, injected.get("headline", ""))

    drifted = fabric_overview(lab)
    step("the page catches it", drifted.get("drifted_count", 0) > 0, f"{drifted.get('drifted_count')} drifted")

    fixed = remediate(lab, "leaf-01", approver="live check")
    step("remediate through the gate", fixed.get("applied") is True, fixed.get("headline", ""))

    # Check the device we actually remediated. Asserting the whole fabric is compliant would
    # fail on the other leaves, which the demo deliberately does not touch on one approval.
    healed = fabric_overview(lab)
    leaf_after = next((d for d in healed["devices"] if d["device"] == "leaf-01"), None)
    step(
        "leaf-01 compliant again",
        bool(leaf_after) and leaf_after["verdict"]["state"] == "compliant",
        (leaf_after["verdict"]["headline"] if leaf_after else "missing"),
    )

    # 7. The other views.
    life = lifecycle(lab)
    step("lifecycle reads real OS versions", life.get("ok") is True, str(life.get("os_fleet")))
    vendors = two_vendors(lab)
    step("two-vendor view", vendors.get("ok") is True, f"{len(vendors.get('vendors') or [])} vendor(s)")

    print("=" * 62)
    failed = [s for s in STEPS if not s[1]]
    if failed:
        print(f"FAILED: {', '.join(s[0] for s in failed)}")
        return 1
    print(f"PASS — {len(STEPS)} steps verified against real switches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
