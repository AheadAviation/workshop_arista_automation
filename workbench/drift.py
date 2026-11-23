"""Compare what the network should be against what it is (INIT-001/SPEC-002).

One pure function and a few small comparators. No device, no NetBox, no HTTP — which
is what lets the entire demo's central claim be tested on a laptop with nothing
running.

The rule that matters most here: **a comparison that cannot see one side must never
report "healthy".** An unreachable switch is a finding, not an empty list. A page that
shows green because it failed to look is worse than no page at all, and in front of a
customer it is the kind of green that ends a meeting badly.
"""

from __future__ import annotations

from workbench.contracts import Actual, Drift, Intent

__all__ = ["compute_drift", "find_trunk", "normalise_port", "summarise"]


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #


def compute_drift(intent: Intent, actual: Actual) -> list[Drift]:
    """Every real difference between intended and actual state on one device.

    Returns an empty list when the device genuinely matches intent. Returns a
    populated list — including for "I could not look" — in every other case.

    Pure: mutates neither argument, and calling it twice on the same inputs gives
    the same result.
    """
    if intent.device != actual.device:
        # A programming error rather than a network one, but reported as a finding so
        # the page cannot silently compare leaf-01's intent against leaf-02's state.
        return [
            Drift(
                device=intent.device,
                kind="identity",
                detail=(
                    f"internal error: intended state is for {intent.device} but actual "
                    f"state is for {actual.device}"
                ),
                actual=actual.device,
                intended=intent.device,
                severity="error",
            )
        ]

    if not actual.reachable:
        return [
            Drift(
                device=intent.device,
                kind="reachability",
                detail=_unreachable_detail(actual),
                actual=actual.error or "unreachable",
                intended="reachable",
                severity="error",
            )
        ]

    findings: list[Drift] = []
    findings.extend(_compare_hostname(intent, actual))
    findings.extend(_compare_vlans(intent, actual))
    findings.extend(_compare_trunks(intent, actual))
    return findings


def _unreachable_detail(actual: Actual) -> str:
    """Phrased for an executive, carrying the engineer's reason in brackets."""
    reason = (actual.error or "no response").strip()
    return f"{actual.device} did not answer, so it could not be checked ({reason})"


# --------------------------------------------------------------------------- #
# Comparators
# --------------------------------------------------------------------------- #


def _compare_hostname(intent: Intent, actual: Actual) -> list[Drift]:
    if not intent.hostname or not actual.hostname:
        return []  # nothing to compare against; not a finding on its own
    if intent.hostname != actual.hostname:
        return [
            Drift(
                device=intent.device,
                kind="hostname",
                detail=(
                    f"device is named {actual.hostname!r} but should be "
                    f"{intent.hostname!r}"
                ),
                actual=actual.hostname,
                intended=intent.hostname,
                severity="error",
            )
        ]
    return []


def _compare_vlans(intent: Intent, actual: Actual) -> list[Drift]:
    """VLANs that should exist on the device but do not.

    Deliberately one-directional for the demo: an *extra* VLAN on a switch is normal
    (a local VLAN, a reserved range), and reporting every one of them would bury the
    finding that matters. Missing intent is the signal; extra reality is context.
    """
    if not intent.vlans:
        return []
    missing = sorted(set(intent.vlans) - set(actual.vlans))
    if not missing:
        return []
    return [
        Drift(
            device=intent.device,
            kind="vlans",
            detail=(
                f"{_vlan_word(missing)} missing from the switch: "
                f"{', '.join(str(v) for v in missing)}"
            ),
            actual=_ranges(sorted(actual.vlans)),
            intended=_ranges(sorted(intent.vlans)),
            severity="error",
        )
    ]


def normalise_port(port: str) -> str:
    """A port name in its short EOS form, so intent and reality can be compared.

    EOS abbreviates unpredictably: the interface is `Ethernet5` in configuration but `Et5` in
    `show interfaces trunk`. Comparing the long form against the short form made a correctly
    configured trunk look empty -- which is exactly how the demo silently "failed" on a switch
    that was in fact perfect.
    """
    name = port.strip()
    for long, short in (("Ethernet", "Et"), ("Port-Channel", "Po"), ("Management", "Ma")):
        if name.startswith(long):
            return short + name[len(long):]
    return name


def find_trunk(
    trunk_allowed: dict[str, tuple[int, ...]], port: str
) -> tuple[bool, tuple[int, ...]]:
    """Look up a port's allowed VLANs, tolerating either name form.

    Returns `(found, vlans)`. `found` distinguishes "the switch does not consider this a trunk"
    from "it is a trunk carrying nothing", which are different findings.
    """
    target = normalise_port(port)
    for key, vlans in trunk_allowed.items():
        if normalise_port(key) == target:
            return True, vlans
    return False, ()


def _compare_trunks(intent: Intent, actual: Actual) -> list[Drift]:
    """Per-port trunk allow-list comparison.

    One `Drift` per port so the page can light up the exact port the customer cares
    about, rather than one lump finding the presenter has to explain.
    """
    findings: list[Drift] = []
    for port in intent.trunks:
        wanted = set(intent.trunk_allowed.get(port, intent.vlans))
        found, have_tuple = find_trunk(actual.trunk_allowed, port)
        have = set(have_tuple)

        if not found:
            findings.append(
                Drift(
                    device=intent.device,
                    kind="trunks",
                    detail=(
                        f"{port} is not configured as a trunk, but it should carry "
                        f"{_vlan_word(sorted(wanted))} {_ranges(sorted(wanted))}"
                    ),
                    actual="not a trunk",
                    intended=_ranges(sorted(wanted)),
                    severity="error",
                )
            )
            continue

        missing = sorted(wanted - have)
        if missing:
            findings.append(
                Drift(
                    device=intent.device,
                    kind="trunks",
                    detail=(
                        f"{port} ({intent.device}) is missing "
                        f"{_vlan_word(missing)} {_ranges(missing)}"
                    ),
                    actual=_ranges(sorted(have)),
                    intended=_ranges(sorted(wanted)),
                    severity="error",
                )
            )
    return findings


# --------------------------------------------------------------------------- #
# Small presentation helpers — used here and by the page, defined once
# --------------------------------------------------------------------------- #


def _vlan_word(vlans: list[int]) -> str:
    return "VLAN" if len(vlans) == 1 else "VLANs"


def _ranges(vlans: list[int]) -> str:
    """[100,110,120,130,150] -> '100,110,120,130,150'; runs collapse to '100-130'.

    EOS itself renders allow-lists as ranges, so collapsing runs makes the page's
    "intended" column read the same way `show interfaces trunk` does on the switch.
    """
    if not vlans:
        return "none"
    ordered = sorted(set(vlans))
    parts: list[str] = []
    start = prev = ordered[0]
    for v in ordered[1:]:
        if v == prev + 1:
            prev = v
            continue
        parts.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = v
    parts.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(parts)


def summarise(findings: list[Drift]) -> dict[str, object]:
    """The one-line verdict the page puts at the top of a device card."""
    if not findings:
        return {"state": "compliant", "headline": "matches intended state", "count": 0}
    errors = [f for f in findings if f.severity == "error"]
    state = "drifted" if errors else "notice"
    if len(findings) == 1:
        headline = findings[0].detail
    else:
        headline = f"{len(findings)} differences from intended state"
    return {"state": state, "headline": headline, "count": len(findings)}


__all__ = ["compute_drift", "find_trunk", "normalise_port", "summarise"]
