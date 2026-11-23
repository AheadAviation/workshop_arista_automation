"""Drift comparison tests (INIT-001/SPEC-002).

Pure logic, so these run in milliseconds and need nothing running.
"""

from __future__ import annotations

import pytest

from workbench.contracts import Actual, Drift, Intent
from workbench.drift import compute_drift, summarise


def intent(**kwargs) -> Intent:
    base = dict(
        device="leaf-01",
        platform="eos",
        source="netbox:dc1-fabric",
        hostname="leaf-01",
        role="leaf",
        asn=65001,
        vlans=(100, 110),
        trunks=("Ethernet5",),
        trunk_allowed={"Ethernet5": (100, 110)},
    )
    base.update(kwargs)
    return Intent(**base)


def actual(**kwargs) -> Actual:
    base = dict(
        device="leaf-01",
        platform="eos",
        reachable=True,
        hostname="leaf-01",
        asn=65001,
        vlans=(100, 110),
        trunk_allowed={"Ethernet5": (100, 110)},
    )
    base.update(kwargs)
    return Actual(**base)


# --------------------------------------------------------------------------- #
# ac-3 — an empty list means genuinely matching
# --------------------------------------------------------------------------- #


def test_matching_device_has_no_drift():
    assert compute_drift(intent(), actual()) == []


def test_extra_vlans_on_the_device_are_not_a_finding():
    """A local or reserved VLAN is normal; only missing intent is a finding."""
    assert compute_drift(intent(), actual(vlans=(100, 110, 999))) == []


def test_extra_allowed_vlans_on_a_trunk_are_not_a_finding():
    assert compute_drift(intent(), actual(trunk_allowed={"Ethernet5": (100, 110, 999)})) == []


def test_missing_vlan_is_reported():
    findings = compute_drift(intent(), actual(vlans=(100,)))
    assert len(findings) == 1
    assert findings[0].kind == "vlans"
    assert "110" in findings[0].detail


def test_missing_trunk_vlan_is_reported_per_port():
    findings = compute_drift(
        intent(vlans=(100, 110, 150), trunk_allowed={"Ethernet5": (100, 110, 150)}),
        actual(vlans=(100, 110, 150), trunk_allowed={"Ethernet5": (100, 110)}),
    )
    assert len(findings) == 1
    assert findings[0].kind == "trunks"
    assert "Ethernet5" in findings[0].detail
    assert "150" in findings[0].detail


def test_two_affected_ports_give_two_findings():
    findings = compute_drift(
        intent(
            vlans=(100, 150),
            trunks=("Ethernet5", "Ethernet6"),
            trunk_allowed={"Ethernet5": (100, 150), "Ethernet6": (100, 150)},
        ),
        actual(
            vlans=(100, 150),
            trunk_allowed={"Ethernet5": (100,), "Ethernet6": (100,)},
        ),
    )
    assert len(findings) == 2
    ports = {f.detail.split()[0] for f in findings}
    assert ports == {"Ethernet5", "Ethernet6"}


def test_port_that_is_not_a_trunk_at_all_is_reported():
    findings = compute_drift(intent(), actual(trunk_allowed={}))
    assert len(findings) == 1
    assert "not configured as a trunk" in findings[0].detail


def test_hostname_mismatch_is_reported():
    findings = compute_drift(intent(), actual(hostname="leaf-99"))
    assert len(findings) == 1
    assert findings[0].kind == "hostname"


def test_missing_hostname_on_either_side_is_not_a_finding():
    assert compute_drift(intent(hostname=""), actual()) == []
    assert compute_drift(intent(), actual(hostname="")) == []


# --------------------------------------------------------------------------- #
# ac-4 — "I could not look" must never read as healthy
# --------------------------------------------------------------------------- #


def test_unreachable_device_produces_a_finding_not_an_empty_list():
    findings = compute_drift(intent(), actual(reachable=False, error="timed out"))
    assert len(findings) == 1
    assert findings[0].kind == "reachability"
    assert findings[0].severity == "error"


