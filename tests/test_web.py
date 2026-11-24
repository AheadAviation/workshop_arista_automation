"""Web layer tests (INIT-001/SPEC-008).

The route layer is tested against the fake lab, so the page's whole contract is proven
with no NetBox, no switches and no docker.

Two properties get the most attention here, because both are demo-ending if wrong:
an approver is mandatory at the HTTP boundary as well as in the gate, and no endpoint
returns a 500 — a stack trace on screen is the failure mode this page exists to avoid.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from workbench.contracts import Actual, Intent, Plan, Preview  # noqa: E402
from workbench.gate import AuditLog  # noqa: E402
from workbench.intent_source import BASE_VLANS  # noqa: E402
from workbench.scenarios import DEMO_DEVICES, Lab  # noqa: E402
from workbench.web import create_app  # noqa: E402

from test_scenarios import FakeLab  # noqa: E402


@pytest.fixture
def client(tmp_path: Path):
    from fastapi.testclient import TestClient

    fake = FakeLab()
    lab = Lab(
        read_intent=fake.read_intent,
        read_actual=fake.read_actual,
        preview=fake.preview,
        apply=fake.apply,
        audit=AuditLog(tmp_path / "audit.jsonl"),
        # The page's own flows record into the source of truth before touching a switch, so
        # the fake needs a writer for the approving paths to work at all.
        write_ipam=fake.record_ipam,
    )
    app = create_app(lab_factory=lambda: lab)
    return TestClient(app), fake, lab


# --------------------------------------------------------------------------- #
# the page itself
# --------------------------------------------------------------------------- #


def test_index_is_served(client):
    c, _, _ = client
    response = c.get("/")
    assert response.status_code == 200
    assert "Network Automation Workbench" in response.text


def test_page_contains_no_terminal_or_cli(client):
    """Brian Nelson's brief: GUI front end, stay away from the CLI in this room."""
    c, _, _ = client
    body = c.get("/").text.lower()
    for forbidden in ("<terminal", "xterm", "cli -p", "bash", "shell"):
        assert forbidden not in body, f"{forbidden!r} must not appear on the page"


def test_static_assets_are_served(client):
    c, _, _ = client
    assert c.get("/static/workbench.css").status_code == 200
    assert c.get("/static/workbench.js").status_code == 200


def test_only_allowlisted_static_files_are_served(client):
    """No path is ever built from user input, so a traversal attempt simply 404s."""
    c, _, _ = client
    assert c.get("/static/../../etc/passwd").status_code in (404, 400)
    assert c.get("/static/secret.txt").status_code == 404


def test_the_page_never_inserts_untrusted_text_as_markup(client):
    """Device output reaches this page; it must never be inserted as HTML.

    Pinned mechanically rather than promised in a comment: a future edit that reaches for
    innerHTML fails here instead of in front of a customer.
    """
    js = (REPO_ROOT / "workbench" / "static" / "workbench.js").read_text(encoding="utf-8")
    code_lines = [
        line for line in js.splitlines()
        if "innerHTML" in line and not line.strip().startswith(("*", "//"))
    ]
    assert code_lines == [], f"innerHTML must not be used to render device data: {code_lines}"


# --------------------------------------------------------------------------- #
# the presenter guide
# --------------------------------------------------------------------------- #


def test_the_guide_is_served(client):
    c, _, _ = client
    response = c.get("/guide")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "step by step" in response.text.lower()


def test_the_guide_has_no_unfilled_placeholders(client):
    """A placeholder with no value behind it renders an empty href that nobody notices
    until they click it during the meeting."""
    c, _, _ = client
    assert "{{" not in c.get("/guide").text


def test_every_external_link_on_the_guide_resolves_to_a_real_address(client):
    """The guide links to NetBox and to the ITSM stack. Those ports live in `urls()` and
    nowhere else, so a deployment that moves one cannot leave a dead link behind."""
    from workbench.web import urls

    c, _, _ = client
    page = c.get("/guide").text
    known = set(urls().values())
    links = set(re.findall(r'href="(http[^"]+)"', page))
    assert links, "the guide should link somewhere"

    unknown = [
        link for link in links
        if not any(link == url or link.startswith(url + "/") for url in known)
    ]
    assert unknown == [], f"links not built from urls(): {unknown} (known: {sorted(known)})"


def test_every_in_page_anchor_on_the_guide_has_a_target(client):
    c, _, _ = client
    page = c.get("/guide").text
    anchors = set(re.findall(r'href="#([A-Za-z0-9_-]+)"', page))
    targets = set(re.findall(r'id="([A-Za-z0-9_-]+)"', page))
    assert anchors, "the contents rail should have anchors"
    assert anchors - targets == set(), f"anchors with no target: {sorted(anchors - targets)}"


