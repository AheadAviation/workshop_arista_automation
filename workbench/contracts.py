"""The four cross-cutting shapes the demo is built on (INIT-001/SPEC-001).

Every other module in `workbench` reads or writes these. They are defined once, here,
so the page and each device driver cannot drift apart on what a "plan" or a "drift"
means.

Deliberately pure: no NetBox, no HTTP, no device. Importing this module cannot touch
the network, which makes it the one piece of the demo that is fully testable on a
laptop with nothing running.

Provenance: adapted from the NAF AutoCon 6 workshop's `Plan` / `Skip` / autonomy
model (Junos), retargeted to Arista EOS and reduced to what a 90-minute executive
briefing shows.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

# --------------------------------------------------------------------------- #
# Closed sets. Anything not in these raises, so a typo in a scenario or a page
# string fails loudly at construction rather than silently rendering a blank tile
# in front of a customer.
# --------------------------------------------------------------------------- #

#: How far the automation may go without a human. `observe` is the demo default so
#: nothing can change a switch before someone deliberately turns the dial up.
AUTONOMY_LEVELS: tuple[str, ...] = ("observe", "assist", "autonomous")

#: What happened for one alert or one requested change. Closed on purpose: the page
#: colours each of these and the audit record indexes on them.
EVENT_KINDS: tuple[str, ...] = (
    "detected",       # drift or an event was seen and understood
    "planned",        # a plan was produced
    "no-drift",       # a plan was produced and the device already matched it
    "within-policy",  # deliberately not acting (a policy said so)
    "no-plan",        # nothing to do for this event
    "pending",        # dry-run held, waiting for a human
    "approved",       # a human said yes
    "rejected",       # a human said no
    "applied",        # the change was committed on the device
    "verified",       # the device now matches intent
    "failed",         # something went wrong; always carries a reason
    "reset",          # the demo was returned to its starting state
)

#: Which device family a plan is for. Determines which executor runs it.
PLATFORMS: tuple[str, ...] = ("eos", "frr")

#: The two diff formats the demo produces. EOS uses `show session-config diffs`;
#: FRR uses a config-file render and diff.
DIFF_FORMATS: tuple[str, ...] = ("eos-session", "unified")


class ContractError(ValueError):
    """Raised when a shape is constructed with something it does not allow."""


def _require_known(value: str, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise ContractError(f"{what} must be one of {', '.join(allowed)}; got {value!r}")
    return value


def _require_non_empty(value: str, what: str) -> str:
    if not value or not str(value).strip():
        raise ContractError(f"{what} is required and cannot be empty")
    return value


def new_id(*parts: str) -> str:
    """A stable, readable id for an event or a change.

    Readable rather than random because it is shown on screen and read out loud in
    a briefing: `vlans-add:leaf-01:a3f1c2` beats `c1f2e3d4`.
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:6]
    return f"{':'.join(p for p in parts if p)}:{digest}"


