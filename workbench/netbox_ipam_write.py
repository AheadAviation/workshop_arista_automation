"""Write VLAN intent back into NetBox's IPAM, so the model stays true (INIT-004).

**The rule this enforces.** If the demo configures a VLAN on a switch, NetBox must say so
afterwards. A tool that changes the network and leaves the source of truth stale is worse
than no tool: the next person reads NetBox, believes it, and is wrong. That is precisely the
"rogue system" failure a network director is right to refuse.

So applying a change has two halves, in this order:

1. **Write intent.** Create the `ipam.vlan` if it does not exist, then add it to each trunk
   interface's `tagged_vlans` (setting `mode = tagged`, because an interface with tagged
   VLANs and no mode is a contradiction NetBox will happily store).
2. **Then configure the device.** The order matters: if the write fails, nothing is pushed,
   and the switch cannot drift away from a model that was never updated.

**Idempotent by construction.** `get_or_create` for the VLAN, and set-union for the
interfaces, so running a change twice leaves the same state rather than duplicating or
erroring. The demo is presented twice in a day; that has to be safe.

**Rollback, and its limits.** `remove_vlan_from_trunks` reverses the interface half and
optionally deletes the VLAN. It is written for the demo reset and for a rejected change. It
deliberately does not try to restore a snapshot of prior interface state: this models
intent, and if a VLAN should not be on a trunk, removing it *is* the correct intent.
"""

from __future__ import annotations

from typing import Any, Iterable

from workbench.netbox_ipam import IpamError, netbox_name


# --------------------------------------------------------------------------- #
# The HTTP side, kept injectable so this is testable without NetBox
# --------------------------------------------------------------------------- #


