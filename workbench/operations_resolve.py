"""Resolve an operation's inputs into a real change, against live NetBox data (INIT-005).

This is the half that keeps the page honest. The page offers dropdowns; this module is what
decides whether a submitted value is actually a thing that exists. Every VLAN id is re-read from
IPAM rather than trusted from the request, so a crafted POST cannot configure a switch with a VLAN
that NetBox has never heard of — which was exactly the hole in the free-text form it replaces.

**Why resolution is a separate step from planning.** Choosing "add a storage array" and picking
values does not yet say *which switches* or *what configuration*. Resolution turns the submitted
values into: the `ipam.vlan` object, the concrete device list, and the field values that will go in
the sentence and the config. Planning then builds plans from that. Keeping them apart means the
page can show a resolved sentence ("Give the new storage array STORAGE on MLAG pair 1, at MTU
9214") *before* previewing a diff, which is what makes the flow feel like a conversation rather
than a form.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from workbench import catalogue, operations
from workbench.contracts import ContractError


class ResolveError(RuntimeError):
    """The submitted values do not resolve to a real change. Fit to show a person."""


@dataclass(frozen=True)
class Resolved:
    """Everything a planning step needs, and nothing it has to guess."""

    operation: operations.Operation
    vlan_vid: int
    vlan_name: str
    devices: list[str]
    values: Mapping[str, Any]
    sentence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation.to_dict(),
            "vlan": {"vid": self.vlan_vid, "name": self.vlan_name},
            "devices": list(self.devices),
            "values": dict(self.values),
            "sentence": self.sentence,
        }


#: A fetcher for IPAM VLANs, injectable so this is testable without NetBox.
VlanFetcher = Callable[[], list[dict[str, Any]]]


def _fetch_vlans_live() -> list[dict[str, Any]]:
    """Every VLAN in IPAM, read from NetBox.

    Imported lazily so this module can be imported (and tested) on a machine with no NetBox
    settings, and so a read failure is a page-shaped error rather than an import-time crash.
    """
    from netbox_ops import conf
    from workbench.netbox_ipam_write import NetboxWriter

    client = NetboxWriter(conf.netbox_url(), conf.netbox_token())
    payload = client.get("/api/ipam/vlans/", limit=500)
    results = payload.get("results")
    if results is None:
        raise ResolveError("NetBox returned no VLAN list; is the IPAM reachable?")
    vlans: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict) or item.get("vid") is None:
            continue
        tenant = item.get("tenant")
        vlans.append(
            {
                "vid": int(item["vid"]),
                "name": str(item.get("name") or ""),
                "tenant": (tenant or {}).get("name") if isinstance(tenant, dict) else "",
                "description": str(item.get("description") or ""),
            }
        )
    return sorted(vlans, key=lambda v: v["vid"])


def field_choices(field: operations.Field, vlans: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The options for one field, from live data.

    This is what the page renders and re-renders as the operator changes operation. Returning the
    same `{value,label}` shape for every source means the front end has no per-source special
    cases — it does not know or care whether a list came from IPAM or from a static tuple.
    """
    if field.source == "ipam.vlan":
        return [
            {
                "value": str(v["vid"]),
                # The label carries the name and the owner, because "VLAN 333" alone means
                # nothing to a person; "Pharmacy (VLAN 333)" does.
                "label": f"{v['name']} (VLAN {v['vid']})"
                + (f" — {v['tenant']}" if v.get("tenant") else ""),
            }
            for v in vlans
        ]

    if field.source == "mlag.pair":
        return [
            {"value": key, "label": catalogue.PAIR_LABELS.get(key, key)}
            for key in catalogue.MLAG_PAIRS
        ]

    if field.source == "static":
        return [{"value": v, "label": lbl} for v, lbl in field.choices]

    # dcim.device / dcim.site are not needed by any current operation. Returning empty rather
    # than inventing data keeps a half-built field visible instead of silently blank.
    return []