def utc_now() -> str:
    """ISO-8601 UTC, the only timestamp format this demo writes."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# What the network should be
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Intent:
    """The intended state for one device, as the demo understands it.

    Deliberately narrow: a handful of things that are worth showing on a screen.
    This is not a general-purpose config model, and it is not trying to be — the
    full device config still comes from the NetBox render path.

    Every field is already flattened out of NetBox (or the AVD group_vars, for the
    FRR track) by the loader, so consumers never have to know where it came from.
    """

    device: str
    platform: str                     # one of PLATFORMS
    source: str                       # e.g. "netbox:dc1-fabric" — shown on the page
    hostname: str = ""
    role: str = ""                    # spine | leaf | border | core
    asn: int = 0
    router_id: str = ""
    #: VLAN ids that must be present on the device.
    vlans: tuple[int, ...] = ()
    #: Trunk ports that must carry every VLAN in `vlans`, in EOS port form.
    trunks: tuple[str, ...] = ()
    #: The exact trunk VLAN allow-list each trunk must have, by port.
    trunk_allowed: dict[str, tuple[int, ...]] = field(default_factory=dict)
    #: What OS the fleet wants this device on, for the lifecycle view (SPEC-007).
    target_os: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        _require_non_empty(self.device, "device")
        _require_known(self.platform, PLATFORMS, "platform")
        _require_non_empty(self.source, "source")
        if self.asn and not (0 < self.asn <= 4294967295):
            raise ContractError(f"asn must be a positive 32-bit integer; got {self.asn}")
        for vlan in self.vlans:
            if not (1 <= vlan <= 4094):
                raise ContractError(f"vlan {vlan} is outside 1-4094")
        for port, allowed in self.trunk_allowed.items():
            if port not in self.trunks:
                raise ContractError(
                    f"trunk_allowed names {port!r}, which is not in trunks {list(self.trunks)!r}"
                )
            for vlan in allowed:
                if not (1 <= vlan <= 4094):
                    raise ContractError(f"trunk {port} allows vlan {vlan}, outside 1-4094")

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


# --------------------------------------------------------------------------- #
# What the device actually is
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Drift:
    """One difference between intended and actual state on one device.

    `detail` is written to be read by a person on a screen, not parsed: "trunk
    Ethernet5 is missing VLAN 150".
    """

    device: str
    kind: str                         # vlans | trunks | bgp | hostname | os
    detail: str
    actual: str = ""
    intended: str = ""
    severity: Literal["info", "warn", "error"] = "warn"

    def __post_init__(self) -> None:
        _require_non_empty(self.device, "device")
        _require_non_empty(self.kind, "kind")
        _require_non_empty(self.detail, "detail")
        _require_known(self.severity, ("info", "warn", "error"), "severity")

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


@dataclass(frozen=True)
class Actual:
    """What one device currently is, as read from the device itself.

    Kept next to `Intent` on purpose so the comparison is a function of two values
    and nothing else — which is what makes it testable without a switch.
    """

    device: str
    platform: str
    reachable: bool
    hostname: str = ""
    asn: int = 0
    vlans: tuple[int, ...] = ()
    trunk_allowed: dict[str, tuple[int, ...]] = field(default_factory=dict)
    os_version: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        _require_non_empty(self.device, "device")
        _require_known(self.platform, PLATFORMS, "platform")

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


# --------------------------------------------------------------------------- #
# What to do about it
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Plan:
    """The change that would close a drift. Always previewable, never self-applying.

    `config` is the payload in the device's own idiom: `set`-style or full text for
    EOS, the rendered file for FRR. `mode` distinguishes a merge from a replace
    because the two behave very differently on a trunk allow-list.
    """

    device: str
    platform: str
    usecase: str                      # the story this belongs to, e.g. "vlan-across-trunks"
    fmt: str                          # "set" | "text"
    config: str
    mode: Literal["merge", "replace"] = "merge"
    reason: str = ""
    #: True when the change is a guard rather than a fix (the NAF workshop's
    #: prefix-limit clamp): it stays until a human takes it off.
    compensating: bool = False
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.device, "device")
        _require_known(self.platform, PLATFORMS, "platform")
        _require_non_empty(self.usecase, "usecase")
        _require_known(self.fmt, ("set", "text"), "fmt")
        _require_known(self.mode, ("merge", "replace"), "mode")
        # An empty plan is not a plan. This is the check that stops the demo
        # "applying" nothing and calling it success.
        _require_non_empty(self.config, "config")

    @property
    def summary(self) -> str:
        lines = len([ln for ln in self.config.splitlines() if ln.strip()])
        return f"{self.mode} {self.fmt} · {lines} line{'s' if lines != 1 else ''} · {self.reason or self.usecase}"

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


@dataclass(frozen=True)
class Skip:
    """A deliberate decision not to act, with the reason a person can read."""

    reason: str

    def __post_init__(self) -> None:
        _require_non_empty(self.reason, "reason")

    def to_dict(self) -> dict[str, Any]:
        return {"skip": self.reason}


@dataclass(frozen=True)
class Preview:
    """The result of asking a device what a plan would change, without changing it.

    An empty `diff` means the device already matches intent. That is the demo's
    definition of "no drift", and it is the reason a re-run of the same approval is
    harmless.
    """

    device: str
    diff: str
    #: Lines actually added or removed, for counting on screen.
    changed_lines: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.diff.strip()

    def to_dict(self) -> dict[str, Any]:
        return {"device": self.device, "diff": self.diff,
                "changed_lines": self.changed_lines, "is_empty": self.is_empty}


# --------------------------------------------------------------------------- #
# The audit record
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AuditEntry:
    """One thing that happened, with who caused it.

    This is the CISO's artefact. There is deliberately no constructor path that
    produces an applied change without an approver, because the entire point of the
    page is that a human stood behind each change.
    """

    kind: str
    device: str
    summary: str
    autonomy: str
    approver: str = ""
    event_id: str = ""
    diff_before: str = ""
    diff_after: str = ""
    detail: str = ""
    at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_known(self.kind, EVENT_KINDS, "kind")
        _require_non_empty(self.device, "device")
        _require_non_empty(self.summary, "summary")
        _require_known(self.autonomy, AUTONOMY_LEVELS, "autonomy")
        # ac-6: an applied change has a named human behind it. This is the single
        # most important invariant in the demo, so it is enforced here rather than
        # trusted to each caller.
        if self.kind in ("approved", "applied") and not self.approver.strip():
            raise ContractError(
                f"a {self.kind!r} entry requires a named approver; "
                "no change is recorded as approved or applied without one"
            )

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


# --------------------------------------------------------------------------- #
# The autonomy rule, stated once
# --------------------------------------------------------------------------- #


def may_act_unattended(level: str, action: Literal["observe", "preview", "apply"]) -> bool:
    """What each autonomy level is allowed to do without a human.

    * `observe`    — watch and report. No preview, no apply.
    * `assist`     — preview (dry-run) freely; applying needs a human. The demo default.
    * `autonomous` — preview and apply, still recording an approver of record.

    Note that even `autonomous` records an approver: the demo never produces an
    applied change with nobody's name on it (`AuditEntry`'s invariant).
    """
    _require_known(level, AUTONOMY_LEVELS, "autonomy level")
    if action == "observe":
        return True
    if action == "preview":
        return level in ("assist", "autonomous")
    if action == "apply":
        return level == "autonomous"
    raise ContractError(f"unknown action {action!r}")


# --------------------------------------------------------------------------- #
# JSON safety
# --------------------------------------------------------------------------- #


def _plain(value: Any) -> Any:
    """Tuples become lists so `json.dumps` works without a custom encoder.

    Small on purpose: the page receives exactly what `to_dict` returns, so there is
    one place tuples could surprise it and this is that place.
    """
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


__all__ = [
    "AUTONOMY_LEVELS",
    "EVENT_KINDS",
    "PLATFORMS",
    "DIFF_FORMATS",
    "Actual",
    "AuditEntry",
    "ContractError",
    "Drift",
    "Intent",
    "Plan",
    "Preview",
    "Skip",
    "may_act_unattended",
    "new_id",
    "utc_now",
]
