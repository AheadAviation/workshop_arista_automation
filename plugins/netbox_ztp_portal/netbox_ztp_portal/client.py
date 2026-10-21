"""Minimal NetBox REST API client (stdlib only).

Ported from the Bloom prototype's server/netbox.py. Framework-agnostic: talks
to NetBox purely over its REST API with plain functions and dicts. Inside the
plugin it targets NetBox's own API (loopback to self) using the token from
plugin settings.

Adapted for the workshop's NetBox v4.1:
  * Prefix scoping uses the flat `site` field (4.1) rather than the generic
    scope_type/scope_id introduced in 4.2+.
Added for the Arista fabric push (not needed by the Cisco prototype, which relied
on device-type interface templates):
  * explicit interface creation, custom_fields on devices, primary-IP assignment.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import conf as config


class NetBoxError(Exception):
    def __init__(self, status, message, detail=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail

    def to_dict(self):
        return {"error": self.message, "status": self.status, "detail": self.detail}


def _request(method, path, params=None, body=None):
    if not config.netbox_configured():
        raise NetBoxError(500, "NetBox is not configured - set netbox_url/netbox_token in PLUGINS_CONFIG")

    url = f"{config.netbox_url()}/api/{path.lstrip('/')}"
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if query:
            url = f"{url}?{query}"

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Token {config.netbox_token()}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            detail = json.loads(raw) if raw else None
        except ValueError:
            detail = raw.decode("utf-8", errors="replace")
        raise NetBoxError(e.code, f"NetBox {method} {path} failed ({e.code})", detail)
    except urllib.error.URLError as e:
        raise NetBoxError(502, f"Could not reach NetBox at {config.netbox_url()}: {e.reason}")


def get(path, params=None):
    return _request("GET", path, params=params)


def post(path, body):
    return _request("POST", path, body=body)


def patch(path, body):
    return _request("PATCH", path, body=body)


def delete(path):
    return _request("DELETE", path)


def get_status():
    return get("status/")


def list_all(path, params=None):
    """Follow NetBox's paginated `next` links and return every result."""
    results = []
    next_path, next_params = path, dict(params or {})
    next_params.setdefault("limit", 50)
    while next_path:
        data = get(next_path, params=next_params)
        results.extend(data.get("results", []))
        next_url = data.get("next")
        if not next_url:
            break
        next_path = next_url.split("/api/", 1)[-1]
        next_params = None
    return results


def _slugify(value):
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-+", "-", value).strip("-") or "item"


# ---- Tags ------------------------------------------------------------------

def get_or_create_tag(name):
    slug = _slugify(name)
    existing = get("extras/tags/", params={"slug": slug})
    if existing and existing.get("count"):
        return existing["results"][0]
    return post("extras/tags/", {"name": name, "slug": slug})


def list_by_tag(path, tag_slug):
    # NetBox's `tag` filter 400s if the tag doesn't exist yet - check first so
    # callers get [] instead of a 400.
    tag = get("extras/tags/", params={"slug": tag_slug})
    if not tag or not tag.get("count"):
        return []
    return list_all(path, params={"tag": tag_slug})


# ---- Custom fields ---------------------------------------------------------

def get_or_create_custom_field(name, type="text", label=None, description="",
                               object_types=None, weight=100):
    """Ensure a device custom field exists (self-sufficiency: the portal can
    stand up a fabric even on a NetBox that never ran the DC1 bootstrap). If it
    already exists (e.g. created by scripts/bootstrap_netbox.py) it is reused
    as-is, so definitions never conflict."""
    existing = get("extras/custom-fields/", params={"name": name})
    if existing and existing.get("count"):
        return existing["results"][0]
    return post("extras/custom-fields/", {
        "name": name, "type": type, "label": label or name,
        "description": description, "object_types": object_types or ["dcim.device"],
        "required": False, "weight": weight,
    })


# ---- Sites -----------------------------------------------------------------

def find_site_by_slug(slug):
    data = get("dcim/sites/", params={"slug": slug})
    if data and data.get("count"):
        return data["results"][0]
    return None


