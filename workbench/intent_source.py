"""Read what the network should be (INIT-001/SPEC-002).

Two sources, both already in this repository:

* **NetBox** — the fabric's source of truth for the EOS switches. Read over REST so the
  demo does not depend on Ansible being in the room.
* **AVD group_vars** — the intent for the FRR track (`labs/l3ls-dual-dc`), which is
  deliberately not in NetBox (see `wiki/Dual-DC-L3LS.md`: the dual-DC track keeps its own
  AVD model). Reading it from its real home is what makes the "two vendors, one intent"
  story honest rather than staged.

**On the trunk layer.** The shipped fabric has no server-facing trunks: `Ethernet1/2`
are L3 fabric links and `Ethernet3/4` are MLAG peerlinks. The Philips story is about a
VLAN crossing a *series of trunks*, so the demo needs trunks that carry server VLANs.
Rather than rewrite the workshop's fabric data — which the core workshop and its 206
tests depend on — the demo's trunk layer lives here as its own declared overlay
(`DEMO_TRUNKS`), and the page says plainly that this is a lab overlay for the demo. That
keeps the promise in the approved plan: no rewrites of what the workshop uses.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Mapping

from workbench.contracts import Intent

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The VLANs the demo's tenant owns. The first five are the shipped fabric's own CORE
#: tenant; 150 is the new one the Philips vendor needs, and it starts absent on purpose
#: so the demo has something real to add.
BASE_VLANS: tuple[int, ...] = (100, 110, 120, 130, 140)
NEW_VLAN = 150
NEW_VLAN_NAME = "PHILIPS_MEDICAL"

#: The demo's server-facing trunk layer, per leaf.
#:
#: `Ethernet5` is the server trunk created by `clab/topology-demo.clab.yml`, where each
#: server is dual-homed across an MLAG pair — which is why one VLAN change must land on two
#: switches, and why "a series of trunks" is literally true here.
#:
#: `Ethernet6` is deliberately absent: the shipped fabric gives each leaf only four data
#: ports (two fabric links, two MLAG peerlink members), so eth5 is the one free port. The
#: earlier version of this file listed Ethernet6 as well, which made the demo configure an
#: interface that does not exist — it silently applied to nothing, and the live check caught
#: it. Do not add a port here without adding it to the topology first.
DEMO_TRUNKS: Mapping[str, tuple[str, ...]] = {
    "leaf-01": ("Ethernet5",),
    "leaf-02": ("Ethernet5",),
    "leaf-03": ("Ethernet5",),
    "leaf-04": ("Ethernet5",),
}

#: What each of those trunks carries when the fabric is in its intended state.
DEMO_TRUNK_VLANS: tuple[int, ...] = BASE_VLANS

HttpGetter = Callable[[str], dict[str, Any]]


class IntentSourceError(RuntimeError):
    """The intended state could not be read. Carries a reason fit to show a person."""


# --------------------------------------------------------------------------- #
# NetBox
# --------------------------------------------------------------------------- #


def netbox_getter(url: str | None = None, token: str | None = None) -> HttpGetter:
    """A small JSON GETter for NetBox's REST API.

    The token travels in a header, never in the URL, so it cannot end up in a log line
    or an exception message that names the URL.
    """

    def get(path: str) -> dict[str, Any]:
        import requests

        base = (url or os.environ.get("NETBOX_URL") or "http://localhost:8888").rstrip("/")
        tok = token or os.environ.get("NETBOX_TOKEN") or ""
        headers = {"Accept": "application/json"}
        if tok:
            headers["Authorization"] = f"Token {tok}"
        try:
            response = requests.get(f"{base}{path}", headers=headers, timeout=15)
        except requests.RequestException as exc:
            raise IntentSourceError(f"could not reach NetBox at {base}: {exc}") from exc
        if response.status_code >= 400:
            raise IntentSourceError(
                f"NetBox answered {response.status_code} for {path}"
            )
        return response.json()

    return get


def read_fabric_intent(
    get: HttpGetter | None = None,
    *,
    fabric: str = "dc1-fabric",
    new_vlan: int | None = None,
) -> dict[str, Intent]:
    """The intended state for every switch in one fabric, keyed by device name.

    **IPAM first.** If NetBox has VLANs modelled in IPAM, those VLANs and the interfaces that
    carry them *are* the intent — nothing in this file is consulted for the VLAN list. That is
    the whole point of the initiative this belongs to: the source of truth has to be the source.
    `BASE_VLANS` remains only as a bootstrap fallback for an install where nothing has been
    modelled yet, and when it is used the `source` field says so, so the page can be honest
    about where the intent came from.

    `new_vlan` adds a VLAN to the intended state without touching NetBox. That is how
    the demo shows "here is the change we are about to make" *before* anything is
    written anywhere — the page can preview a proposed intent against live devices,
    which is the honest way to show a change without editing the source of truth.
    """
    fetch = get or netbox_getter()

    devices = _devices(fetch, fabric)
    if not devices:
        raise IntentSourceError(
            f"NetBox returned no devices for fabric {fabric!r}; run .\\run.ps1 import first"
        )

    names = [d.get("name") for d in devices if d.get("name")]
    from_ipam = _try_ipam(fetch, names, new_vlan=new_vlan)

    if from_ipam is not None:
        # Enrich the IPAM intent with the per-device facts IPAM does not hold (ASN, role), so
        # the page keeps everything it had. Only the VLAN source changes.
        for device in devices:
            name = device.get("name") or ""
            intent = from_ipam.get(name)
            if intent is None:
                continue
            cf = device.get("custom_fields") or {}
            from_ipam[name] = Intent(
                device=intent.device,
                platform=intent.platform,
                source=intent.source,
                hostname=intent.hostname,
                role=_role_slug(device),
                asn=int(cf.get("bgp_asn") or 0),
                router_id=cf.get("router_id") or "",
                vlans=intent.vlans,
                trunks=intent.trunks,
                trunk_allowed=intent.trunk_allowed,
                target_os=intent.target_os,
                notes=intent.notes,
            )
        return from_ipam

    vlans = list(BASE_VLANS)
    if new_vlan is not None and new_vlan not in vlans:
        vlans.append(new_vlan)

    intended: dict[str, Intent] = {}
    for device in devices:
        name = device.get("name") or ""
        if not name:
            continue
        cf = device.get("custom_fields") or {}
        role = _role_slug(device)
        trunks = DEMO_TRUNKS.get(name, ())
        # A spine is a fabric device: it carries no server VLANs and no server trunks. Asking
        # it for tenant VLANs made two perfectly healthy spines report as drifted, which is a
        # false finding on screen and exactly the sort of thing that costs credibility.
        tenant_vlans = tuple(vlans) if trunks else ()
        intended[name] = Intent(
            device=name,
            platform="eos",
            source=f"netbox:{fabric} (declared overlay; run seed_ipam.py to model these in IPAM)",
            hostname=name,
            role=role,
            asn=int(cf.get("bgp_asn") or 0),
            router_id=cf.get("router_id") or "",
            vlans=tenant_vlans,
            trunks=trunks,
            trunk_allowed={port: tenant_vlans for port in trunks},
        )
    return intended


def _try_ipam(
    fetch: HttpGetter,
    devices: list[str],
    *,
    new_vlan: int | None = None,
) -> dict[str, Intent] | None:
    """Read intent from IPAM, or `None` if IPAM has nothing modelled yet.

    Returning `None` rather than raising is deliberate: an install with no IPAM VLANs is the
    normal state before anything has been modelled, and failing there would make the demo
    unusable on a fresh clone. But an IPAM **read failure** is different from an empty IPAM,
    and that does raise — a source of truth that cannot be reached must never be silently
    replaced by a fallback, because then the page would show a confident picture built from
    a tuple while the real source was down.
    """
    from workbench import netbox_ipam

    try:
        per_device, vlans = netbox_ipam.read_ipam_intent(
            fetch, fabric="", devices=devices, new_vlan=new_vlan
        )
    except netbox_ipam.IpamError:
        # A genuine read failure. Let it surface: see the docstring.
        raise
    except Exception:
        # Anything else — an unexpected payload shape, a missing endpoint — means IPAM is not
        # usable here, which is a bootstrap situation rather than a lie worth failing on.
        return None

    if not vlans and new_vlan is None:
        return None

    intent = netbox_ipam.intent_from_ipam(per_device, vlans)

    # A device with no trunk in IPAM still needs an entry, or it vanishes from the page and
    # looks like the fabric lost a switch.
    for name in devices:
        if name in intent:
            continue
        intent[name] = Intent(
            device=name,
            platform="eos",
            source="netbox:ipam",
            hostname=name,
            role="spine",
            asn=0,
            router_id="",
            vlans=(),
            trunks=(),
            notes="no tagged trunk modelled in IPAM for this device",
        )

    if new_vlan is not None:
        # The proposed VLAN is not in IPAM yet. Add it to every trunk so a preview shows the
        # change that would happen, without writing anything.
        for name, existing in list(intent.items()):
            if not existing.trunks:
                continue
            if new_vlan in existing.vlans:
                continue
            vlans_now = tuple(sorted({*existing.vlans, new_vlan}))
            intent[name] = Intent(
                device=existing.device,
                platform=existing.platform,
                source=existing.source,
                hostname=existing.hostname,
                role=existing.role,
                asn=existing.asn,
                router_id=existing.router_id,
                vlans=vlans_now,
                trunks=existing.trunks,
                trunk_allowed={port: vlans_now for port in existing.trunks},
                target_os=existing.target_os,
                notes=existing.notes,
            )

    return intent


def _devices(fetch: HttpGetter, fabric: str) -> list[dict[str, Any]]:
    """The devices carrying this fabric's tag, from NetBox."""
    payload = fetch(f"/api/dcim/devices/?cf_fabric_name={fabric}&limit=200")
    results = payload.get("results")
    if results is None:
        raise IntentSourceError(
            "NetBox device response had no 'results' list; is this really the NetBox API?"
        )
    return [d for d in results if isinstance(d, dict)]


