"""Write a computed Arista fabric into NetBox.

Takes a fabric spec from the New Site wizard, computes the topology with
`fabric.build_fabric`, and creates the real NetBox objects the workshop
pipeline consumes: a Site, cEOS-lab Devices (tagged `containerlab` so the
dynamic inventory + topology generator pick them up), their interfaces + IPs,
the fabric cables, per-device custom_fields, the site BGP ASNs, a base-config
config-context, and the EVPN L2 VLANs.

Framework-agnostic: plain functions over the REST client in client.py.
"""
from . import baseconfig
from . import client
from . import conf as config
from . import fabric

# Workshop EVPN L2 segments (matches netbox_data/fabric_topology.yml tenants).
VLAN_PLAN = [
    (100, "SERVERS"), (110, "MGMT_SERVERS"), (120, "STORAGE"),
    (130, "BACKUP"), (140, "VOIP"),
]

# Device custom fields the Ansible pipeline consumes (inventory/netbox.yml +
# roles/arista_config). Mirrors scripts/bootstrap_netbox.py so the portal works
# even if DC1 was never bootstrapped; reused if already present.
FABRIC_CUSTOM_FIELDS = [
    ("bgp_asn", "integer", "BGP ASN"),
    ("router_id", "text", "Router ID"),
    ("vtep_ip", "text", "VTEP IP"),
    ("switch_id", "integer", "Switch ID"),
    ("mlag_index", "integer", "MLAG Index"),
    ("mlag_name", "text", "MLAG Name"),
    ("mlag_domain_id", "integer", "MLAG Domain ID"),
    ("mlag_peer", "text", "MLAG Peer"),
    ("fabric_name", "text", "Fabric Name"),
]


def ensure_custom_fields(errors):
    for name, type_, label in FABRIC_CUSTOM_FIELDS:
        try:
            client.get_or_create_custom_field(name, type=type_, label=label)
        except client.NetBoxError as e:
            errors.append({"type": "custom-field", "name": name, "error": e.to_dict()})


def _tag_ids(names):
    return [client.get_or_create_tag(n)["id"] for n in names]


def resolve_seq(site_slug):
    """DC1 is the reserved seq=1. Each portal-built fabric gets a stable seq
    from its creation-order position among portal sites (so re-pushing the same
    site keeps its seq; a brand-new site is appended). Sorting by NetBox id
    keeps existing sites' seq fixed as new ones are added, which is essential -
    seq drives all per-fabric address/ASN offsets, so it must never shift for an
    already-built fabric."""
    sites = sorted(client.list_sites_by_tag(config.self_portal_tag()), key=lambda s: s["id"])
    slugs = [s["slug"] for s in sites]
    if site_slug in slugs:
        return 2 + slugs.index(site_slug)
    return 2 + len(slugs)


def _push_asns(site_id, summary, tag_ids, created, skipped, errors):
    rir = client.get_or_create_rir("RFC 6996 (Private Use)", is_private=True)
    asn_ids = []
    for asn_number in [summary["spine_asn"], *summary["leaf_asns"]]:
        try:
            existing = client.find_asn(asn_number)
            if existing:
                skipped.append({"type": "asn", "name": str(asn_number)})
                asn_ids.append(existing["id"])
            else:
                asn = client.create_asn(asn_number, description=f"{summary['fabric_name']} BGP ASN",
                                        tag_ids=tag_ids, rir_id=rir["id"])
                created.append({"type": "asn", "name": str(asn_number)})
                asn_ids.append(asn["id"])
        except client.NetBoxError as e:
            errors.append({"type": "asn", "name": str(asn_number), "error": e.to_dict()})
    if asn_ids:
        try:
            client.set_site_asns(site_id, asn_ids)
        except client.NetBoxError as e:
            errors.append({"type": "site-asns", "name": summary["fabric_name"], "error": e.to_dict()})


def _push_config_context(plan, site_name, created, skipped, errors):
    data = baseconfig.normalize(plan.get("baseConfig"))
    name = f"{baseconfig.CONTEXT_NAME} - {site_name}"
    try:
        existing = client.find_config_context_by_name(name)
        client.get_or_create_config_context(
            name, data, tag_slugs=[config.self_portal_tag()],
            description=f"Base config / services for {site_name} (AHEAD ZTP Portal).",
        )
        (skipped if existing else created).append({"type": "config-context", "name": name})
    except client.NetBoxError as e:
        errors.append({"type": "config-context", "name": name, "error": e.to_dict()})