def test_unreachable_detail_is_plain_english_and_carries_the_reason():
    findings = compute_drift(intent(), actual(reachable=False, error="connection refused"))
    detail = findings[0].detail
    assert "did not answer" in detail
    assert "connection refused" in detail


def test_unreachable_detail_survives_a_missing_error_string():
    findings = compute_drift(intent(), actual(reachable=False, error=""))
    assert "no response" in findings[0].detail


def test_device_identity_mismatch_is_reported_rather_than_compared():
    findings = compute_drift(intent(), actual(device="leaf-02"))
    assert len(findings) == 1
    assert findings[0].kind == "identity"
    assert "internal error" in findings[0].detail


def test_unreachable_beats_every_other_comparison():
    """If we could not read it, we must not report on its VLANs as though we had."""
    findings = compute_drift(intent(), actual(reachable=False, vlans=()))
    assert len(findings) == 1
    assert findings[0].kind == "reachability"


# --------------------------------------------------------------------------- #
# ac-6 — purity
# --------------------------------------------------------------------------- #


def test_comparison_is_repeatable():
    i, a = intent(), actual(vlans=(100,))
    assert compute_drift(i, a) == compute_drift(i, a)


def test_comparison_does_not_mutate_its_inputs():
    i = intent()
    a = actual(vlans=(100,))
    compute_drift(i, a)
    assert i.vlans == (100, 110)
    assert a.vlans == (100,)


def test_comparison_does_not_depend_on_input_ordering():
    a = intent(vlans=(110, 100))
    b = intent(vlans=(100, 110))
    assert compute_drift(a, actual()) == compute_drift(b, actual())


def test_wildcard_trunk_falls_back_to_the_device_vlan_set():
    """A trunk with no explicit allow-list must carry every intended VLAN."""
    i = intent(vlans=(100, 110, 150), trunk_allowed={})
    findings = compute_drift(i, actual(vlans=(100, 110, 150), trunk_allowed={"Ethernet5": (100,)}))
    assert len(findings) == 1
    assert "110" in findings[0].detail and "150" in findings[0].detail


# --------------------------------------------------------------------------- #
# summarise
# --------------------------------------------------------------------------- #


def test_summarise_clean_device():
    assert summarise([])["state"] == "compliant"


def test_summarise_single_finding_uses_its_detail_as_headline():
    finding = Drift(device="leaf-01", kind="vlans", detail="VLAN 150 is missing", severity="error")
    result = summarise([finding])
    assert result["state"] == "drifted"
    assert result["headline"] == "VLAN 150 is missing"


def test_summarise_single_warning_is_a_notice_not_drift():
    """A warning-severity finding is context, not a fault: it must not read as drifted."""
    finding = Drift(device="leaf-01", kind="vlans", detail="VLAN 150 is missing")
    result = summarise([finding])
    assert result["state"] == "notice"
    assert result["headline"] == "VLAN 150 is missing"


def test_summarise_multiple_findings_counts_them():
    findings = [
        Drift(device="d", kind="vlans", detail="a", severity="error"),
        Drift(device="d", kind="trunks", detail="b", severity="error"),
    ]
    result = summarise(findings)
    assert result["state"] == "drifted"
    assert result["count"] == 2
    assert "2 differences" in str(result["headline"])


def test_summarise_info_only_findings_are_a_notice_not_drift():
    result = summarise([Drift(device="d", kind="os", detail="x", severity="info")])
    assert result["state"] == "notice"


# --------------------------------------------------------------------------- #
# the range renderer, since the page depends on it reading like the switch
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "vlans,expected",
    [
        ([], "none"),
        ([100], "100"),
        ([100, 101, 102], "100-102"),
        ([100, 102], "100,102"),
        ([100, 101, 200, 201], "100-101,200-201"),
        ([110, 100, 101], "100-101,110"),
    ],
)
def test_range_rendering(vlans, expected):
    from workbench.drift import _ranges

    assert _ranges(vlans) == expected
