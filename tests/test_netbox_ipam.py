"""NetBox IPAM as the source of truth (INIT-004).

The problem this closes: the demo's VLAN intent lived in a Python tuple. NetBox had zero IPAM
VLANs and every trunk interface had an empty `mode` and no tagged VLANs, so the "source of
truth" could not answer what a trunk carried — even after the demo had configured one.

These tests pin the two properties that make IPAM trustworthy here:

1. **Reads are honest.** A read failure raises; an empty IPAM returns empty. Those are
   different, and confusing them either lies to the page or breaks a fresh install.
2. **Writes are idempotent and additive.** Applying the same change twice leaves the same
   state, and a VLAN list is unioned, never replaced — a replace would silently drop a VLAN
   another team put on that trunk.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from workbench import netbox_ipam  # noqa: E402
from workbench.netbox_ipam import IpamError, eos_name, netbox_name  # noqa: E402
from workbench.netbox_ipam_write import (  # noqa: E402
    NetboxWriter,
    add_vlan_to_trunks,
    remove_vlan_from_trunks,
)


# --------------------------------------------------------------------------- #
# interface naming
# --------------------------------------------------------------------------- #


def test_interface_names_translate_both_ways():
    """NetBox stores `eth5`; EOS says `Ethernet5`. One mapping, so nothing disagrees."""
    assert eos_name("eth5") == "Ethernet5"
    assert netbox_name("Ethernet5") == "eth5"
    assert netbox_name("ethernet5") == "eth5"


def test_an_unknown_interface_name_is_left_alone():
    assert eos_name("Port-Channel2000") == "Port-Channel2000"
    assert netbox_name("Port-Channel2000") == "port-channel2000"


def test_the_mapping_round_trips():
    for short in netbox_ipam.NETBOX_TO_EOS:
        assert netbox_name(eos_name(short)) == short


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #


def _fetch(routes):
    """A tiny fake NetBox: exact path -> payload."""

    def get(path):
        if path not in routes:
            raise AssertionError(f"unexpected path {path}; have {sorted(routes)}")
        return routes[path]

    return get


def test_vlans_are_read_with_their_tenancy_and_group():
    payload = {
        "results": [
            {"vid": 333, "name": "PHARMACY", "tenant": {"slug": "pharmacy"},
             "group": {"slug": "fabric"}, "site": {"slug": "dc1"}, "description": "d"},
            {"vid": 100, "name": "SERVERS"},
        ]
    }
    vlans = netbox_ipam.read_vlans(_fetch({"/api/ipam/vlans/?limit=500": payload}))

    assert [v["vid"] for v in vlans] == [100, 333], "sorted by vid"
    pharmacy = next(v for v in vlans if v["vid"] == 333)
    assert pharmacy["tenant"] == "pharmacy"
    assert pharmacy["group"] == "fabric"


def test_a_vlan_without_a_vid_is_skipped_not_crashed():
    payload = {"results": [{"name": "broken"}, {"vid": 5, "name": "OK"}]}
    vlans = netbox_ipam.read_vlans(_fetch({"/api/ipam/vlans/?limit=500": payload}))
    assert [v["vid"] for v in vlans] == [5]


def test_a_malformed_vlan_response_is_an_error_not_an_empty_list():
    """`[]` and "I could not look" mean very different things to someone about to present."""
    with pytest.raises(IpamError):
        netbox_ipam.read_vlans(_fetch({"/api/ipam/vlans/?limit=500": {}}))


def test_only_tagged_interfaces_are_treated_as_trunks():
    payload = {
        "results": [
            {"name": "eth1", "mode": "", "tagged_vlans": []},
            {"name": "eth5", "mode": "tagged", "tagged_vlans": [{"vid": 100}, {"vid": 110}]},
            {"name": "eth6", "mode": "access", "tagged_vlans": []},
        ]
    }
    trunks = netbox_ipam.read_trunk_interfaces(
        _fetch({"/api/dcim/interfaces/?device=leaf-01&limit=200": payload}), "leaf-01"
    )

    assert trunks == {"Ethernet5": [100, 110]}, "and the name is EOS form"


def test_an_interface_with_no_trunk_is_not_reported_as_a_trunk():
    payload = {"results": [{"name": "eth5", "mode": "", "tagged_vlans": []}]}
    trunks = netbox_ipam.read_trunk_interfaces(
        _fetch({"/api/dcim/interfaces/?device=leaf-01&limit=200": payload}), "leaf-01"
    )
    assert trunks == {}


def test_intent_from_ipam_uses_the_union_of_what_the_trunks_carry():
    per_device = {"leaf-01": {"trunks": {"Ethernet5": [100, 150]}, "vlans": [100, 150]}}
    intent = netbox_ipam.intent_from_ipam(per_device, [{"vid": 100, "name": "SERVERS"}])

    assert intent["leaf-01"].source == "netbox:ipam"
    assert intent["leaf-01"].vlans == (100, 150)
    assert intent["leaf-01"].trunk_allowed["Ethernet5"] == (100, 150)


def test_a_device_with_no_trunks_is_a_spine_by_implication():
    per_device = {"spine-01": {"trunks": {}, "vlans": []}}
    intent = netbox_ipam.intent_from_ipam(per_device, [{"vid": 100, "name": "S"}])
    assert intent["spine-01"].role == "spine"
    assert intent["spine-01"].vlans == ()


# --------------------------------------------------------------------------- #
# writing, against a fake NetBox
# --------------------------------------------------------------------------- #


class FakeNetbox(NetboxWriter):
    """A NetBox that records writes and answers reads from memory."""

    def __init__(self, *, vlans=None, interfaces=None):
        super().__init__("http://fake", "token")
        self.vlans = vlans or []
        self.interfaces = interfaces or {}
        self.patches: list[tuple[str, dict]] = []
        self.posts: list[tuple[str, dict]] = []
        self.deleted: list[str] = []

    def get(self, path, **params):  # noqa: D102
        if path == "/api/ipam/vlans/":
            vid = params.get("vid")
            found = [v for v in self.vlans if vid is None or v["vid"] == vid]
            return {"results": found}
        if path == "/api/dcim/interfaces/":
            if params.get("limit") == 500:
                return {"results": list(self.interfaces.values())}
            device, name = params.get("device"), params.get("name")
            for iface in self.interfaces.values():
                if iface["device"] == device and iface["name"] == name:
                    return {"results": [iface]}
            return {"results": []}
        if path == "/api/tenancy/tenants/":
            return {"results": []}
        if path == "/api/dcim/sites/":
            return {"results": [{"id": 1, "slug": "dc1"}]}
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, payload):  # noqa: D102
        self.posts.append((path, payload))
        if path == "/api/ipam/vlans/":
            created = {"id": len(self.vlans) + 1, **payload}
            self.vlans.append(created)
            return created
        raise AssertionError(f"unexpected POST {path}")

    def patch(self, path, payload):  # noqa: D102
        self.patches.append((path, payload))
        return {"id": 1, **payload}

    def delete(self, path):  # noqa: D102
        self.deleted.append(path)
        return True


def _trunk(device="leaf-01", name="eth5", mode="", tagged=None):
    """An interface as NetBox's API actually returns it.

    Two details that are easy to get wrong and were both wrong in the first version of this
    fake, which is why they then broke against the real API:

    * `mode` is a **choice object**, `{"value": "tagged", "label": "Tagged"}`, not a string.
    * `tagged_vlans` entries carry both `id` (the pk, which is what a write needs) and `vid`
      (the number a human reads). They are different integers in a real lab.
    """
    tagged = tagged or []
    return {
        "id": 42,
        "device": device,
        "name": name,
        "mode": {"value": mode, "label": mode.title()} if mode else None,
        "tagged_vlans": [
            {"id": 1000 + v, "vid": v, "name": f"VLAN{v}"} for v in tagged
        ],
    }


def test_adding_a_vlan_creates_it_then_tags_the_interface():
    nb = FakeNetbox(interfaces={"eth5": _trunk()})

    result = add_vlan_to_trunks(nb, device="leaf-01", interface="Ethernet5", vid=150, vlan_name="PHILIPS")

    assert result["changed"] is True
    assert any(p[0] == "/api/ipam/vlans/" for p in nb.posts), "the VLAN must exist in IPAM"
    assert nb.patches, "the interface must be updated"
    path, payload = nb.patches[0]
    assert path == "/api/dcim/interfaces/42/"
    # The write carries the VLAN's **primary key**, not its number: NetBox rejects a pk that
    # does not exist, and VLAN 150 need not have pk 150.
    assert payload["tagged_vlans"] == [result["vlan_id"]]


def test_adding_a_vlan_sends_a_primary_key_not_a_vlan_number():
    """The live bug: `Invalid pk "100" - object does not exist`, because VLAN 100 had pk 7."""
    nb = FakeNetbox(interfaces={"eth5": _trunk()})
    result = add_vlan_to_trunks(nb, device="leaf-01", interface="Ethernet5", vid=333, vlan_name="P")

    _path, payload = nb.patches[0]
    assert payload["tagged_vlans"] == [result["vlan_id"]]
    assert 333 not in payload["tagged_vlans"] or result["vlan_id"] == 333


def test_adding_a_vlan_sets_mode_tagged():
    """An interface with tagged VLANs and no mode is a contradiction NetBox will happily store."""
    nb = FakeNetbox(interfaces={"eth5": _trunk(mode="")})
    add_vlan_to_trunks(nb, device="leaf-01", interface="Ethernet5", vid=150, vlan_name="P")
    _path, payload = nb.patches[0]
    assert payload["mode"] == "tagged"


def test_adding_a_vlan_never_drops_an_existing_one():
    """Union, not replace: a replace would silently remove a VLAN another team added."""
    nb = FakeNetbox(interfaces={"eth5": _trunk(mode="tagged", tagged=[100, 110])})
    result = add_vlan_to_trunks(nb, device="leaf-01", interface="Ethernet5", vid=150, vlan_name="P")
    _path, payload = nb.patches[0]

    # The existing pks are preserved and the new vlan's pk is added.
    assert 1100 in payload["tagged_vlans"], "VLAN 100's pk must survive"
    assert 1110 in payload["tagged_vlans"], "VLAN 110's pk must survive"
    assert result["vlan_id"] in payload["tagged_vlans"]


def test_adding_the_same_vlan_twice_changes_nothing_the_second_time():
    nb = FakeNetbox(
        vlans=[{"id": 9, "vid": 150, "name": "P"}],
        interfaces={"eth5": _trunk(mode="tagged", tagged=[150])},
    )
    result = add_vlan_to_trunks(nb, device="leaf-01", interface="Ethernet5", vid=150, vlan_name="P")

    assert result["changed"] is False
    assert nb.patches == [], "idempotent: no write when nothing would change"


def test_an_existing_vlan_is_reused_not_duplicated():
    nb = FakeNetbox(vlans=[{"id": 9, "vid": 150, "name": "SOMETHING_ELSE"}], interfaces={"eth5": _trunk()})
    add_vlan_to_trunks(nb, device="leaf-01", interface="Ethernet5", vid=150, vlan_name="PHILIPS")

    assert not any(p[0] == "/api/ipam/vlans/" for p in nb.posts), "no duplicate VLAN"
    # And the existing name is left alone: it may be another team's label.
    assert nb.vlans[0]["name"] == "SOMETHING_ELSE"


def test_an_interface_netbox_does_not_model_is_a_clear_error():
    nb = FakeNetbox(interfaces={})
    with pytest.raises(IpamError) as exc:
        add_vlan_to_trunks(nb, device="leaf-01", interface="Ethernet5", vid=150, vlan_name="P")
    assert "eth5" in str(exc.value), "the message should name the interface NetBox expects"


def test_removing_a_vlan_takes_it_off_the_interface():
    nb = FakeNetbox(interfaces={"eth5": _trunk(mode="tagged", tagged=[100, 150])})
    result = remove_vlan_from_trunks(nb, vid=150, devices=["leaf-01"])

    assert result["ok"] is True
    _path, payload = nb.patches[0]
    # VLAN 100's pk survives; VLAN 150's is gone.
    assert payload["tagged_vlans"] == [1100]


def test_removing_a_vlan_that_is_not_there_is_a_no_op():
    nb = FakeNetbox(interfaces={"eth5": _trunk(mode="tagged", tagged=[100])})
    result = remove_vlan_from_trunks(nb, vid=150, devices=["leaf-01"])
    assert result["ok"] is True
    assert nb.patches == []


def test_deleting_the_vlan_also_untags_it_everywhere_first():
    """NetBox refuses to delete a VLAN still in use, so the order has to be untag-then-delete."""
    nb = FakeNetbox(
        vlans=[{"id": 9, "vid": 150, "name": "P"}],
        interfaces={"eth5": _trunk(mode="tagged", tagged=[150])},
    )
    result = remove_vlan_from_trunks(nb, vid=150, devices=["leaf-01"], delete_vlan=True)

    assert result["vlan_deleted"] is True
    assert nb.deleted == ["/api/ipam/vlans/9/"]


# --------------------------------------------------------------------------- #
# the token
# --------------------------------------------------------------------------- #


def test_the_token_travels_in_a_header_not_the_url():
    writer = NetboxWriter("http://netbox", "supersecret")
    headers = writer._headers()
    assert headers["Authorization"] == "Token supersecret"
    assert "supersecret" not in writer.base


# --------------------------------------------------------------------------- #
# NetBox's choice-field shape
# --------------------------------------------------------------------------- #
# NetBox returns a choice field as {"value": ..., "label": ...}. Comparing the dict to a string
# never matches, and this bug skipped every trunk in the fabric — the page said "no server trunk
# declared" for switches that plainly had one. Pinned here because it is invisible on inspection.


def test_mode_is_read_from_netboxs_choice_field_shape():
    payload = {
        "results": [
            {"name": "eth5", "mode": {"value": "tagged", "label": "Tagged"},
             "tagged_vlans": [{"vid": 100}, {"vid": 110}]},
        ]
    }
    trunks = netbox_ipam.read_trunk_interfaces(
        _fetch({"/api/dcim/interfaces/?device=leaf-01&limit=200": payload}), "leaf-01"
    )
    assert trunks == {"Ethernet5": [100, 110]}, "a dict-shaped mode must still be understood"


def test_mode_is_also_read_from_a_plain_string():
    """Older NetBox versions, and brief-mode responses, can return a bare string."""
    payload = {"results": [{"name": "eth5", "mode": "tagged", "tagged_vlans": [{"vid": 100}]}]}
    trunks = netbox_ipam.read_trunk_interfaces(
        _fetch({"/api/dcim/interfaces/?device=leaf-01&limit=200": payload}), "leaf-01"
    )
    assert trunks == {"Ethernet5": [100]}


def test_an_access_mode_dict_is_not_a_trunk():
    payload = {"results": [{"name": "eth5", "mode": {"value": "access", "label": "Access"}, "tagged_vlans": []}]}
    trunks = netbox_ipam.read_trunk_interfaces(
        _fetch({"/api/dcim/interfaces/?device=leaf-01&limit=200": payload}), "leaf-01"
    )
    assert trunks == {}
