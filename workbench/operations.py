"""The day-two operation catalogue: what a network team is actually asked to do (INIT-005).

**The problem this solves, stated as the user did.** The day-two page was a form: pick a switch,
type a VLAN number, type a VLAN name, click three times. That is CLI thinking wearing a GUI, and
it makes the operator the integration layer — they must know which VLAN a service uses, and which
switches it belongs on. Worse, the VLAN and name were **free text**, so nothing tied the change to
IPAM. You could type VLAN 150 / PHILIPS_MEDICAL against a NetBox that had no such VLAN, and the
page would happily configure a switch with an object the source of truth had never heard of.

**The shape that replaces it.** An **operation** names a *kind of work* — "add a storage array",
"change a jack port", "retire a service". Choosing one reconfigures the form: each operation
declares the **fields** it needs, and those fields are populated from live NetBox data, not typed.
The VLAN list comes from `ipam.vlan`. The device list comes from `dcim.device`. The jack list comes
from the cable plant.

**Why operations rather than one clever form.** Different work has genuinely different inputs. A
storage array needs a service and an MTU; a jack change needs a building, a room and a port; a
retirement needs a service and nothing else. Forcing those into one form is what produced the
switch+VLAN+name triple that fitted none of them well. A declared operation with declared fields
is the smallest structure that fits all three, and it is data — adding a fourth operation is a
list entry, not a template edit.

**Where this stops, deliberately.** An operation *prepares* a change; it does not apply one. Every
operation resolves to the same three things the existing gate already understands — a VLAN, a set
of devices, and a set of plans — so the approval, the audit record, the ticket and the IPAM
write-back are the machinery that already exists and is already proved. This module adds the
front door, not a second road.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: What an operation is fundamentally doing, which decides what the engine runs.
OPERATION_KINDS: tuple[str, ...] = (
    "deliver",   # add a VLAN to trunks
    "retire",    # remove a VLAN from trunks
    "reconfigure",  # change an attribute of something that already exists
)

#: How a field gets its value. `choice` fields are populated from NetBox; `number` and `text` are
#: typed, and are deliberately rare — a field is only ever free text when no system knows the
#: answer, which for this demo means "never" for anything that reaches a switch.
FIELD_SOURCES: tuple[str, ...] = (
    "ipam.vlan",      # every VLAN in IPAM, with vid + name + tenant
    "dcim.device",    # devices in the fabric, by role
    "dcim.site",      # sites
    "mlag.pair",      # the fabric's MLAG pairs, from the catalogue
    "static",         # a fixed list declared here (e.g. MTU sizes)
)


@dataclass(frozen=True)
class Field:
    """One input an operation needs.

    `source` is the load-bearing part: it says **where the answer comes from**. A field whose
    source is `ipam.vlan` can only ever hold a VLAN that actually exists in NetBox, which is the
    whole point — the operator chooses, they do not invent.
    """

    key: str
    label: str
    source: str
    help: str = ""
    required: bool = True
    #: For `static` sources, the choices themselves.
    choices: tuple[tuple[str, str], ...] = ()
    #: For `number` sources only.
    default: str = ""

    def __post_init__(self) -> None:
        if self.source not in FIELD_SOURCES:
            raise ValueError(f"{self.key}: unknown field source {self.source!r}")
        if self.source == "static" and not self.choices:
            raise ValueError(f"{self.key}: a static field needs choices")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "source": self.source,
            "help": self.help,
            "required": self.required,
            "default": self.default,
            "choices": [{"value": v, "label": lbl} for v, lbl in self.choices],
        }


@dataclass(frozen=True)
class Operation:
    """A kind of day-two work, and the inputs it needs.

    `kind` decides what the engine does with the resolved values; `fields` decide what the page
    asks for. The two are deliberately separate: several operations share `kind: deliver` and
    therefore share every downstream safety property, while still presenting different questions.
    """

    key: str
    name: str
    kind: str
    summary: str
    #: The sentence shown once the inputs resolve, with `{placeholders}` filled from the values.
    sentence: str
    fields: tuple[Field, ...] = field(default_factory=tuple)
    #: Which of the resolved values identifies the VLAN. Usually a field sourced from
    #: `ipam.vlan`; `retire` needs one too, and nothing else.
    vlan_field: str = ""

    def __post_init__(self) -> None:
        if self.kind not in OPERATION_KINDS:
            raise ValueError(f"{self.key}: unknown kind {self.kind!r}")
        keys = {f.key for f in self.fields}
        if self.vlan_field and self.vlan_field not in keys:
            raise ValueError(f"{self.key}: vlan_field {self.vlan_field!r} is not a declared field")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "kind": self.kind,
            "summary": self.summary,
            "fields": [f.to_dict() for f in self.fields],
        }


#: The operations the page offers. Written in the language a hospital's network team would use —
#: "add a storage array", not "modify tagged_vlans" — because the point is that the person asking
#: does not need to know how the network models it.
OPERATIONS: tuple[Operation, ...] = (
    Operation(
        key="deliver_service",
        name="Give a service its network",
        kind="deliver",
        summary="Put an existing VLAN on the trunks a service's servers are attached to.",
        sentence="Deliver {vlan} to {place}.",
        vlan_field="vlan",
        fields=(
            Field(
                key="vlan",
                label="Which network?",
                source="ipam.vlan",
                help="Every VLAN below is a real object in IPAM. Nothing here is typed.",
            ),
            Field(
                key="scope",
                label="Where?",
                source="static",
                help="A dual-homed server needs the VLAN on both switches in its pair.",
                choices=(
                    ("mlag-pair", "One MLAG pair (a dual-homed server)"),
                    ("all-leaves", "Every leaf (the whole estate)"),
                ),
            ),
            Field(
                key="pair",
                label="Which pair?",
                source="mlag.pair",
                help="Only used when the scope is a single pair.",
                required=False,
            ),
        ),
    ),
    Operation(
        key="add_storage_array",
        name="Add a storage array",
        kind="deliver",
        summary="A new array arrives: its network, on both legs of the pair, at the right MTU.",
        sentence="Give the new storage array {vlan} on {place}, at MTU {mtu}.",
        vlan_field="vlan",
        fields=(
            Field(
                key="vlan",
                label="Which network does the array live on?",
                source="ipam.vlan",
                help="Storage arrays normally sit on the storage VLAN, not a clinical one.",
            ),
            Field(
                key="pair",
                label="Which pair is the array cabled to?",
                source="mlag.pair",
                help="A storage array is dual-homed: one leg per switch, or half its traffic has "
                     "nowhere to go.",
            ),
            Field(
                key="mtu",
                label="Frame size",
                source="static",
                help="Jumbo frames are the usual cause of a storage array that 'works' but is slow.",
                choices=(
                    ("9214", "9214 (jumbo, 9000 payload)"),
                    ("1500", "1500 (standard)"),
                ),
                default="9214",
            ),
        ),
    ),
    Operation(
        key="change_jack",
        name="Change what a jack delivers",
        kind="deliver",
        summary="A wall port needs a different network — an office becomes a clinical space.",
        sentence="Change {jack} to deliver {vlan}.",
        vlan_field="vlan",
        fields=(
            Field(
                key="building",
                label="Building",
                source="static",
                help="Where the jack is. In this lab the choices are the modelled locations.",
                choices=(
                    ("main-hospital", "Main Hospital"),
                    ("east-wing", "East Wing (clinical)"),
                ),
            ),
            Field(
                key="jack",
                label="Jack",
                source="static",
                help="The wall port, as the facilities team labels it.",
                choices=(
                    ("MH-1-0142", "MH-1-0142 (Main Hospital, floor 1)"),
                    ("EW-2-0031", "EW-2-0031 (East Wing, floor 2)"),
                ),
            ),
            Field(
                key="vlan",
                label="What should it deliver?",
                source="ipam.vlan",
                help="Choose the network this port should provide.",
            ),
        ),
    ),
    Operation(
        key="retire_service",
        name="Retire a network",
        kind="retire",
        summary="A service is decommissioned: take its VLAN off every trunk.",
        sentence="Retire {vlan} from every leaf.",
        vlan_field="vlan",
        fields=(
            Field(
                key="vlan",
                label="Which network?",
                source="ipam.vlan",
                help="Pick the network being retired. It is removed from every leaf trunk.",
            ),
        ),
    ),
)

BY_KEY: dict[str, Operation] = {op.key: op for op in OPERATIONS}


class UnknownOperation(ValueError):
    """An operation that is not in the catalogue. Names the valid ones."""


def operation(key: str) -> Operation:
    found = BY_KEY.get(str(key or "").strip().lower())
    if found is None:
        raise UnknownOperation(
            f"{key!r} is not an operation this tool offers. "
            f"Available: {', '.join(sorted(BY_KEY))}"
        )
    return found


def operations_dicts() -> list[dict[str, Any]]:
    """The catalogue as plain data, for the page to render dynamically."""
    return [op.to_dict() for op in OPERATIONS]


__all__ = [
    "BY_KEY",
    "FIELD_SOURCES",
    "OPERATION_KINDS",
    "OPERATIONS",
    "Field",
    "Operation",
    "UnknownOperation",
    "operation",
    "operations_dicts",
]
