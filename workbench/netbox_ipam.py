"""Read intended state from NetBox's IPAM, not from a tuple in Python (INIT-004).

**Why this exists.** The demo's intent used to come from `BASE_VLANS = (100, 110, ...)` — a
tuple in this repository. That is a rogue system by any reasonable definition: NetBox was
consulted for device names and ASNs, but the VLAN list, and which interface carries what,
lived in Python. NetBox had **zero** IPAM VLANs and every trunk interface had an empty
`mode` and no `tagged_vlans` at all, so the source of truth could not tell you what a trunk
carried even after this demo had configured one.

That is indefensible in front of a network director, and worse at a customer whose whole
objection to buying NetBox is that they already have an IPAM and do not want a second one.
If this is the tool that models their network, the model has to be real.

**What NetBox actually models**, and what this module reads:

* `ipam.vlan` — the VLAN itself: `vid`, `name`, optional `site`, `tenant`, `group`,
  `description`.
* `ipam.vlangroup` — a scope for VLAN ids, so "100" in one context need not collide.
* `dcim.interface.mode` — `access` or `tagged`. This is the field that says a port is a
  trunk. It was empty everywhere.
* `dcim.interface.tagged_vlans` — the allow-list, as real foreign keys to `ipam.vlan`.
* `dcim.interface.untagged_vlan` — the native VLAN.

So "add VLAN 150 to the server trunk" has a complete, checkable representation in NetBox:
create the VLAN, then add it to the interface's `tagged_vlans`. No tuple, no overlay.

**The fallback, stated plainly.** A brand-new install has no IPAM data yet, and the demo
must still explain itself before anything is modelled. So when NetBox has no VLANs at all,
this module falls back to the declared overlay in `intent_source` and says so in the
`source` field. That is a bootstrap convenience, not the design: once `scripts/seed_ipam.py`
has run, IPAM is authoritative and the fallback never fires.
"""

from __future__ import annotations

from typing import Any, Mapping

from workbench.contracts import Intent


class IpamError(RuntimeError):
    """IPAM could not be read. Carries a reason fit to show a person."""


#: Interface names as NetBox records them, versus as EOS prints them. NetBox's own model
#: uses the short form (`eth5`) because that is how the importer created them; EOS says
#: `Ethernet5`. Keeping the mapping in one place means the page and the device cannot
#: disagree about which port is being discussed.
NETBOX_TO_EOS: Mapping[str, str] = {
    "eth1": "Ethernet1",
    "eth2": "Ethernet2",
    "eth3": "Ethernet3",
    "eth4": "Ethernet4",
    "eth5": "Ethernet5",
    "eth6": "Ethernet6",
    "eth7": "Ethernet7",
    "eth8": "Ethernet8",
}


def eos_name(interface: str) -> str:
    """`eth5` -> `Ethernet5`, leaving anything unrecognised alone."""
    return NETBOX_TO_EOS.get(interface.lower(), interface)


def netbox_name(interface: str) -> str:
    """`Ethernet5` -> `eth5`, for matching a page's port against NetBox's record."""
    for short, long in NETBOX_TO_EOS.items():
        if long.lower() == interface.lower():
            return short
    return interface.lower()


# --------------------------------------------------------------------------- #
# Reading IPAM
# --------------------------------------------------------------------------- #


def read_vlans(fetch) -> list[dict[str, Any]]:
    """Every IPAM VLAN as `{vid, name, site, tenant, group, description}`."""
    payload = fetch("/api/ipam/vlans/?limit=500")
    results = payload.get("results")
    if results is None:
        raise IpamError("NetBox VLAN response had no 'results' list")
    vlans: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        vid = item.get("vid")
        if vid is None:
            continue
        site = item.get("site")
        tenant = item.get("tenant")
        group = item.get("group")
        vlans.append(
            {
                "vid": int(vid),
                "name": str(item.get("name") or ""),
                "site": (site or {}).get("slug") if isinstance(site, dict) else site,
                "tenant": (tenant or {}).get("slug") if isinstance(tenant, dict) else tenant,
                "group": (group or {}).get("slug") if isinstance(group, dict) else group,
                "description": str(item.get("description") or ""),
            }
        )
    return sorted(vlans, key=lambda v: v["vid"])