def _push_mlag_contexts(built, created, skipped, errors):
    """One config-context per MLAG leaf pair, scoped to that pair's tag, holding
    the `mlag_domain` structure roles/arista_config's mlag_domain.j2 consumes -
    mirroring scripts/bootstrap_netbox.py's per-pair contexts for DC1."""
    for pair in built.get("mlag_pairs", []):
        # Ensure the pair scope tag exists (also applied to the leaf devices).
        client.get_or_create_tag(pair["tag"])
        name = f"MLAG {pair['tag']}"
        try:
            existing = client.find_config_context_by_name(name)
            client.get_or_create_config_context(
                name, {"mlag_domain": pair["mlag_domain"]}, tag_slugs=[pair["tag"]],
                description=f"MLAG peer-link for {pair['tag']} (AHEAD ZTP Portal).", weight=4000,
            )
            (skipped if existing else created).append({"type": "config-context", "name": name})
        except client.NetBoxError as e:
            errors.append({"type": "config-context", "name": name, "error": e.to_dict()})


def _push_vlans(site_id, tag_ids, created, errors):
    for vid, name in VLAN_PLAN:
        try:
            client.get_or_create_vlan(vid, name, site_id=site_id, tag_ids=tag_ids)
            created.append({"type": "vlan", "name": f"{vid} {name}"})
        except client.NetBoxError as e:
            errors.append({"type": "vlan", "name": f"{vid} {name}", "error": e.to_dict()})


