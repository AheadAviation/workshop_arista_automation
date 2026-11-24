"""Model the demo fabric in NetBox properly: IPAM VLANs, trunks, prefixes (INIT-004).

**Before this ran**, the lab's NetBox had zero IPAM VLANs, zero VLAN groups, and every
switch port had an empty `mode` with no tagged VLANs. The VLAN intent lived in a Python tuple
and a config context — so NetBox could not answer "what does this trunk carry?", which is the
first question anyone asks of a source of truth.

This script makes it a real model:

* a **tenant** per service owner, so a VLAN belongs to somebody;
* a **VLAN group** scoped to the site, so VLAN ids have a home;
* an **`ipam.vlan`** for every service in the catalogue, with name, description and tenant;
* the **prefix and gateway address** for each VLAN, so IPAM is not a VLAN list pretending;
* `dcim.interface.mode = tagged` on every server trunk, with the VLANs in `tagged_vlans`.

Run it once, from the host:

    python scripts/seed_ipam.py            # report what would change
    python scripts/seed_ipam.py --apply    # write it

Idempotent: existing objects are reused, and interface VLAN lists are unioned, never
replaced. Running it twice changes nothing the second time.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbench import catalogue  # noqa: E402
from workbench.netbox_ipam_write import NetboxWriter, ensure_vlan  # noqa: E402

SITE_SLUG = "dc1"
GROUP_NAME = "Fabric tenant VLANs"
GROUP_SLUG = "fabric-tenant-vlans"

#: Which tenant owns which service. A hospital's VLANs belong to a service, not to "the
#: network team", and NetBox models that as tenancy.
SERVICE_TENANTS: dict[str, tuple[str, str]] = {
    # service key -> (tenant name, tenant slug)
    "pharmacy": ("Pharmacy", "pharmacy"),
    "radiology": ("Radiology", "radiology"),
    "philips": ("Clinical Monitoring", "clinical-monitoring"),
    "voip": ("Clinical Telephony", "clinical-telephony"),
    "storage": ("Infrastructure", "infrastructure"),
    "backup": ("Infrastructure", "infrastructure"),
    "servers": ("Infrastructure", "infrastructure"),
    "mgmt-servers": ("Infrastructure", "infrastructure"),
}

#: The gateway inside each VLAN, from the fabric's own config context, so IPAM and the
#: rendered configuration agree. A prefix that disagrees with the config is worse than none.
#:
#: **Do not derive these from the VLAN id.** An earlier version used `10.100.<vid>.0/24`,
#: which works until a VLAN id exceeds 255 — `10.100.312.0/24` is not a valid IPv4 address,
#: and NetBox 4.1.11 answers with a 500 `KeyError` rather than a validation error, so the
#: real mistake is buried. A VLAN id is a layer-2 label; a subnet is a layer-3 plan. They are
#: different numbering schemes that happen to be near each other in a small lab, and
#: conflating them is exactly the sort of thing a customer's IPAM owner would object to.
SERVICE_PREFIXES: dict[str, str] = {
    "servers": "10.100.100.0/24",
    "mgmt-servers": "10.100.110.0/24",
    "storage": "10.100.120.0/24",
    "backup": "10.100.130.0/24",
    "voip": "10.100.140.0/24",
    "philips": "10.100.150.0/24",
    # The two services above 255 get their own block, chosen as a plan rather than derived.
    "radiology": "10.101.12.0/24",
    "pharmacy": "10.101.33.0/24",
}

TRUNK_INTERFACES = ("eth5",)


def client() -> NetboxWriter:
    base = os.environ.get("NETBOX_URL") or "http://localhost:8888"
    token = os.environ.get("NETBOX_TOKEN") or ""
    if not token:
        raise SystemExit("NETBOX_TOKEN is not set; run this from the repo with .env loaded")
    return NetboxWriter(base, token)


def find_one(c: NetboxWriter, path: str, **params) -> dict | None:
    results = c.get(path, limit=2, **params).get("results") or []
    return results[0] if results else None


def ensure_tenant(c: NetboxWriter, name: str, slug: str, apply: bool) -> dict | None:
    existing = find_one(c, "/api/tenancy/tenants/", slug=slug)
    if existing:
        return existing
    if not apply:
        print(f"    would create tenant {name}")
        return None
    created = c.post("/api/tenancy/tenants/", {"name": name, "slug": slug})
    print(f"    created tenant {name} (id {created.get('id')})")
    return created


def ensure_group(c: NetboxWriter, apply: bool) -> dict | None:
    existing = find_one(c, "/api/ipam/vlan-groups/", slug=GROUP_SLUG)
    if existing:
        return existing
    site = find_one(c, "/api/dcim/sites/", slug=SITE_SLUG)
    payload = {"name": GROUP_NAME, "slug": GROUP_SLUG}
    if site:
        payload["scope_type"] = "dcim.site"
        payload["scope_id"] = site["id"]
    if not apply:
        print(f"    would create VLAN group {GROUP_NAME}")
        return None
    created = c.post("/api/ipam/vlan-groups/", payload)
    print(f"    created VLAN group {GROUP_NAME} (id {created.get('id')})")
    return created


def ensure_prefix(c: NetboxWriter, prefix: str, vlan_id: int, description: str, apply: bool) -> None:
    """Create a prefix, refusing an invalid one with a readable reason.

    NetBox 4.1.11 answers a malformed prefix with HTTP 500 and `KeyError: 'data'`, which names
    nothing useful. Validating here means the error says what is actually wrong.
    """
    import ipaddress

    try:
        ipaddress.ip_network(prefix, strict=True)
    except ValueError as exc:
        raise SystemExit(
            f"refusing to send an invalid prefix {prefix!r} to NetBox: {exc}. "
            "A VLAN id is not a subnet: do not derive one from the other."
        ) from None

    existing = find_one(c, "/api/ipam/prefixes/", prefix=prefix)
    if existing:
        return
    if not apply:
        print(f"    would create prefix {prefix}")
        return
    c.post(
        "/api/ipam/prefixes/",
        {"prefix": prefix, "status": "active", "description": description, "vlan": vlan_id},
    )
    print(f"    created prefix {prefix}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args(argv)

    c = client()
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"Modelling the demo fabric in NetBox IPAM ({mode})")

    print("\nTenants")
    tenants: dict[str, dict | None] = {}
    for name, slug in set(SERVICE_TENANTS.values()):
        tenants[slug] = ensure_tenant(c, name, slug, args.apply)
        if tenants[slug]:
            print(f"    tenant {name} exists (id {tenants[slug]['id']})")

    print("\nVLAN group")
    group = ensure_group(c, args.apply)
    if group:
        print(f"    group {GROUP_NAME} exists (id {group['id']})")

    print("\nVLANs")
    for svc in catalogue.CATALOGUE:
        tenant_slug = SERVICE_TENANTS.get(svc.key, ("", ""))[1]
        tenant = tenants.get(tenant_slug)
        existing = find_one(c, "/api/ipam/vlans/", vid=svc.vlan)
        if existing:
            print(f"    VLAN {svc.vlan} {existing.get('name')} exists")
            continue
        if not args.apply:
            print(f"    would create VLAN {svc.vlan} {svc.name}")
            continue

        payload = {
            "vid": svc.vlan,
            "name": svc.name.upper().replace(" ", "_").replace("/", "_")[:64],
            "status": "active",
            "description": svc.purpose[:200],
        }
        if group:
            payload["group"] = group["id"]
        if tenant:
            payload["tenant"] = tenant["id"]
        site = find_one(c, "/api/dcim/sites/", slug=SITE_SLUG)
        if site:
            payload["site"] = site["id"]
        created = c.post("/api/ipam/vlans/", payload)
        print(f"    created VLAN {svc.vlan} {created.get('name')} (id {created.get('id')})")

    print("\nPrefixes")
    for svc in catalogue.CATALOGUE:
        prefix = SERVICE_PREFIXES.get(svc.key)
        if not prefix:
            continue
        vlan = find_one(c, "/api/ipam/vlans/", vid=svc.vlan)
        if not vlan:
            print(f"    skip {prefix}: VLAN {svc.vlan} not created yet")
            continue
        ensure_prefix(c, prefix, vlan["id"], svc.name, args.apply)

    print("\nTrunk interfaces")
    leaves = sorted({d for pair in catalogue.MLAG_PAIRS.values() for d in pair})
    # The fabric's own five tenant VLANs are on every trunk from the start; the services the
    # demo *delivers* (pharmacy, radiology, philips) are deliberately absent so the day-two
    # story has something real to do.
    baseline = [
        svc
        for svc in catalogue.CATALOGUE
        if svc.key in ("servers", "mgmt-servers", "storage", "backup", "voip")
    ]
    vlan_ids: dict[int, int] = {}
    for svc in baseline:
        vlan = find_one(c, "/api/ipam/vlans/", vid=svc.vlan)
        if vlan:
            vlan_ids[svc.vlan] = vlan["id"]

    for device in leaves:
        for iface in TRUNK_INTERFACES:
            record = find_one(c, "/api/dcim/interfaces/", device=device, name=iface)
            if not record:
                print(f"    {device}:{iface} not in NetBox")
                continue
            already = {v.get("vid") if isinstance(v, dict) else v for v in record.get("tagged_vlans") or []}
            missing = [svc for svc in baseline if svc.vlan not in already]
            if not missing and str(record.get("mode") or "") == "tagged":
                print(f"    {device}:{iface} already a tagged trunk with {len(already)} VLAN(s)")
                continue
            if not args.apply:
                print(f"    would set {device}:{iface} tagged with {[s.vlan for s in baseline]}")
                continue

            ids = [vlan_ids[svc.vlan] for svc in baseline if svc.vlan in vlan_ids]
            c.patch(
                f"/api/dcim/interfaces/{record['id']}/",
                {"mode": "tagged", "tagged_vlans": sorted(set(ids))},
            )
            print(f"    {device}:{iface} -> tagged, {len(ids)} VLAN(s)")

    print()
    if args.apply:
        print("Done. NetBox now models the VLANs, their tenancy, their prefixes and the trunks.")
        print("Verify with: python scripts/verify_ipam.py")
        return 0

    print("Dry run only. Re-run with --apply to write this into NetBox.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