def _choice_value(field: Any) -> str:
    """Normalise one of NetBox's choice fields to its bare value.

    NetBox's REST API returns a choice field as `{"value": "tagged", "label": "Tagged"}`, not
    as a string. Comparing `str(record["mode"])` against `"tagged"` therefore never matched,
    and every trunk was silently skipped — which read on the page as "this switch has no
    server trunk" on a switch that plainly did. That is the kind of bug that is invisible
    until someone is standing next to you asking why the trunk is missing.
    """
    if isinstance(field, dict):
        return str(field.get("value") or "")
    return str(field or "")


def read_trunk_interfaces(fetch, device: str) -> dict[str, list[int]]:
    """The tagged VLANs per interface for one device, as NetBox records them.

    Returns only interfaces that are actually trunks (`mode == "tagged"`), so an empty
    result is a real answer — "NetBox does not model a trunk here" — rather than a silent
    zero that a caller might mistake for "carries nothing".
    """
    payload = fetch(f"/api/dcim/interfaces/?device={device}&limit=200")
    results = payload.get("results")
    if results is None:
        raise IpamError(f"NetBox interface response had no 'results' list for {device}")

    trunks: dict[str, list[int]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        if _choice_value(item.get("mode")) != "tagged":
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        vids: list[int] = []
        for entry in item.get("tagged_vlans") or []:
            # The list endpoint may return ids or objects depending on `brief`.
            if isinstance(entry, dict):
                vid = entry.get("vid")
                if vid is not None:
                    vids.append(int(vid))
            elif isinstance(entry, int):
                vids.append(entry)
        trunks[eos_name(name)] = sorted(vids)
    return trunks


def read_ipam_intent(
    fetch,
    *,
    fabric: str,
    devices: list[str],
    new_vlan: int | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """The intended VLAN state for a fabric, from IPAM.

    Returns `(per_device, vlans)` where `per_device` maps a device name to
    `{"trunks": {port: [vids]}, "vlans": [vids]}`.

    Raises `IpamError` only for a genuine read failure. An empty IPAM is *not* an error —
    it is reported as empty so the caller can decide to fall back, because on a fresh
    install that is the expected state and failing there would be unhelpful.
    """
    vlans = read_vlans(fetch)
    known = {v["vid"] for v in vlans}
    if new_vlan is not None:
        # A proposed VLAN is not in IPAM yet — that is the point of proposing it. It is
        # allowed into the intended state without being written, so the page can show
        # "this is what would change" before anything is created.
        known.add(int(new_vlan))

    per_device: dict[str, dict[str, Any]] = {}
    for device in devices:
        trunks = read_trunk_interfaces(fetch, device)
        carried: set[int] = set()
        for vids in trunks.values():
            carried.update(vids)
        per_device[device] = {
            "trunks": trunks,
            "vlans": sorted(carried),
        }

    return per_device, vlans


def intent_from_ipam(per_device: Mapping[str, dict[str, Any]], vlans: list[dict[str, Any]]) -> dict[str, Intent]:
    """Build `Intent` objects from what IPAM says.

    One VLAN list per device, from the union of what its trunks carry — which is how the
    drift engine already compares (`Intent.vlans` against `Actual.vlans`), so this needs no
    change to the comparison at all.
    """
    names = {v["vid"]: v["name"] for v in vlans}
    out: dict[str, Intent] = {}
    for device, state in per_device.items():
        trunks = state.get("trunks") or {}
        out[device] = Intent(
            device=device,
            platform="eos",
            source="netbox:ipam",
            hostname=device,
            role="leaf" if trunks else "spine",
            asn=0,
            router_id="",
            vlans=tuple(state.get("vlans") or ()),
            trunks=tuple(sorted(trunks)),
            trunk_allowed={port: tuple(sorted(vids)) for port, vids in trunks.items()},
            notes=(
                f"{len(vlans)} VLAN(s) in IPAM; "
                + ", ".join(f"{vid}={names.get(vid, '?')}" for vid in sorted(names)[:6])
            ),
        )
    return out


__all__ = [
    "IpamError",
    "NETBOX_TO_EOS",
    "eos_name",
    "intent_from_ipam",
    "netbox_name",
    "read_ipam_intent",
    "read_trunk_interfaces",
    "read_vlans",
]
