"""Unit tests for Diode-gRPC Assurance integration (INIT-035/SPEC-002)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from netbox_demo import assurance


def _dev(dev_id: str, name: str, state: int, *, source: str = "netbox-demo-k8s-orb/device-discovery"):
    return SimpleNamespace(
        id=dev_id,
        name=name,
        object_type="dcim.device",
        deviation_type="dt-1",
        source=source,
        state=state,
    )


class _FakeDiodeApi:
    STATES = {2: "OPEN", 3: "APPLIED", 4: "IGNORED", 5: "FAILED"}

    def __init__(self, pages: list[list[SimpleNamespace]], *, raise_exc: Exception | None = None):
        self.pages = pages
        self.raise_exc = raise_exc
        self.applied: list[tuple[list[str], str | None]] = []
        self.rediffed: list[tuple[list[str], str | None]] = []
        self.ignored: list[tuple[list[str], str | None]] = []

    def get_state_string(self, state: int) -> str:
        return self.STATES[state]

    def get_deviation_type_name_from_id(self, type_id: str) -> str:
        return "Missing Object" if type_id == "dt-1" else type_id

    def get_deviation_list(self, page_size=100, page_token=None, filters=None, ordering=None):
        if self.raise_exc:
            raise self.raise_exc
        if filters and "id" in filters:
            wanted = set(filters["id"])
            flat = [d for page in self.pages for d in page if d.id in wanted]
            return SimpleNamespace(deviations=flat, next_page_token="")
        idx = int(page_token) if page_token else 0
        batch = self.pages[idx] if idx < len(self.pages) else []
        nxt = str(idx + 1) if idx + 1 < len(self.pages) else ""
        return SimpleNamespace(deviations=batch, next_page_token=nxt)

    def deviation_apply(self, dev_id: str, branch_id=None):
        self.applied.append(([dev_id], branch_id))

    def bulk_deviation_apply(self, ids: list[str], branch_id=None):
        self.applied.append((list(ids), branch_id))

    def deviation_rediff(self, dev_id: str, branch_id=None):
        self.rediffed.append(([dev_id], branch_id))

    def bulk_deviation_rediff(self, ids: list[str], branch_id=None):
        self.rediffed.append((list(ids), branch_id))

    def deviation_ignore(self, dev_id: str):
        self.ignored.append(([dev_id], None))

    def bulk_deviation_ignore(self, ids: list[str], branch_id=None):
        self.ignored.append((list(ids), branch_id))


@pytest.fixture(autouse=True)
def _patch_conf(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(assurance.conf, "assurance_deviations_url", lambda: "/plugins/assurance/deviations/")


def test_summary_reads_diode_grpc_across_pages(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeDiodeApi(
        pages=[
            [_dev("1", "demo-spine-01", 2), _dev("2", "demo-spine-02", 2)],
            [_dev("3", "demo-leaf-01", 3), _dev("4", "demo-leaf-02", 4)],
        ]
    )
    monkeypatch.setattr(assurance, "_plugin_installed", lambda: True)
    monkeypatch.setattr(assurance, "_load_diode_api", lambda: fake)

    result = assurance.summary()
    assert result["available"] is True
    assert result["source"] == "diode_grpc"
    assert result["open"] == 2
    assert result["counts"] == {"open": 2, "applied": 1, "ignored": 1, "total": 4}


def test_summary_zero_open_when_only_applied(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeDiodeApi(pages=[[_dev("1", "demo-spine-01", 3)]])
    monkeypatch.setattr(assurance, "_plugin_installed", lambda: True)
    monkeypatch.setattr(assurance, "_load_diode_api", lambda: fake)

    result = assurance.summary()
    assert result["available"] is True
    assert result["open"] == 0
    ok, msg = assurance.zero_drift_claim()
    assert ok is True
    assert "no open deviations" in msg.lower()


def test_summary_never_fakes_zero_on_diode_grpc_error(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeDiodeApi(pages=[], raise_exc=RuntimeError("gRPC deadline exceeded"))
    monkeypatch.setattr(assurance, "_plugin_installed", lambda: True)
    monkeypatch.setattr(assurance, "_load_diode_api", lambda: fake)

    result = assurance.summary()
    assert result["available"] is False
    assert result["source"] == "diode_grpc"
    assert "deadline exceeded" in result["reason"]
    assert assurance.open_deviation_count() is None
    ok, _ = assurance.zero_drift_claim()
    assert ok is None


def test_orm_fallback_rejects_stub_model_without_status_field(monkeypatch: pytest.MonkeyPatch):
    stub_model = SimpleNamespace(
        _meta=SimpleNamespace(get_fields=lambda: [SimpleNamespace(name="id")]),
        objects=SimpleNamespace(count=lambda: 0),
    )
    monkeypatch.setattr(assurance, "_plugin_installed", lambda: True)
    monkeypatch.setattr(assurance, "_load_diode_api", lambda: None)
    monkeypatch.setattr(assurance, "_deviation_models", lambda: iter([stub_model]))

    result = assurance.summary()
    assert result["available"] is False
    assert result["source"] == "orm"


def test_list_and_apply_enforce_demo_scope(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeDiodeApi(
        pages=[
            [
                _dev("d-1", "demo-leaf-01", 2, source="netbox-demo-k8s-orb/device-discovery"),
                _dev("d-2", "demo-leaf-02", 2, source="netbox-demo-k8s-orb/device-discovery"),
                _dev("prod-99", "core-rtr-01", 2, source="prod-orb/device-discovery"),
            ]
        ]
    )
    monkeypatch.setattr(assurance, "_plugin_installed", lambda: True)
    monkeypatch.setattr(assurance, "_load_diode_api", lambda: fake)

    listed = assurance.list_deviations(states=["open"], demo_only=True)
    assert listed["available"] is True
    assert [d["id"] for d in listed["deviations"]] == ["d-1", "d-2"]

    # Refuses non-demo deviation ID
    refused = assurance.apply_deviations(["d-1", "prod-99"])
    assert refused["ok"] is False
    assert "prod-99" in refused["rejected"]
    assert fake.applied == []

    # Applies all open demo-scoped deviations when ids=None
    applied = assurance.apply_deviations(None)
    assert applied["ok"] is True
    assert applied["count"] == 2
    assert fake.applied == [(["d-1", "d-2"], None)]

    # Single-ID rediff uses single-item helper
    rediffed = assurance.rediff_deviations(["d-1"])
    assert rediffed["ok"] is True
    assert fake.rediffed == [(["d-1"], None)]