def _role_slug(device: dict[str, Any]) -> str:
    role = device.get("role")
    if isinstance(role, dict):
        return str(role.get("slug") or role.get("name") or "").lower()
    if isinstance(role, str):
        return role.lower()
    return ""


# --------------------------------------------------------------------------- #
# The FRR track (AVD group_vars)
# --------------------------------------------------------------------------- #

FRR_INTENT_FILE = REPO_ROOT / "labs" / "l3ls-dual-dc" / "sites" / "core" / "group_vars" / "CORE.yml"


def read_frr_intent(path: Path | None = None) -> Intent:
    """The intended state for `frr-core1`, from the AVD group_vars that define it.

    This is the same intent that renders `templates/frr.conf.j2`, so the demo is
    reading the real source rather than a copy made for the demo. The VLAN and trunk
    story does not apply to a route reflector, so those fields carry the tenant's VLANs
    and nothing else.
    """
    file = path or FRR_INTENT_FILE
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise IntentSourceError(f"could not read the FRR intent at {file}: {exc}") from exc

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a repo requirement
        raise IntentSourceError("PyYAML is required to read the FRR intent") from exc

    data = yaml.safe_load(text) or {}
    asn = _deep_find(data, "bgp_as") or _deep_find(data, "as") or 0
    # The shipped CORE.yml declares `loopback_ipv4_pool` (a subnet), not a per-node
    # router id, because AVD derives the id from the node's `id` within that pool.
    # Reading the node's id and the pool's network is the honest thing here: it is the
    # closest thing to a router id the file actually contains, and the demo shows the
    # pool rather than inventing an address.
    pool = str(_deep_find(data, "loopback_ipv4_pool") or "")
    return Intent(
        device="frr-core1",
        platform="frr",
        source="avd:labs/l3ls-dual-dc/CORE.yml",
        hostname="frr-core1",
        role="core",
        asn=int(asn or 0),
        router_id=pool,
        vlans=(),
        trunks=(),
        notes="Route reflector for the multivendor DCI; carries no tenant VLANs.",
    )


