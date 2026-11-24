"""The service catalogue: what a network team is actually asked to do (INIT-003).

**The problem this solves.** The day-two flow asked a presenter to pick a switch from a
dropdown and type a VLAN number. That is CLI thinking wearing a GUI, and it is exactly what
Brian Nelson said to avoid in the room: nobody in an executive briefing wants to choose
`leaf-03` and remember that pharmacy is 333.

The pattern this copies is the one `twix` uses at a university network — you pick a
**service** ("Pharmacy", "Radiology") and a **place** ("this server", "this building"), and
the tool works out the switch, the port and the configuration. The VLAN number is an
implementation detail of the service, not something a human types.

So this module turns the change into a sentence:

    "Give the new pharmacy system the pharmacy network." → VLAN 333 on four trunks.

**Why a catalogue rather than free text.** A service that exists is a thing the network
already knows how to deliver: a VLAN with a name, a gateway, and a set of trunks that should
carry it. Offering only services that are declared means the demo cannot produce a
nonsense change — and it matches how a real access-request tool works, where the template
list comes from the source of truth rather than from whatever the requester can spell.

The catalogue is data, not code, so a site can replace it with their own service names
without touching this file's logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from workbench.contracts import ContractError

#: Where a service should be delivered. Ordered because the page renders them in this order.
SCOPES: tuple[str, ...] = ("all-leaves", "mlag-pair", "one-switch")


@dataclass(frozen=True)
class Service:
    """One thing the network can be asked to deliver.

    `vlan` is the implementation. `name` is what a person recognises. `purpose` is why it
    exists, which is the sentence a presenter reads out and the reason an executive cares.
    """

    key: str
    name: str
    vlan: int
    purpose: str
    #: Which scope this service is normally delivered at. A clinical system is dual-homed
    #: across an MLAG pair; a management service belongs everywhere.
    default_scope: str = "mlag-pair"

    def __post_init__(self) -> None:
        if not (1 <= self.vlan <= 4094):
            raise ContractError(f"{self.key}: vlan {self.vlan} is outside 1-4094")
        if self.default_scope not in SCOPES:
            raise ContractError(f"{self.key}: unknown scope {self.default_scope!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "vlan": self.vlan,
            "purpose": self.purpose,
            "default_scope": self.default_scope,
        }


#: The demo's catalogue. Deliberately the tenant VLANs the fabric already models, plus the
#: pharmacy network the story needs, so every entry is a real thing rather than a prop.
#:
#: Names are what a hospital's network team would say out loud. None of them is a number,
#: because the point is that a requester does not think in numbers.
CATALOGUE: tuple[Service, ...] = (
    Service(
        key="pharmacy",
        name="Pharmacy",
        vlan=333,
        purpose="Pharmacy dispensing and label printing. Clinical, needs its own segment.",
        default_scope="mlag-pair",
    ),
    Service(
        key="radiology",
        name="Radiology / PACS",
        vlan=312,
        purpose="Imaging. High throughput, large frames, evenings are the busy window.",
        default_scope="mlag-pair",
    ),
    Service(
        key="philips",
        name="Philips patient monitoring",
        vlan=150,
        purpose="Bedside monitors. The clinical system that arrives needing its own VLAN.",
        default_scope="mlag-pair",
    ),
    Service(
        key="voip",
        name="Voice / clinical phones",
        vlan=140,
        purpose="Nurse call and clinical handsets. Latency-sensitive.",
        default_scope="all-leaves",
    ),
    Service(
        key="storage",
        name="Storage",
        vlan=120,
        purpose="The arrays. Jumbo frames, and the MTU mistake that breaks it silently.",
        default_scope="mlag-pair",
    ),
    Service(
        key="backup",
        name="Backup",
        vlan=130,
        purpose="Nightly backup traffic, deliberately kept off the clinical segments.",
        default_scope="all-leaves",
    ),
    Service(
        key="servers",
        name="General servers",
        vlan=100,
        purpose="Shared application servers that belong to no single clinical service.",
        default_scope="all-leaves",
    ),
    Service(
        key="mgmt-servers",
        name="Server management",
        vlan=110,
        purpose="Out-of-band management for the server estate.",
        default_scope="all-leaves",
    ),
)

BY_KEY: Mapping[str, Service] = {s.key: s for s in CATALOGUE}

#: The MLAG pairs, as they are cabled. This is the "place" a change is delivered to, and the
#: reason one request legitimately touches two switches: a server is dual-homed across a pair,
#: so its VLAN must exist on both legs or half its traffic has nowhere to go.
MLAG_PAIRS: Mapping[str, tuple[str, str]] = {
    "pair-1": ("leaf-01", "leaf-02"),
    "pair-2": ("leaf-03", "leaf-04"),
}

PAIR_LABELS: Mapping[str, str] = {
    "pair-1": "MLAG pair 1 (leaf-01 + leaf-02) — Philips servers",
    "pair-2": "MLAG pair 2 (leaf-03 + leaf-04) — second server pair",
}

SCOPE_LABELS: Mapping[str, str] = {
    "all-leaves": "Every leaf (the whole estate)",
    "mlag-pair": "One MLAG pair (a dual-homed server)",
    "one-switch": "A single switch (a maintenance fix)",
}


class UnknownService(ContractError):
    """A service that is not in the catalogue. Carries the list, so the page can offer it."""


def service(key: str) -> Service:
    """One service by key. Raises with the valid keys rather than returning None."""
    found = BY_KEY.get(str(key or "").strip().lower())
    if found is None:
        raise UnknownService(
            f"{key!r} is not a service this network offers. "
            f"Available: {', '.join(sorted(BY_KEY))}"
        )
    return found


def targets(scope: str, *, pair: str = "", device: str = "") -> list[str]:
    """Which switches a change is delivered to, given a scope and a place.

    This is the function that makes the change a sentence rather than a checklist: the caller
    says "pharmacy, to pair-1" and gets the switches back. It never returns an empty list —
    a scope that resolves to nothing is a mistake worth raising, because a silent no-op
    change is the worst thing to show.
    """
    if scope not in SCOPES:
        raise ContractError(f"unknown scope {scope!r}; expected one of {', '.join(SCOPES)}")

    if scope == "all-leaves":
        return sorted({d for pair_members in MLAG_PAIRS.values() for d in pair_members})

    if scope == "mlag-pair":
        members = MLAG_PAIRS.get(pair)
        if not members:
            raise ContractError(
                f"unknown MLAG pair {pair!r}; expected one of {', '.join(MLAG_PAIRS)}"
            )
        return list(members)

    # one-switch
    every = {d for pair_members in MLAG_PAIRS.values() for d in pair_members}
    if device not in every:
        raise ContractError(
            f"{device!r} is not a leaf in this fabric; expected one of {', '.join(sorted(every))}"
        )
    return [device]


def describe(service_key: str, scope: str, *, pair: str = "", device: str = "") -> str:
    """The change as one sentence, for a presenter to read out.

    This sentence is the product. If it does not make sense to a CIO who has never seen a
    switch, the workflow is wrong regardless of how the code is written.

    Written as two plain clauses joined by a dash rather than one long sentence with
    agreement puzzles: an earlier version produced "they carries", which is the kind of thing
    that undermines a demo the moment it is read aloud.
    """
    svc = service(service_key)
    switches = targets(scope, pair=pair, device=device)
    count = len(switches)

    if scope == "all-leaves":
        where = "every leaf switch"
    elif scope == "mlag-pair":
        where = "both switches in {}".format(PAIR_LABELS.get(pair, pair))
    else:
        where = switches[0]

    if count == 1:
        tail = "1 switch will change"
    else:
        tail = "{} switches will change".format(count)

    return "Deliver {} (VLAN {}) to {} - {}".format(svc.name, svc.vlan, where, tail)


def catalogue_dicts() -> list[dict[str, Any]]:
    """The catalogue as plain data, for the page and the API."""
    return [s.to_dict() for s in CATALOGUE]


def scope_dicts() -> list[dict[str, Any]]:
    """The scopes and places, so the page does not hardcode the fabric's shape."""
    return [
        {
            "key": key,
            "label": SCOPE_LABELS[key],
            "places": (
                [{"key": p, "label": label} for p, label in PAIR_LABELS.items()]
                if key == "mlag-pair"
                else []
            ),
        }
        for key in SCOPES
    ]


__all__ = [
    "BY_KEY",
    "CATALOGUE",
    "MLAG_PAIRS",
    "PAIR_LABELS",
    "SCOPES",
    "SCOPE_LABELS",
    "Service",
    "UnknownService",
    "catalogue_dicts",
    "describe",
    "scope_dicts",
    "service",
    "targets",
]
