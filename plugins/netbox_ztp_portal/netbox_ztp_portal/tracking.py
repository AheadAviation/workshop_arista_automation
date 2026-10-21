"""Fleet tracking: bring-up progress across every portal-built fabric.

Reads real NetBox state (sites + devices tagged by the portal) and reports,
per fabric, how many spine/leaf devices exist and how far along each is
(bound -> booting/ZTP -> config pushed -> active), inferred from device status
and whether a primary management IP has been assigned.
"""
from . import client
from . import conf as config


def _stage(device):
    status = (device.get("status") or {})
    status_val = status.get("value") if isinstance(status, dict) else status
    has_mgmt = bool(device.get("primary_ip4"))
    if status_val == "active" and has_mgmt:
        return "active"
    if has_mgmt:
        return "config-pushed"
    if status_val in ("staged", "planned"):
        return "booting"
    return "bound"


def build_tracking():
    sites = client.list_sites_by_tag(config.self_portal_tag())
    devices = client.list_by_tag("dcim/devices/", config.self_portal_tag())

    by_site = {}
    for d in devices:
        site = d.get("site") or {}
        sid = site.get("id")
        if sid is None:
            continue
        role = (d.get("role") or {}).get("slug", "unknown")
        entry = by_site.setdefault(sid, {"roles": {}, "stages": {}})
        entry["roles"][role] = entry["roles"].get(role, 0) + 1
        stage = _stage(d)
        entry["stages"][stage] = entry["stages"].get(stage, 0) + 1

    fabrics = []
    for s in sites:
        agg = by_site.get(s["id"], {"roles": {}, "stages": {}})
        total = sum(agg["roles"].values())
        active = agg["stages"].get("active", 0)
        fabrics.append({
            "id": s["id"], "name": s["name"], "slug": s["slug"],
            "url": f"{config.netbox_url()}/dcim/sites/{s['id']}/",
            "description": s.get("description", ""),
            "roles": agg["roles"], "stages": agg["stages"],
            "device_count": total, "active_count": active,
            "percent": round(100 * active / total) if total else 0,
        })
    fabrics.sort(key=lambda f: f["name"])
    return {"fabrics": fabrics, "site_count": len(fabrics)}