def push_site_plan(plan):
    """Create (or reuse) all NetBox objects for the fabric described by `plan`.

    plan = {site_code, num_spines, num_leaf_pairs, seq?, baseConfig?}
    Returns {ok, site, summary, created, skipped, errors}.
    """
    created, skipped, errors = [], [], []

    site_code = (plan.get("site_code") or plan.get("site") or "DC2").strip().upper()
    site_slug = site_code.lower()
    num_spines = int(plan.get("num_spines", 2))
    num_leaf_pairs = int(plan.get("num_leaf_pairs", 2))
    seq = int(plan.get("seq") or resolve_seq(site_slug))

    built = fabric.build_fabric(site_code, num_spines, num_leaf_pairs, seq)
    summary = built["summary"]

    ensure_custom_fields(errors)

    portal_tag = config.self_portal_tag()
    clab_tag = config.containerlab_tag()
    base_tag_ids = _tag_ids([portal_tag, clab_tag, "evpn-fabric"])
    role_tag_ids = {r: _tag_ids([r]) for r in ("spine", "leaf")}

    # ---- Site ----
    description = plan.get("description") or f"{summary['fabric_name']} - built by the AHEAD ZTP Portal"
    existing_site = client.find_site_by_slug(site_slug)
    if existing_site:
        site = existing_site
        skipped.append({"type": "site", "name": site_code, "id": site["id"]})
    else:
        try:
            site = client.create_site(site_code, site_slug, base_tag_ids, description=description)
            created.append({"type": "site", "name": site_code, "id": site["id"]})
        except client.NetBoxError as e:
            errors.append({"type": "site", "name": site_code, "error": e.to_dict()})
            return {"ok": False, "site": None, "summary": summary,
                    "created": created, "skipped": skipped, "errors": errors}
    site_id = site["id"]

    # Optional region
    if plan.get("region"):
        try:
            region = client.get_or_create_region(plan["region"])
            client.set_site_region(site_id, region["id"])
        except client.NetBoxError as e:
            errors.append({"type": "region", "name": plan["region"], "error": e.to_dict()})

    # ---- Manufacturer / device type / roles ----
    manufacturer = client.get_or_create_manufacturer(config.manufacturer())
    device_type = client.get_or_create_device_type(
        config.ceos_device_type_model(), manufacturer["id"], slug=config.ceos_device_type_slug())
    roles = {
        "spine": client.get_or_create_device_role("spine", color="f44336"),
        "leaf": client.get_or_create_device_role("leaf", color="2196f3"),
    }

    # ---- Devices + interfaces + IPs (before cabling) ----
    iface_index = {}  # (device_name, iface_name) -> interface id
    for d in built["devices"]:
        tags = base_tag_ids + role_tag_ids[d["role"]]
        if d.get("mlag_pair_tag"):
            tags = tags + _tag_ids([d["mlag_pair_tag"]])
        existing = client.find_device_by_name(d["name"])
        if existing:
            device = existing
            # Keep custom_fields + tags in sync on re-push (fabric re-design / drift).
            try:
                client.patch(f"dcim/devices/{device['id']}/",
                             {"custom_fields": d["custom_fields"], "tags": tags})
            except client.NetBoxError as e:
                errors.append({"type": "device-cf", "name": d["name"], "error": e.to_dict()})
            skipped.append({"type": "device", "name": d["name"], "id": device["id"]})
        else:
            try:
                device = client.create_device(
                    name=d["name"], device_type_id=device_type["id"],
                    role_id=roles[d["role"]]["id"], site_id=site_id, status="active",
                    tag_ids=tags, custom_fields=d["custom_fields"],
                )
                created.append({"type": "device", "name": d["name"], "id": device["id"]})
            except client.NetBoxError as e:
                errors.append({"type": "device", "name": d["name"], "error": e.to_dict()})
                continue

        primary_ip_id = None
        for spec in d["interfaces"]:
            try:
                iface = client.get_or_create_interface(
                    device["id"], spec["name"], type=spec.get("type", "10gbase-x-sfpp"),
                    description=spec.get("description", ""))
                iface_index[(d["name"], spec["name"])] = iface["id"]
                if spec.get("ip"):
                    ip = client.assign_ip_to_interface(
                        spec["ip"], iface["id"], tag_ids=base_tag_ids,
                        description=f"{d['name']} {spec['name']}")
                    if spec["name"] == d.get("mgmt_interface"):
                        primary_ip_id = ip["id"]
            except client.NetBoxError as e:
                errors.append({"type": "interface", "name": f"{d['name']} {spec['name']}",
                               "error": e.to_dict()})
        if primary_ip_id and not device.get("primary_ip4"):
            try:
                client.set_device_primary_ip4(device["id"], primary_ip_id)
            except client.NetBoxError as e:
                errors.append({"type": "primary-ip", "name": d["name"], "error": e.to_dict()})

    # ---- Cables ----
    for c in built["cables"]:
        a = iface_index.get((c["a_device"], c["a_interface"]))
        b = iface_index.get((c["b_device"], c["b_interface"]))
        label = f"{c['a_device']}:{c['a_interface']} <-> {c['b_device']}:{c['b_interface']}"
        if not a or not b:
            errors.append({"type": "cable", "name": label, "error": "interface not found"})
            continue
        if client.interface_has_cable(a) or client.interface_has_cable(b):
            skipped.append({"type": "cable", "name": label})
            continue
        try:
            client.create_cable(a, b, cable_type=c.get("type"))
            created.append({"type": "cable", "name": label})
        except client.NetBoxError as e:
            errors.append({"type": "cable", "name": label, "error": e.to_dict()})

    # ---- ASNs, VLANs, base config ----
    _push_asns(site_id, summary, base_tag_ids, created, skipped, errors)
    _push_vlans(site_id, base_tag_ids, created, errors)
    _push_config_context(plan, site_code, created, skipped, errors)
    _push_mlag_contexts(built, created, skipped, errors)

    return {
        "ok": not errors,
        "site": {"id": site_id, "name": site_code, "slug": site_slug,
                 "url": f"{config.netbox_url()}/dcim/sites/{site_id}/"},
        "summary": summary,
        "created": created, "skipped": skipped, "errors": errors,
    }


def preview_site_plan(plan):
    """Compute the fabric without writing anything - powers the wizard preview."""
    site_code = (plan.get("site_code") or plan.get("site") or "DC2").strip().upper()
    num_spines = int(plan.get("num_spines", 2))
    num_leaf_pairs = int(plan.get("num_leaf_pairs", 2))
    seq = int(plan.get("seq") or resolve_seq(site_code.lower()))
    built = fabric.build_fabric(site_code, num_spines, num_leaf_pairs, seq)
    return {"ok": True, "summary": built["summary"],
            "devices": [{"name": d["name"], "role": d["role"], "mgmt_ip": d["mgmt_ip"],
                         "custom_fields": d["custom_fields"],
                         "interfaces": [{"name": i["name"], "ip": i.get("ip"), "role": i.get("role")}
                                        for i in d["interfaces"]]}
                        for d in built["devices"]],
            "cables": built["cables"]}