def resolve(
    *,
    operation_key: str,
    values: Mapping[str, Any],
    vlans: list[dict[str, Any]] | None = None,
    fetch: VlanFetcher | None = None,
) -> Resolved:
    """Turn submitted values into a real change, or raise with a reason a person can act on.

    Every value that names something in NetBox is **matched against the live list**, never
    accepted as given. That is the difference between a dropdown and a text box: a text box can
    hold a VLAN that does not exist, and the old form would configure a switch with it.
    """
    op = operations.operation(operation_key)

    known = vlans
    if known is None:
        known = (fetch or _fetch_vlans_live)()

    # --- required fields are all present -------------------------------------------------
    missing = [
        f.label
        for f in op.fields
        if f.required and not str(values.get(f.key, "") or "").strip()
    ]
    if missing:
        raise ResolveError("Choose " + ", ".join(missing) + ".")

    # --- the VLAN must be a real IPAM object ---------------------------------------------
    if not op.vlan_field:
        raise ResolveError(f"operation {op.key} declares no VLAN field")

    raw_vid = str(values.get(op.vlan_field, "") or "").strip()
    try:
        vid = int(raw_vid)
    except (TypeError, ValueError):
        raise ResolveError(f"{raw_vid!r} is not a VLAN number.") from None

    match = next((v for v in known if int(v["vid"]) == vid), None)
    if match is None:
        # Named explicitly, because this is the failure the old free-text form could not even
        # detect: it would happily configure a VLAN that IPAM had never heard of.
        raise ResolveError(
            f"VLAN {vid} is not in IPAM, so it cannot be delivered. "
            "Create it in IPAM first, or choose one that exists."
        )

    # --- which switches ---------------------------------------------------------------
    devices = _devices_for(op, values)

    # --- the sentence a human reads before approving -----------------------------------
    place = _place_label(op, values, devices)
    sentence = op.sentence.format(
        vlan=f"{match['name']} (VLAN {vid})",
        place=place,
        jack=_jack_label(op, values),
        mtu=str(values.get("mtu") or ""),
        building="",
    )

    return Resolved(
        operation=op,
        vlan_vid=vid,
        vlan_name=str(match["name"] or f"VLAN{vid}"),
        devices=devices,
        values={k: str(v) for k, v in values.items()},
        sentence=sentence,
    )


def _devices_for(op: operations.Operation, values: Mapping[str, Any]) -> list[str]:
    """The switches this operation lands on.

    Resolution is explicit per operation rather than derived from a scope string, because the
    operations genuinely differ: a storage array always lands on one pair (its own cabling), while
    a service can be delivered everywhere. Encoding that here keeps the page from offering a scope
    whose answer the operator would have to guess.
    """
    if op.kind == "retire":
        # A retirement is fleet-wide: leaving one leaf carrying a retired VLAN is the drift this
        # whole tool exists to find.
        return catalogue.targets("all-leaves")

    if op.key == "change_jack":
        # A wall port hangs off one access switch. The demo's jack-to-switch mapping is declared
        # in the catalogue rather than resolved from cable data, which is stated plainly on the
        # page — the lab has no modelled cable plant for wall ports.
        jack = str(values.get("jack") or "").strip()
        mapping = {
            "MH-1-0142": ["leaf-03"],
            "EW-2-0031": ["leaf-04"],
        }
        if jack not in mapping:
            raise ResolveError(f"jack {jack!r} is not one of the modelled wall ports")
        return mapping[jack]

    if op.key == "add_storage_array":
        pair = str(values.get("pair") or "").strip()
        return catalogue.targets("mlag-pair", pair=pair)

    # deliver_service
    scope = str(values.get("scope") or "").strip() or "mlag-pair"
    if scope == "all-leaves":
        return catalogue.targets("all-leaves")
    return catalogue.targets("mlag-pair", pair=str(values.get("pair") or "").strip())


def _place_label(op: operations.Operation, values: Mapping[str, Any], devices: list[str]) -> str:
    if op.key == "change_jack":
        return ""
    if len(devices) == 1:
        return devices[0]
    pair = str(values.get("pair") or "").strip()
    if pair and op.key != "retire_service":
        return catalogue.PAIR_LABELS.get(pair, pair)
    return f"every leaf switch ({len(devices)})"


def _jack_label(op: operations.Operation, values: Mapping[str, Any]) -> str:
    if op.key != "change_jack":
        return ""
    jack = str(values.get("jack") or "").strip()
    return jack


def describe_all(vlans: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The catalogue plus every field's options, so the page can render without extra calls.

    One payload rather than a request per field: the page swaps the whole form when the operation
    changes, and round-tripping per field would make that feel laggy in front of an audience.
    """
    known = vlans if vlans is not None else _fetch_vlans_live()

    ops = []
    for op in operations.OPERATIONS:
        payload = op.to_dict()
        for field_payload, field in zip(payload["fields"], op.fields):
            field_payload["options"] = field_choices(field, known)
        ops.append(payload)

    return {
        "ok": True,
        "operations": ops,
        "vlan_count": len(known),
        "source": "netbox:ipam",
    }


__all__ = [
    "ResolveError",
    "Resolved",
    "describe_all",
    "field_choices",
    "resolve",
]