def _deep_find(data: Any, key: str) -> Any:
    """First value for `key` anywhere in a nested mapping.

    The AVD group_vars nest their BGP settings a few levels deep and the exact path
    differs between AVD versions, so this searches rather than hard-coding a path that
    a future AVD bump would silently break.
    """
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = _deep_find(value, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _deep_find(item, key)
            if found is not None:
                return found
    return None


# --------------------------------------------------------------------------- #
# The change the demo proposes
# --------------------------------------------------------------------------- #


def add_vlan_intent(intended: dict[str, Intent], vlan: int = NEW_VLAN) -> dict[str, Intent]:
    """The intended state with one more VLAN on every demo trunk.

    Pure: returns new `Intent` objects and leaves the argument untouched, so the page
    can hold "current intent" and "proposed intent" at the same time and diff them.
    """
    proposed: dict[str, Intent] = {}
    for name, intent in intended.items():
        if vlan in intent.vlans:
            proposed[name] = intent
            continue
        vlans = tuple(sorted({*intent.vlans, vlan}))
        proposed[name] = Intent(
            device=intent.device,
            platform=intent.platform,
            source=intent.source,
            hostname=intent.hostname,
            role=intent.role,
            asn=intent.asn,
            router_id=intent.router_id,
            vlans=vlans,
            trunks=intent.trunks,
            trunk_allowed={port: vlans for port in intent.trunks},
            target_os=intent.target_os,
            notes=intent.notes,
        )
    return proposed


__all__ = [
    "BASE_VLANS",
    "DEMO_TRUNK_VLANS",
    "DEMO_TRUNKS",
    "FRR_INTENT_FILE",
    "IntentSourceError",
    "NEW_VLAN",
    "NEW_VLAN_NAME",
    "add_vlan_intent",
    "netbox_getter",
    "read_fabric_intent",
    "read_frr_intent",
]