def test_every_guide_step_has_a_unique_id_for_its_tick(client):
    """The tick state is keyed by `data-step`. A duplicate id makes two steps share one
    tick, which reads as a bug in the presenter's own checklist."""
    c, _, _ = client
    steps = re.findall(r'data-step="([A-Za-z0-9_-]+)"', c.get("/guide").text)
    assert len(steps) >= 15, f"expected the full run, found {len(steps)} steps"
    assert len(steps) == len(set(steps)), "duplicate step ids"


def test_the_guide_never_prints_a_secret(client):
    """It is a document shown on a laptop, sometimes projected by accident."""
    c, _, _ = client
    page = c.get("/guide").text
    for secret in ("0123456789abcdef0123456789abcdef01234567", "4aroyvDrKCLlDhnA0C1"):
        assert secret not in page


def test_the_guide_assets_are_served(client):
    c, _, _ = client
    assert c.get("/static/guide.css").status_code == 200
    assert c.get("/static/guide.js").status_code == 200


def test_the_guide_js_survives_being_loaded_elsewhere():
    """It is a shared static file. If it is ever included on a page without the guide's
    elements it must return, not throw."""
    js = (REPO_ROOT / "workbench" / "static" / "guide.js").read_text(encoding="utf-8")
    assert "if (boxes.length === 0) return;" in js


def test_the_workbench_links_to_the_guide(client):
    c, _, _ = client
    assert 'href="/guide"' in c.get("/").text


def test_urls_reads_the_environment_so_links_follow_a_moved_port(monkeypatch):
    from workbench.web import urls

    monkeypatch.setenv("WORKBENCH_BIND", "0.0.0.0")
    monkeypatch.setenv("WORKBENCH_PORT", "9100")
    monkeypatch.setenv("NETBOX_PUBLIC_URL", "http://netbox.internal:8000/")
    monkeypatch.setenv("ITSM_PUBLIC_URL", "http://itsm.internal/")

    resolved = urls()
    # A bind address is not a browser address; 0.0.0.0 must become a followable host.
    assert resolved["workbench"] == "http://127.0.0.1:9100"
    assert resolved["netbox"] == "http://netbox.internal:8000"
    assert resolved["itsm"] == "http://itsm.internal"


def test_render_guide_refuses_an_unknown_placeholder():
    """Better a loud failure in the rehearsal than a silently empty href on the page."""
    from workbench.web import render_guide

    with pytest.raises(KeyError):
        render_guide(template="<a href=\"{{nowhere}}\">x</a>")


# --------------------------------------------------------------------------- #
# the service-catalogue routes
# --------------------------------------------------------------------------- #
# These exercise the HTTP routes, not the functions behind them. That distinction matters:
# an earlier version of the routes passed keyword arguments to a helper that only accepted
# positional ones, and every service-endpoint call became a 500 while the underlying
# functions were perfectly fine. Unit tests on the functions alone would not have caught it.


def test_the_catalogue_route_returns_services_and_scopes(client):
    c, _, _ = client
    payload = c.get("/api/catalogue").json()

    assert payload["ok"] is True
    assert len(payload["services"]) >= 4
    assert payload["scopes"]
    keys = {s["key"] for s in payload["services"]}
    assert "pharmacy" in keys


def test_the_catalogue_route_does_not_leak_anything_unexpected(client):
    """The page renders this directly, so every field has to be something it can show."""
    c, _, _ = client
    for svc in c.get("/api/catalogue").json()["services"]:
        assert set(svc) == {"key", "name", "vlan", "purpose", "default_scope"}


def test_planning_a_service_over_http_returns_a_sentence(client):
    c, _, _ = client
    payload = c.post(
        "/api/service/plan",
        json={"service": "pharmacy", "scope": "mlag-pair", "pair": "pair-1"},
    ).json()

    assert payload["ok"] is True, payload
    assert "Pharmacy" in payload["headline"]
    assert payload["to_change"], "something must need changing"


def test_planning_without_a_service_is_a_400_not_a_500(client):
    c, _, _ = client
    response = c.post("/api/service/plan", json={"scope": "mlag-pair"})
    assert response.status_code == 400
    assert "choose" in response.json()["headline"].lower()