class NetboxWriter:
    """A minimal NetBox API client for the writes this demo needs.

    Separate from the read path because a reader should not be able to mutate, and because
    the token that can write is a different risk from the one that can read.
    """

    def __init__(self, base: str, token: str, *, timeout: float = 20.0) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            # In a header, never the URL: a token in a query string ends up in logs and in
            # screenshots of the address bar.
            headers["Authorization"] = f"Token {self.token}"
        return headers

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        import requests

        try:
            response = requests.get(
                f"{self.base}{path}", headers=self._headers(), params=params, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise IpamError(f"could not reach NetBox at {self.base}: {exc}") from exc
        if response.status_code >= 400:
            raise IpamError(f"NetBox answered {response.status_code} for GET {path}")
        return response.json()

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        import requests

        try:
            response = requests.post(
                f"{self.base}{path}", headers=self._headers(), json=payload, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise IpamError(f"could not reach NetBox at {self.base}: {exc}") from exc
        if response.status_code >= 400:
            raise IpamError(
                f"NetBox answered {response.status_code} for POST {path}: {response.text[:200]}"
            )
        return response.json()

    def patch(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        import requests

        try:
            response = requests.patch(
                f"{self.base}{path}", headers=self._headers(), json=payload, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise IpamError(f"could not reach NetBox at {self.base}: {exc}") from exc
        if response.status_code >= 400:
            raise IpamError(
                f"NetBox answered {response.status_code} for PATCH {path}: {response.text[:200]}"
            )
        return response.json()

    def delete(self, path: str) -> bool:
        import requests

        try:
            response = requests.delete(f"{self.base}{path}", headers=self._headers(), timeout=self.timeout)
        except requests.RequestException as exc:
            raise IpamError(f"could not reach NetBox at {self.base}: {exc}") from exc
        return response.status_code in (204, 404)


# --------------------------------------------------------------------------- #
# VLANs
# --------------------------------------------------------------------------- #


def ensure_vlan(
    client: NetboxWriter,
    *,
    vid: int,
    name: str,
    site: str | None = None,
    tenant: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Create the VLAN in IPAM if it is not there, and return it.

    Idempotent: an existing VLAN is returned as-is rather than erroring or duplicating. The
    name is **not** overwritten on an existing VLAN — a VLAN's name is often used as a label
    by another team, and silently renaming it from a demo would be a surprise.
    """
    existing = client.get("/api/ipam/vlans/", vid=vid, limit=2)
    results = existing.get("results") or []
    if results:
        return results[0]

    payload: dict[str, Any] = {"vid": int(vid), "name": name or f"VLAN{vid}", "status": "active"}
    if description:
        payload["description"] = description
    if tenant:
        found = client.get("/api/tenancy/tenants/", slug=tenant, limit=2).get("results") or []
        if found:
            payload["tenant"] = found[0]["id"]
    if site:
        found = client.get("/api/dcim/sites/", slug=site, limit=2).get("results") or []
        if found:
            payload["site"] = found[0]["id"]

    return client.post("/api/ipam/vlans/", payload)


# --------------------------------------------------------------------------- #
# Interfaces
# --------------------------------------------------------------------------- #


def _interface(client: NetboxWriter, device: str, interface: str) -> dict[str, Any] | None:
    name = netbox_name(interface)
    found = client.get("/api/dcim/interfaces/", device=device, name=name, limit=2).get("results") or []
    return found[0] if found else None


def _tagged_pks(interface: dict[str, Any]) -> list[int]:
    """The **primary keys** of the tagged VLANs on an interface.

    NetBox's `tagged_vlans` write field takes VLAN primary keys (`ipam.vlan.id`), not VLAN
    numbers (`vid`). Sending `100` meaning "the VLAN numbered 100" produced
    `Invalid pk "100" - object does not exist`, because VLAN 100 happens to have pk 7 in this
    lab. The read path deals in `vid` because that is what a person reads; this is the one
    place the distinction matters, so it is named for it.
    """
    pks: list[int] = []
    for entry in interface.get("tagged_vlans") or []:
        if isinstance(entry, dict) and entry.get("id") is not None:
            pks.append(int(entry["id"]))
        elif isinstance(entry, int):
            pks.append(entry)
    return pks


def _tagged_vids(interface: dict[str, Any]) -> list[int]:
    """The VLAN *numbers* on an interface, for comparing against intent."""
    vids: list[int] = []
    for entry in interface.get("tagged_vlans") or []:
        if isinstance(entry, dict) and entry.get("vid") is not None:
            vids.append(int(entry["vid"]))
        elif isinstance(entry, int):
            vids.append(entry)
    return vids


def _mode_of(interface: dict[str, Any]) -> str:
    """NetBox returns a choice field as `{"value": ..., "label": ...}`, not a string.

    Comparing `str(record["mode"])` to `"tagged"` never matches, which silently skipped every
    trunk. See the same note in `netbox_ipam._choice_value`.
    """
    mode = interface.get("mode")
    if isinstance(mode, dict):
        return str(mode.get("value") or "")
    return str(mode or "")


def add_vlan_to_trunks(
    client: NetboxWriter,
    *,
    device: str,
    interface: str,
    vid: int,
    vlan_name: str,
    site: str | None = None,
) -> dict[str, Any]:
    """Record that `interface` on `device` carries `vid` as a tagged VLAN.

    Does three things, idempotently:

    1. ensures the VLAN exists in IPAM,
    2. sets the interface's `mode` to `tagged` — a trunk with tagged VLANs and no mode is a
       state NetBox will store but that contradicts itself,
    3. adds the VLAN to `tagged_vlans` as a union, never replacing the existing list.

    Returns what changed, so a caller can report honestly rather than assuming.
    """
    vlan = ensure_vlan(client, vid=vid, name=vlan_name, site=site)
    record = _interface(client, device, interface)
    if record is None:
        raise IpamError(
            f"NetBox does not model {device}:{interface} — import the fabric first "
            "(the interface name in NetBox is the short form, e.g. eth5)"
        )

    vlan_id = vlan.get("id")
    if vlan_id is None:
        raise IpamError(f"NetBox did not return an id for VLAN {vid}")

    current_vids = _tagged_vids(record)
    current_pks = _tagged_pks(record)
    payload: dict[str, Any] = {}
    notes: list[str] = []

    if _mode_of(record) != "tagged":
        payload["mode"] = "tagged"
        notes.append(f"mode {_mode_of(record) or '(none)'} -> tagged")

    if vid not in current_vids:
        # Union on **primary keys**: NetBox rejects a pk that does not exist, and a union that
        # mixes pks and vids would either corrupt the list or drop an existing VLAN.
        payload["tagged_vlans"] = sorted({*current_pks, int(vlan_id)})
        notes.append(f"tagged_vlans + {vid}")

    if not payload:
        return {
            "ok": True,
            "changed": False,
            "headline": f"{device}:{interface} already records VLAN {vid}",
            "notes": [],
            "vlan_id": vlan_id,
        }

    client.patch(f"/api/dcim/interfaces/{record['id']}/", payload)
    return {
        "ok": True,
        "changed": True,
        "headline": f"{device}:{interface} now records VLAN {vid}",
        "notes": notes,
        "vlan_id": vlan_id,
    }


def record_service_on_devices(
    client: NetboxWriter,
    *,
    vid: int,
    vlan_name: str,
    devices: Iterable[str],
    trunk: str = "Ethernet5",
    site: str | None = None,
) -> dict[str, Any]:
    """Write one service's VLAN onto every device in a change. All-or-nothing per device.

    Returns per-device outcomes rather than raising on the first failure: a partial write
    that is *reported* is far more useful than an exception that hides which switches were
    updated, and the caller can then choose not to push to the ones that failed.
    """
    results: list[dict[str, Any]] = []
    failures = 0
    changed = 0

    for device in devices:
        try:
            outcome = add_vlan_to_trunks(
                client, device=device, interface=trunk, vid=vid, vlan_name=vlan_name, site=site
            )
        except IpamError as exc:
            results.append({"device": device, "ok": False, "detail": str(exc)[:200]})
            failures += 1
            continue
        results.append({"device": device, **outcome})
        if outcome.get("changed"):
            changed += 1

    return {
        "ok": failures == 0,
        "results": results,
        "failures": failures,
        "changed": changed,
        "headline": (
            f"NetBox updated on {len(results) - failures} of {len(results)} switch(es)"
            if results
            else "no switches to update"
        ),
    }


def remove_vlan_from_trunks(
    client: NetboxWriter,
    *,
    vid: int,
    devices: Iterable[str],
    trunk: str = "Ethernet5",
    delete_vlan: bool = False,
) -> dict[str, Any]:
    """Reverse the interface half of a change, and optionally the VLAN itself.

    Used by the demo reset and by a rejected change. Deliberately not a snapshot restore:
    this models intent, and the intent after a rollback is "that trunk does not carry this
    VLAN".
    """
    results: list[dict[str, Any]] = []
    for device in devices:
        record = _interface(client, device, trunk)
        if record is None:
            results.append({"device": device, "ok": False, "detail": "interface not in NetBox"})
            continue

        current_vids = _tagged_vids(record)
        if vid not in current_vids:
            results.append({"device": device, "ok": True, "changed": False})
            continue

        # Keep the primary keys whose vid is not the one being removed.
        keep: list[int] = []
        for entry in record.get("tagged_vlans") or []:
            if isinstance(entry, dict):
                if entry.get("vid") is not None and int(entry["vid"]) == vid:
                    continue
                if entry.get("id") is not None:
                    keep.append(int(entry["id"]))
            elif isinstance(entry, int):
                keep.append(entry)

        client.patch(f"/api/dcim/interfaces/{record['id']}/", {"tagged_vlans": keep})
        results.append({"device": device, "ok": True, "changed": True})

    deleted = False
    if delete_vlan:
        found = client.get("/api/ipam/vlans/", vid=vid, limit=2).get("results") or []
        if found:
            # Remove from every interface first, or NetBox refuses to delete a VLAN in use.
            for record in client.get("/api/dcim/interfaces/", limit=500).get("results") or []:
                if vid not in _tagged_vids(record):
                    continue
                keep: list[int] = []
                for entry in record.get("tagged_vlans") or []:
                    if isinstance(entry, dict):
                        if entry.get("vid") is not None and int(entry["vid"]) == vid:
                            continue
                        if entry.get("id") is not None:
                            keep.append(int(entry["id"]))
                    elif isinstance(entry, int):
                        keep.append(entry)
                client.patch(f"/api/dcim/interfaces/{record['id']}/", {"tagged_vlans": keep})
            deleted = client.delete(f"/api/ipam/vlans/{found[0]['id']}/")

    return {
        "ok": all(r.get("ok") for r in results),
        "results": results,
        "vlan_deleted": deleted,
    }


__all__ = [
    "NetboxWriter",
    "add_vlan_to_trunks",
    "ensure_vlan",
    "record_service_on_devices",
    "remove_vlan_from_trunks",
]