def create_site(name, slug, tag_ids, description=""):
    return post("dcim/sites/", {
        "name": name, "slug": slug, "status": "active",
        "tags": tag_ids, "description": description,
    })


def list_sites_by_tag(tag_slug):
    return list_by_tag("dcim/sites/", tag_slug)


def set_site_asns(site_id, asn_ids):
    return patch(f"dcim/sites/{site_id}/", {"asns": asn_ids})


def get_or_create_region(name):
    slug = _slugify(name)
    existing = get("dcim/regions/", params={"slug": slug})
    if existing and existing.get("count"):
        return existing["results"][0]
    return post("dcim/regions/", {"name": name, "slug": slug})


def set_site_region(site_id, region_id):
    return patch(f"dcim/sites/{site_id}/", {"region": region_id})


# ---- Racks -----------------------------------------------------------------

def find_rack_by_name(site_id, name):
    data = get("dcim/racks/", params={"site_id": site_id, "name": name})
    if data and data.get("count"):
        return data["results"][0]
    return None


def create_rack(site_id, name, location_id=None, tag_ids=None, u_height=42):
    body = {"site": site_id, "name": name, "status": "active", "u_height": u_height}
    if location_id:
        body["location"] = location_id
    if tag_ids:
        body["tags"] = tag_ids
    return post("dcim/racks/", body)


# ---- Manufacturer / DeviceType / Role --------------------------------------

def get_or_create_manufacturer(name):
    slug = _slugify(name)
    existing = get("dcim/manufacturers/", params={"slug": slug})
    if existing and existing.get("count"):
        return existing["results"][0]
    return post("dcim/manufacturers/", {"name": name, "slug": slug})


def find_device_type_by_slug(slug):
    data = get("dcim/device-types/", params={"slug": slug})
    if data and data.get("count"):
        return data["results"][0]
    return None


def get_or_create_device_type(model, manufacturer_id, slug=None):
    slug = slug or _slugify(model)
    existing = find_device_type_by_slug(slug)
    if existing:
        return existing
    return post("dcim/device-types/", {"model": model, "slug": slug, "manufacturer": manufacturer_id})


def get_or_create_device_role(name, color="2196f3"):
    slug = _slugify(name)
    existing = get("dcim/device-roles/", params={"slug": slug})
    if existing and existing.get("count"):
        return existing["results"][0]
    return post("dcim/device-roles/", {"name": name, "slug": slug, "color": color})


# ---- Devices ---------------------------------------------------------------

def find_device_by_name(name):
    data = get("dcim/devices/", params={"name": name})
    if data and data.get("count"):
        return data["results"][0]
    return None


def find_device_by_serial(serial):
    data = get("dcim/devices/", params={"serial": serial})
    if data and data.get("count"):
        return data["results"][0]
    return None


def create_device(name, device_type_id, role_id, site_id, rack_id=None, serial=None,
                   status="active", tag_ids=None, face="front", position=None,
                   custom_fields=None):
    body = {
        "name": name, "device_type": device_type_id, "role": role_id,
        "site": site_id, "status": status,
    }
    if rack_id:
        body["rack"] = rack_id
        body["face"] = face
        if position is not None:
            body["position"] = position
    if serial:
        body["serial"] = serial
    if tag_ids:
        body["tags"] = tag_ids
    if custom_fields:
        body["custom_fields"] = custom_fields
    return post("dcim/devices/", body)


def set_device_primary_ip4(device_id, ip_id):
    return patch(f"dcim/devices/{device_id}/", {"primary_ip4": ip_id})


# ---- Interfaces ------------------------------------------------------------

def find_interface(device_id, name):
    data = get("dcim/interfaces/", params={"device_id": device_id, "name": name})
    if data and data.get("count"):
        return data["results"][0]
    return None


def get_or_create_interface(device_id, name, type="1000base-t", description="", enabled=True):
    existing = find_interface(device_id, name)
    if existing:
        return existing
    return post("dcim/interfaces/", {
        "device": device_id, "name": name, "type": type,
        "description": description, "enabled": enabled,
    })


def update_interface(interface_id, **fields):
    return patch(f"dcim/interfaces/{interface_id}/", fields)