def test_planning_an_unknown_service_is_shaped_not_raised(client):
    """A bad service is a page state, never a stack trace."""
    c, _, _ = client
    response = c.post("/api/service/plan", json={"service": "nope", "scope": "all-leaves"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert "not a change this network offers" in payload["headline"]


def test_applying_a_service_over_http_requires_an_approver(client):
    c, fake, _ = client
    response = c.post("/api/service/apply", json={"change_id": "anything", "approver": "  "})
    assert response.status_code == 400
    assert "approver" in response.json()["headline"].lower()
    assert fake.applied == []


def test_applying_without_a_change_id_is_refused(client):
    c, fake, _ = client
    response = c.post("/api/service/apply", json={"approver": "Brian Nelson"})
    assert response.status_code == 400
    assert "preview" in response.json()["headline"].lower()
    assert fake.applied == []


def test_the_full_service_flow_through_http(client):
    """Plan then apply, over HTTP, as the page does — two switches from one approval."""
    c, fake, lab = client

    plan = c.post(
        "/api/service/plan",
        json={"service": "pharmacy", "scope": "mlag-pair", "pair": "pair-1"},
    ).json()
    assert plan["ok"] is True
    change_id = plan["change_id"]

    applied = c.post(
        "/api/service/apply", json={"change_id": change_id, "approver": "Brian Nelson"}
    ).json()

    assert applied["ok"] is True, applied
    assert sorted(item["device"] for item in applied["applied"]) == ["leaf-01", "leaf-02"]
    assert len(fake.applied) == 2
    assert len(lab.audit.applied_changes()) == 2


def test_the_apply_route_refuses_a_change_that_was_never_staged(client):
    c, fake, _ = client
    payload = c.post(
        "/api/service/apply", json={"change_id": "service:made:up", "approver": "Brian"}
    ).json()

    assert payload["ok"] is False
    assert payload["refused"] is True
    assert fake.applied == []


def test_the_page_does_not_render_a_diff_without_a_gate(client):
    """The approve control is only rendered for a non-empty, unapplied change."""
    js = (REPO_ROOT / "workbench" / "static" / "workbench.js").read_text(encoding="utf-8")
    assert "withApprove && result.ok && !result.no_drift && !result.applied" in js


def test_devices_endpoint_lists_the_demo_fabric(client):
    c, _, _ = client
    payload = c.get("/api/devices").json()
    assert payload["devices"] == list(DEMO_DEVICES)


def test_health_reports_the_audit_path(client):
    c, _, _ = client
    payload = c.get("/api/health").json()
    assert payload["ok"] is True
    assert "audit" in payload


# --------------------------------------------------------------------------- #
# overview
# --------------------------------------------------------------------------- #


def test_overview_returns_every_device(client):
    c, _, _ = client
    payload = c.get("/api/overview").json()
    assert payload["ok"] is True
    assert payload["device_count"] == len(DEMO_DEVICES)


def test_overview_is_json_safe(client):
    c, _, _ = client
    import json

    json.dumps(c.get("/api/overview").json())  # must not raise


def test_overview_can_propose_a_vlan(client):
    c, _, _ = client
    payload = c.get("/api/overview?new_vlan=150").json()
    assert payload["proposed_vlan"] == 150


# --------------------------------------------------------------------------- #
# the change flow
# --------------------------------------------------------------------------- #


def test_propose_returns_a_diff_and_changes_nothing(client):
    c, fake, _ = client
    payload = c.post("/api/propose", json={"device": "leaf-01", "vlan": 150}).json()
    assert payload["ok"] is True
    assert payload["changed_lines"] > 0
    assert fake.applied == []


def test_propose_rejects_a_device_not_in_the_demo(client):
    c, _, _ = client
    response = c.post("/api/propose", json={"device": "leaf-99"})
    assert response.status_code == 400


def test_approve_requires_an_approver_at_the_http_boundary(client):
    """The gate checks this too; the route is the second lock, not the only one."""
    c, fake, _ = client
    response = c.post("/api/approve", json={"device": "leaf-01", "approver": "  "})
    assert response.status_code == 400
    assert "approver is required" in response.json()["headline"]
    assert fake.applied == []


def test_approve_with_a_name_applies_and_records(client, ):
    c, fake, lab = client
    payload = c.post(
        "/api/approve", json={"device": "leaf-01", "approver": "Brian Nelson"}
    ).json()
    assert payload["ok"] is True
    assert payload["applied"] is True
    assert payload["approver"] == "Brian Nelson"
    assert len(fake.applied) == 1
    assert len(lab.audit.applied_changes()) == 1


def test_the_full_loop_through_http(client):
    """Propose, approve, break, catch, remediate, confirm — over HTTP, as the page does."""
    c, _, _ = client

    assert c.post("/api/propose", json={"device": "leaf-01"}).json()["ok"] is True
    assert c.post("/api/approve", json={"device": "leaf-01", "approver": "A"}).json()["applied"] is True
    assert c.get("/api/overview").json()["drifted_count"] == 0

    assert c.post("/api/inject", json={"device": "leaf-01"}).json()["ok"] is True
    assert c.get("/api/overview").json()["drifted_count"] > 0

    remediated = c.post("/api/remediate", json={"device": "leaf-01", "approver": "B"}).json()
    assert remediated["applied"] is True
    assert c.get("/api/overview").json()["drifted_count"] == 0


def test_remediate_requires_an_approver(client):
    c, fake, _ = client
    response = c.post("/api/remediate", json={"device": "leaf-01", "approver": ""})
    assert response.status_code == 400


def test_inject_rejects_a_device_not_in_the_demo(client):
    c, _, _ = client
    assert c.post("/api/inject", json={"device": "spine-99"}).status_code == 400


# --------------------------------------------------------------------------- #
# lifecycle and audit
# --------------------------------------------------------------------------- #


def test_lifecycle_lists_every_device_with_an_os(client):
    c, _, _ = client
    payload = c.get("/api/lifecycle").json()
    assert payload["total"] == len(DEMO_DEVICES)
    assert all(row["os_version"] for row in payload["devices"])


def test_two_vendors_reports_both_sides_with_their_limits(client):
    """The claim is one intent across two vendors; the limits are stated, not hidden."""
    c, _, _ = client
    payload = c.get("/api/two-vendors").json()
    assert payload["ok"] is True
    vendors = {v["vendor"]: v for v in payload["vendors"]}
    assert "Arista EOS" in vendors
    assert "FRRouting" in vendors
    # The FRR side states it is read-and-diff only, rather than claiming a full apply path.
    assert "read and diff" in vendors["FRRouting"]["capability"]
    assert "apply" in vendors["Arista EOS"]["capability"]
    assert "audit record names" in payload["note"] or "frr-reload" in payload["note"]


def test_two_vendors_survives_the_frr_track_being_absent(client):
    """If the dual-DC track was never rendered, the page must still load."""
    c, _, _ = client
    payload = c.get("/api/two-vendors").json()
    assert payload["ok"] is True, "the vendor view must not fail when FRR is unavailable"
    assert len(payload["vendors"]) >= 1


def test_audit_is_empty_before_any_change(client):
    c, _, _ = client
    assert c.get("/api/audit").json()["count"] == 0


def test_audit_records_an_applied_change(client):
    c, _, _ = client
    c.post("/api/approve", json={"device": "leaf-01", "approver": "Brittany"})
    payload = c.get("/api/audit").json()
    assert payload["applied"] == 1
    assert payload["entries"][0]["approver"] == "Brittany"


# --------------------------------------------------------------------------- #
# resilience — a demo must not show a stack trace
# --------------------------------------------------------------------------- #


def test_a_broken_lab_returns_a_page_shaped_error_not_a_500(tmp_path: Path):
    from fastapi.testclient import TestClient

    def boom(**_: object):
        raise RuntimeError("NetBox answered 502")

    broken = Lab(
        read_intent=boom,
        read_actual=lambda d: Actual(device=d, platform="eos", reachable=True),
        preview=lambda p: Preview(device=p.device, diff=""),
        apply=lambda p: Preview(device=p.device, diff=""),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    client = TestClient(create_app(lab_factory=lambda: broken))
    response = client.get("/api/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert "502" in payload["detail"]


def test_an_unexpected_error_in_a_route_is_shaped_not_raised(tmp_path: Path):
    from fastapi.testclient import TestClient

    def explode(*args, **kwargs):
        raise ValueError("something nobody predicted")

    fake = FakeLab()
    lab = Lab(
        read_intent=fake.read_intent,
        read_actual=fake.read_actual,
        preview=fake.preview,
        apply=fake.apply,
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )
    client = TestClient(create_app(lab_factory=lambda: lab))

    # Patch the scenario function the route calls, to simulate an unforeseen failure.
    import workbench.web as web

    original = web.api_lifecycle
    web.api_lifecycle = explode  # type: ignore[assignment]
    try:
        payload = client.get("/api/lifecycle").json()
        assert payload["ok"] is False
        assert "ValueError" in payload["detail"]
    finally:
        web.api_lifecycle = original  # type: ignore[assignment]


def test_a_malformed_json_body_does_not_crash_a_route(client):
    c, _, _ = client
    response = c.post(
        "/api/propose", content=b"not json", headers={"Content-Type": "application/json"}
    )
    # A missing device is a 400, never a 500.
    assert response.status_code == 400
