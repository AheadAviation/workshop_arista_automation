"""Change control: the gate that makes this a process rather than a button (INIT-002/SPEC-004).

The single most important behaviour in the plugin is that **a change cannot reach a switch
without a change record**. It is also the thing that was quietly wrong: `ticket_id` was optional,
so `approve_and_push(..., ticket_id=None)` returned `applied: True, verified: True` and pushed
VLAN 347 to a real switch. That is not change control with a missing safeguard; it is a remote
control with an audit line after the fact.

These tests run against fakes — no Docker, no Zammad — so they can pin the refusal precisely.
`scripts/verify_change_control.py` proves the same properties against the live stack.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

TEMPLATES = REPO_ROOT / "plugins" / "netbox_ops" / "netbox_ops" / "templates" / "netbox_ops"
PKG = REPO_ROOT / "plugins" / "netbox_ops" / "netbox_ops"


# --------------------------------------------------------------------------- #
# loading the plugin's modules without NetBox
# --------------------------------------------------------------------------- #
# `netbox_ops/__init__.py` imports `netbox.plugins.PluginConfig`, so the package cannot be
# imported on a machine without NetBox. These tests only need `change_flow` and its
# collaborators, so the package is stood up as a namespace with the real module files executed
# into it. That exercises the actual source rather than a copy of it.


def _load_plugin_modules(monkeypatch):
    import importlib.util
    import types

    # `conf.py` does `from netbox.plugins import get_plugin_config`, which does not exist outside
    # a NetBox install. A minimal stub is enough: these tests replace every collaborator they
    # care about, and `get_plugin_config` only needs to answer with a default.
    if "netbox" not in sys.modules:
        netbox = types.ModuleType("netbox")
        plugins = types.ModuleType("netbox.plugins")
        plugins.get_plugin_config = lambda *a, **k: None
        netbox.plugins = plugins
        monkeypatch.setitem(sys.modules, "netbox", netbox)
        monkeypatch.setitem(sys.modules, "netbox.plugins", plugins)

    pkg = types.ModuleType("netbox_ops")
    pkg.__path__ = [str(PKG)]
    monkeypatch.setitem(sys.modules, "netbox_ops", pkg)

    loaded: dict[str, object] = {}
    # `conf` first: the others import it at module level.
    for name in ("conf", "itsm", "engine", "live_config", "change_flow"):
        spec = importlib.util.spec_from_file_location(
            f"netbox_ops.{name}", PKG / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, f"netbox_ops.{name}", module)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # pragma: no cover - only on a broken environment
            pytest.skip(f"cannot load netbox_ops.{name}: {exc}")
        setattr(pkg, name, module)
        loaded[name] = module
    return loaded


def _change_flow_with(monkeypatch, *, ticket_ok: bool = True, applies: bool = True):
    """The real `change_flow` module, with its collaborators swapped for recorders."""
    modules = _load_plugin_modules(monkeypatch)
    change_flow = modules["change_flow"]
    conf = modules["conf"]
    engine = modules["engine"]
    itsm = modules["itsm"]
    live_config = modules["live_config"]

    calls: dict[str, list] = {"approve_vlan": [], "approve_ticket": [], "close_ticket": []}

    class FakeResult:
        ok = applies
        headline = "applied" if applies else "refused"
        detail = ""
        data = {"applied": applies}

    monkeypatch.setattr(engine, "propose_vlan", lambda *a, **k: FakeResult())
    monkeypatch.setattr(
        engine, "approve_vlan", lambda *a, **k: calls["approve_vlan"].append(a) or FakeResult()
    )
    monkeypatch.setattr(
        engine,
        "fabric_overview",
        lambda **k: type("R", (), {"ok": True, "data": {"devices": [{"device": "leaf-01", "verdict": {"state": "compliant"}}]}})(),
    )
    monkeypatch.setattr(
        itsm,
        "approve_ticket",
        lambda *a, **k: calls["approve_ticket"].append(a)
        or ({"ok": True, "state_changed": True, "headline": "approved"} if ticket_ok else {"ok": False, "headline": "no"}),
    )
    monkeypatch.setattr(
        itsm,
        "close_ticket",
        lambda *a, **k: calls["close_ticket"].append(a) or {"ok": True, "headline": "closed"},
    )
    monkeypatch.setattr(
        itsm, "create_change_ticket", lambda *a, **k: {"ok": True, "ticket_id": 7, "number": "76007"}
    )
    monkeypatch.setattr(live_config, "snapshot", lambda d: {"ok": True, "lines": 10})
    monkeypatch.setattr(live_config, "diff_against_snapshot", lambda d: {"diff": "", "changed_lines": 0})
    monkeypatch.setattr(live_config, "clear_snapshot", lambda d: None)
    monkeypatch.setattr(conf, "demo_vlan", lambda: 150)
    monkeypatch.setattr(conf, "demo_vlan_name", lambda: "PHILIPS")

    return change_flow, calls


def test_a_change_without_a_ticket_is_refused(monkeypatch):
    """The exact bug: this used to push to the switch and report success."""
    change_flow, calls = _change_flow_with(monkeypatch)

    result = change_flow.approve_and_push("leaf-01", "Brian Nelson", ticket_id=None, vlan=210)

    assert result["ok"] is False
    assert result["refused"] is True
    assert "ticket is required" in result["headline"].lower()
    assert calls["approve_vlan"] == [], "the switch must not be touched without a ticket"


def test_a_change_without_a_ticket_does_not_approve_anything(monkeypatch):
    """No ticket means nothing happened at all, not "approved in NetBox but not the ITSM"."""
    change_flow, calls = _change_flow_with(monkeypatch)

    change_flow.approve_and_push("leaf-01", "Brian Nelson", ticket_id=None)

    assert calls["approve_ticket"] == []
    assert calls["close_ticket"] == []


def test_a_nameless_approver_is_refused_before_the_ticket_is_touched(monkeypatch):
    change_flow, calls = _change_flow_with(monkeypatch)

    result = change_flow.approve_and_push("leaf-01", "   ", ticket_id=7, vlan=210)

    assert result["ok"] is False
    assert "approver" in result["headline"].lower()
    assert calls["approve_vlan"] == []


def test_a_ticket_that_cannot_be_approved_stops_the_change(monkeypatch):
    """Approving in NetBox while the ticket says otherwise leaves the two disagreeing, which is
    worse than not changing anything."""
    change_flow, calls = _change_flow_with(monkeypatch, ticket_ok=False)

    result = change_flow.approve_and_push("leaf-01", "Brian Nelson", ticket_id=7, vlan=210)

    assert result["ok"] is False
    assert result["refused"] is True
    assert calls["approve_vlan"] == [], "no push when the ticket refused the approval"


# --------------------------------------------------------------------------- #
# the happy path still works, and only closes on evidence
# --------------------------------------------------------------------------- #


def test_a_ticketed_change_applies_and_closes(monkeypatch):
    change_flow, calls = _change_flow_with(monkeypatch)

    result = change_flow.approve_and_push("leaf-01", "Brian Nelson", ticket_id=7, vlan=210)

    assert result["ok"] is True
    assert result["applied"] is True
    assert result["verified"] is True
    assert result["ticket_state_changed"] is True
    assert result["ticket_closed"] is True
    assert result["ticket_id"] == 7
    assert len(calls["approve_vlan"]) == 1


def test_the_ticket_is_closed_only_after_the_device_is_verified(monkeypatch):
    """Closing on approval would assert success before any evidence exists."""
    change_flow, calls = _change_flow_with(monkeypatch)
    from netbox_ops import engine

    # The change applies, but the read-back disagrees with intent.
    monkeypatch.setattr(
        engine,
        "fabric_overview",
        lambda **k: type("R", (), {"ok": True, "data": {"devices": [{"device": "leaf-01", "verdict": {"state": "drifted"}}]}})(),
    )

    result = change_flow.approve_and_push("leaf-01", "Brian Nelson", ticket_id=7, vlan=210)

    assert result["verified"] is False
    assert result["ticket_closed"] is False, "an unverified change must leave the ticket open"
    assert calls["close_ticket"] == []
    assert "left open" in result["ticket_note"]


def test_the_approval_is_recorded_in_the_ticket_not_only_in_our_return_value(monkeypatch):
    change_flow, calls = _change_flow_with(monkeypatch)

    change_flow.approve_and_push("leaf-01", "Brian Nelson", ticket_id=42, vlan=210)

    assert calls["approve_ticket"], "the approval must be written to the ITSM"
    args = calls["approve_ticket"][0]
    assert args[0] == 42
    assert "Brian Nelson" in args[1]


# --------------------------------------------------------------------------- #
# the UI must not offer the unsafe option
# --------------------------------------------------------------------------- #


def test_the_form_does_not_offer_a_no_ticket_option():
    """`<option value="">— none —</option>` was how a presenter would accidentally bypass the
    gate without noticing."""
    page = (TEMPLATES / "_change.html").read_text(encoding="utf-8")
    assert "— none —" not in page
    assert 'name="ticket_id" required' in page


def test_the_form_says_the_ticket_is_required():
    page = (TEMPLATES / "_change.html").read_text(encoding="utf-8")
    assert "change record behind it" in page


def test_the_view_refuses_a_missing_ticket_before_the_engine():
    """Two locks: the boundary refuses, and the engine refuses. Either alone would do; both
    means a future refactor of one cannot silently open the gate."""
    views = (REPO_ROOT / "plugins" / "netbox_ops" / "netbox_ops" / "views.py").read_text(encoding="utf-8")
    block = views.split("class ApproveAndPushView", 1)[1].split("@method_decorator", 1)[0]
    assert "if not ticket_id:" in block
    assert "A change ticket is required" in block


# --------------------------------------------------------------------------- #
# ticket state must be legible, not an id
# --------------------------------------------------------------------------- #


def test_ticket_states_are_resolved_to_names():
    """The page showed `state 4`. On a page whose point is a legible process, that is a defect."""
    itsm = (REPO_ROOT / "plugins" / "netbox_ops" / "netbox_ops" / "itsm.py").read_text(encoding="utf-8")
    assert "def _state_names" in itsm
    assert "ticket_states" in itsm
    # And it must survive the lookup failing.
    assert "except ItsmError:" in itsm
