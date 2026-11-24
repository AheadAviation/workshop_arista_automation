#!/usr/bin/env python3
"""End-to-end live verification of the Network Ops Workshop plugin (INIT-004).

Drives the exact browser HTTP flow (login -> CSRF -> POST/GET endpoints) through every
Quick Start step (Clean Slate -> Import -> Generate Topology -> Generate Configs ->
Deploy -> Push Full Configs -> Live Fabric Checks -> Full Validation Suite) plus
Day 2 Diff and Backup.
"""

from __future__ import annotations

import re
import sys
import time

import requests

BASE = "http://localhost:8888"
USER = "admin"
PASS = "admin"


def wait_for_netbox(timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{BASE}/login/", timeout=5)
            if r.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(3)
    raise RuntimeError(f"NetBox did not become ready at {BASE} within {timeout}s")


def login(session: requests.Session) -> None:
    r = session.get(f"{BASE}/login/", timeout=15)
    r.raise_for_status()
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("Could not find CSRF token on /login/")
    resp = session.post(
        f"{BASE}/login/",
        data={"username": USER, "password": PASS, "csrfmiddlewaretoken": m.group(1), "next": "/plugins/ops/workshop/"},
        headers={"Referer": f"{BASE}/login/"},
        timeout=20,
    )
    resp.raise_for_status()
    if "Sign in" in resp.text and "Quick Start Walkthrough" not in resp.text:
        raise RuntimeError("NetBox login failed")


def csrf_from_workshop(session: requests.Session) -> tuple[str, str]:
    r = session.get(f"{BASE}/plugins/ops/workshop/", timeout=30)
    r.raise_for_status()
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("Could not find CSRF token on /plugins/ops/workshop/")
    return m.group(1), r.text


def post_action(session: requests.Session, path: str, data: dict | None = None, timeout: int = 600) -> tuple[bool, str, str]:
    token, _ = csrf_from_workshop(session)
    payload = {"csrfmiddlewaretoken": token, **(data or {})}
    r = session.post(
        f"{BASE}{path}",
        data=payload,
        headers={"Referer": f"{BASE}/plugins/ops/workshop/"},
        timeout=timeout,
    )
    r.raise_for_status()
    ok = "alert-success" in r.text
    m = re.search(r'<div class="alert alert-(?:success|danger)">\s*<strong>([^<]+)</strong>', r.text)
    headline = m.group(1).strip() if m else f"HTTP {r.status_code}"
    dm = re.search(r'<pre[^>]*>(.*?)</pre>', r.text, re.S)
    detail = dm.group(1).strip()[-600:] if dm else ""
    return ok, headline, detail


def get_action(session: requests.Session, path: str, timeout: int = 180) -> tuple[bool, str, str]:
    r = session.get(f"{BASE}{path}", timeout=timeout)
    r.raise_for_status()
    ok = "alert-success" in r.text
    m = re.search(r'<div class="alert alert-(?:success|danger)">\s*<strong>([^<]+)</strong>', r.text)
    headline = m.group(1).strip() if m else f"HTTP {r.status_code}"
    dm = re.search(r'<pre[^>]*>(.*?)</pre>', r.text, re.S)
    detail = dm.group(1).strip()[-600:] if dm else ""
    return ok, headline, detail


def main() -> int:
    skip_deploy = "--skip-deploy" in sys.argv
    print("Waiting for NetBox...")
    wait_for_netbox()
    s = requests.Session()
    login(s)
    print("Logged in to NetBox.")

    token, html = csrf_from_workshop(s)
    assert "Quick Start Walkthrough" in html, "Workshop tab did not render"
    assert "Day 2 Operations" in html, "Day 2 panel did not render"
    print("OK: /plugins/ops/workshop/ renders cleanly.")

    if not skip_deploy:
        steps = [
            ("Step 1 — Clean Slate (Full Reset)", "POST", "/plugins/ops/workshop/action/clean/", {"clear_netbox": "1"}),
            ("Step 3 — Import Network Data", "POST", "/plugins/ops/workshop/action/import/", None),
            ("Step 4 — Generate Topology", "POST", "/plugins/ops/workshop/action/topology/", None),
            ("Step 4b — Inspect Topology YAML", "GET", "/plugins/ops/workshop/view/topology/", None),
            ("Step 5 — Generate Configs", "POST", "/plugins/ops/workshop/action/configs/", None),
            ("Step 5b — Inspect spine-01.cfg", "GET", "/plugins/ops/workshop/view/config/spine-01/", None),
            ("Step 6 — Deploy ContainerLab Fabric", "POST", "/plugins/ops/workshop/action/deploy/", None),
        ]

        for label, method, path, data in steps:
            t0 = time.time()
            ok, headline, detail = post_action(s, path, data) if method == "POST" else get_action(s, path)
            dt = time.time() - t0
            status = "PASS" if ok else "FAIL"
            print(f"[{status}] {label} ({dt:.1f}s): {headline}")
            if not ok:
                if detail:
                    print(detail)
                return 1

        print("Waiting 65s for 6 cEOS switches to finish booting...")
        time.sleep(65)

    for label, method, path, data in [
        ("Step 7 — Push Full Configs", "POST", "/plugins/ops/workshop/action/push/", None),
    ]:
        t0 = time.time()
        ok, headline, detail = post_action(s, path, data)
        dt = time.time() - t0
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {label} ({dt:.1f}s): {headline}")
        if not ok:
            if detail:
                print(detail)
            return 1

    print("Waiting 30s for BGP/MLAG/EVPN convergence...")
    time.sleep(30)

    post_steps = [
        ("Step 8 — Live Fabric Verification (eAPI)", "GET", "/plugins/ops/workshop/action/verify/", None),
        ("Step 9 — Full Validation Suite (test_all.yml)", "POST", "/plugins/ops/workshop/action/validate/", {"quick": "0"}),
        ("Day 2 — Diff Running vs Generated", "GET", "/plugins/ops/workshop/action/diff/", None),
        ("Day 2 — Backup Switches", "POST", "/plugins/ops/workshop/action/backup/", None),
    ]
    failed = 0
    for label, method, path, data in post_steps:
        t0 = time.time()
        ok, headline, detail = post_action(s, path, data) if method == "POST" else get_action(s, path)
        dt = time.time() - t0
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {label} ({dt:.1f}s): {headline}")
        if not ok:
            if detail:
                print(detail)
            failed += 1

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
