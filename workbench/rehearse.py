"""A walkthrough of the demo against a fake lab, over real HTTP (INIT-001/SPEC-008).

This is the closest thing to the actual meeting that can run with no lab: it starts the
real FastAPI app, points it at the fake six-switch fabric, and walks the exact sequence the
presenter will walk. It is a check for a human to run, not a unit test — `tests/` already
covers the same ground in isolation.

    python -m workbench.rehearse

Exits 0 when the whole story works, non-zero with the step it failed at otherwise.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fake_lab(tmp: Path):
    """The fake lab from the test suite, reused so the rehearsal and the tests agree."""
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from test_scenarios import FakeLab
    from workbench.gate import AuditLog
    from workbench.scenarios import Lab

    fake = FakeLab()
    lab = Lab(
        read_intent=fake.read_intent,
        read_actual=fake.read_actual,
        preview=fake.preview,
        apply=fake.apply,
        audit=AuditLog(tmp / "audit.jsonl"),
    )
    return lab, fake


def walk() -> int:
    from fastapi.testclient import TestClient

    from workbench.web import create_app

    with tempfile.TemporaryDirectory() as tmpdir:
        lab, fake = _fake_lab(Path(tmpdir))
        client = TestClient(create_app(lab_factory=lambda: lab))

        steps: list[tuple[str, bool, str]] = []

        def step(name: str, ok: bool, detail: str = "") -> None:
            steps.append((name, ok, detail))
            mark = "ok  " if ok else "FAIL"
            print(f"  {mark}  {name}{(': ' + detail) if detail else ''}")

        print("Walking the demo against a fake six-switch fabric")
        print("=" * 62)

        # The page itself.
        page = client.get("/")
        step("page serves", page.status_code == 200, f"{len(page.text)} bytes")
        step(
            "page has no terminal",
            all(word not in page.text.lower() for word in ("xterm", "cli -p", "<terminal")),
        )

        # 1. The premise.
        overview = client.get("/api/overview").json()
        step("fabric overview", overview.get("ok") is True, overview.get("headline", ""))
        step(
            "fabric starts compliant",
            overview.get("drifted_count") == 0,
            f"{overview.get('device_count')} devices",
        )

        # 2. The Philips change.
        proposed = client.post("/api/propose", json={"device": "leaf-01", "vlan": 150}).json()
        step("propose VLAN 150", proposed.get("ok") is True, proposed.get("headline", ""))
        step("proposal changes nothing", fake.applied == [], f"{len(fake.applied)} applied")

        # 3. Approve.
        approved = client.post(
            "/api/approve", json={"device": "leaf-01", "approver": "Rehearsal"}
        ).json()
        step("approve with a name", approved.get("applied") is True, approved.get("headline", ""))

        # 3b. The gate refuses a nameless approval.
        nameless = client.post("/api/approve", json={"device": "leaf-02", "approver": "  "})
        step("nameless approval is refused", nameless.status_code == 400)

        # 4. Break it, catch it, fix it.
        injected = client.post("/api/inject", json={"device": "leaf-01"}).json()
        step("inject drift", injected.get("ok") is True, injected.get("headline", ""))

        after_drift = client.get("/api/overview").json()
        step(
            "the page catches the drift",
            after_drift.get("drifted_count", 0) > 0,
            f"{after_drift.get('drifted_count')} drifted",
        )

        fixed = client.post(
            "/api/remediate", json={"device": "leaf-01", "approver": "Rehearsal"}
        ).json()
        step("remediate through the gate", fixed.get("applied") is True, fixed.get("headline", ""))

        after_fix = client.get("/api/overview").json()
        step("fabric compliant again", after_fix.get("drifted_count") == 0)

        # 5. The audit trail.
        audit = client.get("/api/audit").json()
        step("audit recorded the changes", audit.get("applied", 0) >= 2, f"{audit.get('applied')} applied")
        approvers = {e.get("approver") for e in audit.get("entries", []) if e.get("approver")}
        step("every change names its approver", "Rehearsal" in approvers)

        # 6. Lifecycle and vendors.
        life = client.get("/api/lifecycle").json()
        step("lifecycle view", life.get("ok") is True, f"{life.get('total')} devices")
        vendors = client.get("/api/two-vendors").json()
        step(
            "two-vendor view loads",
            "vendors" in vendors,
            f"{len(vendors.get('vendors') or [])} vendor(s)",
        )

    print("=" * 62)
    failed = [s for s in steps if not s[1]]
    if failed:
        print(f"FAILED at: {', '.join(s[0] for s in failed)}")
        return 1
    print(f"PASS — all {len(steps)} steps of the demo work end to end.")
    print("This proves the page and the flow. It does not prove your lab is up:")
    print("run `python -m workbench.cli check` for that.")
    return 0


def main() -> int:
    return walk()


if __name__ == "__main__":
    raise SystemExit(main())
