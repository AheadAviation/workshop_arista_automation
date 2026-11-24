"""The day-two operation catalogue and its resolver (INIT-005).

The property that matters most here is the one the old form could not provide: **you cannot name a
VLAN that IPAM does not have.** The old page had a free-text VLAN box, so it would configure a
switch with any number typed into it — an object the source of truth had never heard of. That is
what these tests pin, along with the catalogue's own integrity.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from workbench import operations, operations_resolve  # noqa: E402


# --------------------------------------------------------------------------- #
# the catalogue
# --------------------------------------------------------------------------- #


def test_every_operation_has_a_name_that_is_not_a_verbless_noun():
    """Names are read aloud to an operator; "tagged_vlans" is not a name."""
    for op in operations.OPERATIONS:
        assert op.name.strip()
        assert "_" not in op.name, f"{op.key}: name looks like an identifier"
        assert op.summary.strip(), f"{op.key} has no summary"


def test_operation_keys_are_unique():
    keys = [op.key for op in operations.OPERATIONS]
    assert len(keys) == len(set(keys))


def test_every_operation_declares_a_vlan_field_that_exists():
    for op in operations.OPERATIONS:
        assert op.vlan_field, f"{op.key} declares no vlan_field"
        assert op.vlan_field in {f.key for f in op.fields}


def test_every_operation_has_at_least_one_field():
    for op in operations.OPERATIONS:
        assert op.fields, f"{op.key} asks for nothing, so it cannot do anything"


def test_an_unknown_operation_names_the_valid_ones():
    with pytest.raises(operations.UnknownOperation) as exc:
        operations.operation("do-something-else")
    assert "deliver_service" in str(exc.value)


def test_a_static_field_must_declare_its_choices():
    with pytest.raises(ValueError):
        operations.Field(key="x", label="X", source="static")


def test_a_field_cannot_use_an_undeclared_source():
    with pytest.raises(ValueError):
        operations.Field(key="x", label="X", source="telepathy")


def test_vlan_fields_are_sourced_from_ipam_not_typed():
    """The whole point: a VLAN input must be a menu of real objects, never a text box."""
    for op in operations.OPERATIONS:
        vlan_field = next(f for f in op.fields if f.key == op.vlan_field)
        assert vlan_field.source == "ipam.vlan", (
            f"{op.key}: the VLAN field is sourced from {vlan_field.source!r}, "
            "which means it could hold something IPAM has never heard of"
        )


# --------------------------------------------------------------------------- #
# field options come from live data
# --------------------------------------------------------------------------- #


VLANS = [
    {"vid": 120, "name": "STORAGE", "tenant": "Infrastructure", "description": ""},
    {"vid": 333, "name": "PHARMACY", "tenant": "Pharmacy", "description": ""},
]


def test_ipam_field_options_carry_the_name_and_the_owner():
    """A bare "333" means nothing to a person; "PHARMACY (VLAN 333) — Pharmacy" does."""
    field = operations.Field(key="vlan", label="Which?", source="ipam.vlan")
    options = operations_resolve.field_choices(field, VLANS)

    assert [o["value"] for o in options] == ["120", "333"]
    assert "PHARMACY" in options[1]["label"]
    assert "333" in options[1]["label"]
    assert "Pharmacy" in options[1]["label"]


def test_pair_options_come_from_the_fabric_topology():
    field = operations.Field(key="pair", label="Which?", source="mlag.pair")
    options = operations_resolve.field_choices(field, VLANS)
    assert {o["value"] for o in options} == set(__import__("workbench.catalogue", fromlist=["x"]).MLAG_PAIRS)


# --------------------------------------------------------------------------- #
# resolution refuses what is not real
# --------------------------------------------------------------------------- #


def test_a_vlan_that_is_not_in_ipam_is_refused():
    """This is the hole the free-text form had, and the reason this module exists."""
    with pytest.raises(operations_resolve.ResolveError) as exc:
        operations_resolve.resolve(
            operation_key="deliver_service",
            values={"vlan": "999", "scope": "all-leaves"},
            vlans=VLANS,
        )
    message = str(exc.value)
    assert "999" in message
    assert "IPAM" in message
    assert "cannot be delivered" in message


def test_a_non_numeric_vlan_is_refused_cleanly():
    with pytest.raises(operations_resolve.ResolveError) as exc:
        operations_resolve.resolve(
            operation_key="deliver_service",
            values={"vlan": "not-a-number", "scope": "all-leaves"},
            vlans=VLANS,
        )
    assert "not a VLAN number" in str(exc.value)


def test_a_missing_required_field_names_the_field():
    with pytest.raises(operations_resolve.ResolveError) as exc:
        operations_resolve.resolve(
            operation_key="deliver_service", values={"scope": "all-leaves"}, vlans=VLANS
        )
    assert "Which network" in str(exc.value)


def test_a_real_vlan_resolves_to_the_devices_and_a_sentence():
    resolved = operations_resolve.resolve(
        operation_key="deliver_service",
        values={"vlan": "120", "scope": "mlag-pair", "pair": "pair-1"},
        vlans=VLANS,
    )
    assert resolved.vlan_vid == 120
    assert resolved.vlan_name == "STORAGE"
    assert sorted(resolved.devices) == ["leaf-01", "leaf-02"]
    assert "STORAGE" in resolved.sentence
    assert "120" in resolved.sentence


def test_all_leaves_resolves_to_every_leaf_once():
    resolved = operations_resolve.resolve(
        operation_key="deliver_service",
        values={"vlan": "120", "scope": "all-leaves"},
        vlans=VLANS,
    )
    assert sorted(resolved.devices) == ["leaf-01", "leaf-02", "leaf-03", "leaf-04"]


def test_a_storage_array_always_lands_on_a_pair_not_one_switch():
    """A storage array is dual-homed: one switch would leave half its traffic unaddressed."""
    resolved = operations_resolve.resolve(
        operation_key="add_storage_array",
        values={"vlan": "120", "pair": "pair-2", "mtu": "9214"},
        vlans=VLANS,
    )
    assert sorted(resolved.devices) == ["leaf-03", "leaf-04"]
    assert "MTU 9214" in resolved.sentence


def test_retirement_targets_the_whole_estate():
    """Leaving one leaf carrying a retired VLAN is the drift this tool exists to find."""
    resolved = operations_resolve.resolve(
        operation_key="retire_service", values={"vlan": "333"}, vlans=VLANS
    )
    assert sorted(resolved.devices) == ["leaf-01", "leaf-02", "leaf-03", "leaf-04"]
    assert "Retire" in resolved.sentence


def test_a_jack_resolves_to_the_switch_it_is_cabled_to():
    resolved = operations_resolve.resolve(
        operation_key="change_jack",
        values={"building": "east-wing", "jack": "EW-2-0031", "vlan": "120"},
        vlans=VLANS,
    )
    assert resolved.devices == ["leaf-04"]


def test_an_unmodelled_jack_is_refused_rather_than_guessed():
    with pytest.raises(operations_resolve.ResolveError):
        operations_resolve.resolve(
            operation_key="change_jack",
            values={"building": "east-wing", "jack": "NOPE-9", "vlan": "120"},
            vlans=VLANS,
        )


def test_resolved_carries_the_values_back_for_the_page():
    """The page needs to echo what was chosen, so a mistake is visible before it is approved."""
    resolved = operations_resolve.resolve(
        operation_key="add_storage_array",
        values={"vlan": "120", "pair": "pair-1", "mtu": "1500"},
        vlans=VLANS,
    )
    payload = resolved.to_dict()
    assert payload["values"]["mtu"] == "1500"
    assert payload["vlan"] == {"vid": 120, "name": "STORAGE"}


def test_the_sentence_never_leaks_an_internal_key():
    """It is read aloud to an executive; `mlag-pair` is not a word."""
    for op in operations.OPERATIONS:
        resolved = operations_resolve.resolve(
            operation_key=op.key,
            values={
                "vlan": "120",
                "scope": "mlag-pair",
                "pair": "pair-1",
                "mtu": "9214",
                "building": "main-hospital",
                "jack": "MH-1-0142",
            },
            vlans=VLANS,
        )
        for leak in ("mlag-pair", "all-leaves", "None", "{", "ipam.vlan"):
            assert leak not in resolved.sentence, f"{op.key}: {leak!r} leaked into {resolved.sentence!r}"


# --------------------------------------------------------------------------- #
# the payload the page renders
# --------------------------------------------------------------------------- #


def test_describe_all_returns_every_field_with_its_options():
    payload = operations_resolve.describe_all(vlans=VLANS)

    assert payload["ok"] is True
    assert payload["source"] == "netbox:ipam"
    assert payload["vlan_count"] == 2
    for op in payload["operations"]:
        for field in op["fields"]:
            assert "options" in field, f"{op['key']}.{field['key']} has no options key"
            if field["source"] in ("ipam.vlan", "mlag.pair", "static"):
                assert field["options"], (
                    f"{op['key']}.{field['key']} would render an empty dropdown"
                )