def interface_has_cable(interface_id):
    data = get(f"dcim/interfaces/{interface_id}/")
    return bool(data and data.get("cable"))


# ---- Cables ----------------------------------------------------------------

def create_cable(a_iface_id, b_iface_id, status="connected", cable_type=None):
    body = {
        "a_terminations": [{"object_type": "dcim.interface", "object_id": a_iface_id}],
        "b_terminations": [{"object_type": "dcim.interface", "object_id": b_iface_id}],
        "status": status,
    }
    if cable_type:
        body["type"] = cable_type
    return post("dcim/cables/", body)


# ---- IPAM ------------------------------------------------------------------

def find_prefix(prefix):
    data = get("ipam/prefixes/", params={"prefix": prefix})
    if data and data.get("count"):
        return data["results"][0]
    return None


def create_prefix(prefix, site_id=None, description="", tag_ids=None, status="active"):
    """NetBox v4.1 scopes a prefix via the flat `site` field (the generic
    scope_type/scope_id form arrived in 4.2)."""
    body = {"prefix": prefix, "status": status, "description": description}
    if site_id:
        body["site"] = site_id
    if tag_ids:
        body["tags"] = tag_ids
    return post("ipam/prefixes/", body)


def find_ip_address(address):
    data = get("ipam/ip-addresses/", params={"address": address})
    if data and data.get("count"):
        return data["results"][0]
    return None


def assign_ip_to_interface(address, interface_id, tag_ids=None, description=""):
    """Create the IP (or reuse it) and bind it to the given interface."""
    existing = find_ip_address(address)
    if existing:
        if existing.get("assigned_object_id") != interface_id:
            existing = patch(f"ipam/ip-addresses/{existing['id']}/", {
                "assigned_object_type": "dcim.interface", "assigned_object_id": interface_id,
            })
        return existing
    body = {
        "address": address, "status": "active", "description": description,
        "assigned_object_type": "dcim.interface", "assigned_object_id": interface_id,
    }
    if tag_ids:
        body["tags"] = tag_ids
    return post("ipam/ip-addresses/", body)


# ---- ASN -------------------------------------------------------------------

def get_or_create_rir(name, is_private=False):
    slug = _slugify(name)
    existing = get("ipam/rirs/", params={"slug": slug})
    if existing and existing.get("count"):
        return existing["results"][0]
    return post("ipam/rirs/", {"name": name, "slug": slug, "is_private": is_private})


def find_asn(asn_number):
    data = get("ipam/asns/", params={"asn": asn_number})
    if data and data.get("count"):
        return data["results"][0]
    return None


def create_asn(asn_number, description="", tag_ids=None, rir_id=None):
    body = {"asn": asn_number, "description": description}
    if rir_id:
        body["rir"] = rir_id
    if tag_ids:
        body["tags"] = tag_ids
    return post("ipam/asns/", body)


# ---- VLANs -----------------------------------------------------------------

def get_or_create_vlan(vid, name, site_id=None, tag_ids=None):
    params = {"vid": vid}
    if site_id:
        params["site_id"] = site_id
    data = get("ipam/vlans/", params=params)
    if data and data.get("count"):
        return data["results"][0]
    body = {"vid": vid, "name": name, "status": "active"}
    if site_id:
        body["site"] = site_id
    if tag_ids:
        body["tags"] = tag_ids
    return post("ipam/vlans/", body)


# ---- Config contexts -------------------------------------------------------

def find_config_context_by_name(name):
    data = get("extras/config-contexts/", params={"name": name})
    if data and data.get("count"):
        return data["results"][0]
    return None


def get_or_create_config_context(name, data_payload, tag_slugs=None, description="", weight=1000):
    """ConfigContext's scoping `tags` field takes tag **slugs** (not IDs). A
    context applies to a device only if the device carries all the given tags,
    so pass a precise scope (e.g. a per-pair MLAG tag)."""
    existing = find_config_context_by_name(name)
    if existing:
        return patch(f"extras/config-contexts/{existing['id']}/", {"data": data_payload})
    body = {"name": name, "data": data_payload, "description": description, "weight": weight}
    if tag_slugs:
        body["tags"] = list(tag_slugs)
    return post("extras/config-contexts/", body)
