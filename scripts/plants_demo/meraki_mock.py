"""A stand-in for the Cisco Meraki Dashboard API (v1), for demos without Meraki access.

It serves the read endpoints the sync uses, with the same shapes, credentials,
pagination and rate limiting as the real service:

    GET /api/v1/organizations
    GET /api/v1/organizations/{id}/networks
    GET /api/v1/organizations/{id}/devices
    GET /api/v1/organizations/{id}/devices/availabilities

the per-device reads the change checks and level-1 runbooks use:

    GET /api/v1/devices/{serial}/clients                  (an access point's wireless clients)
    GET /api/v1/devices/{serial}/appliance/dhcp/subnets   (an MX's DHCP scopes: used / free)
    GET /api/v1/networks/{id}/appliance/uplinks/usageHistory   (bytes per WAN uplink)

and the two write calls the change and level-1 demos use:

    GET /api/v1/devices/{serial}/switch/ports[/{portId}]
    PUT /api/v1/devices/{serial}/switch/ports/{portId}     (name, enabled, type, vlan, voiceVlan)
    PUT /api/v1/networks/{id}/firmwareUpgrades             (products.<type>.nextUpgrade.toVersion.shortName)

The mock applies a firmware upgrade immediately; the real service schedules it. A
switch's ports start out as access ports on ``DEFAULT_PORT_VLAN``.

The data is the Meraki-managed part of the sample organisation, as a dashboard
would report it, plus the differences listed under ``meraki_mock`` in
``netbox_data/plants_demo.yml`` (offline devices, old firmware, a changed IP,
devices NetBox has never heard of...) so the sync has something to find.

This is a MOCK. It has not been checked against a real Meraki organisation.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from .populate import IP_BASE, expand_site, meraki_serial, site_vlans

API_PREFIX = "/api/v1"
DEFAULT_KEY = "mock-meraki-api-key"
PRODUCT_TYPE = {"firewall": "appliance", "switch": "switch", "ap": "wireless"}
MODEL_PRODUCT = {"MX": "appliance", "MS": "switch", "MR": "wireless", "MV": "camera"}
# (smallest, largest) perPage the real endpoints accept
PER_PAGE_RANGE = {"networks": (3, 100000), "devices": (3, 5000), "availabilities": (3, 1000)}
DEFAULT_PORT_VLAN = 10  # a fresh mock switch port sits in the corporate VLAN
PORT_TYPES = ("access", "trunk")


def port_count(model: str) -> int:
    """Access ports on an MS switch model, from its name (MS120-24P -> 24)."""
    match = re.match(r"MS\d+-(\d+)", model or "")
    return int(match.group(1)) if match else 0


def switch_ports(world: dict[str, Any], serial: str) -> dict[str, dict[str, Any]] | None:
    """The ports of a switch, created on first use; None if it is not a known switch."""
    device = next((d for d in world["devices"] if d["serial"] == serial), None)
    if device is None or device["productType"] != "switch":
        return None
    store = world.setdefault("ports", {})
    if serial not in store:
        store[serial] = {
            str(number): {
                "portId": str(number),
                "name": "",
                "enabled": True,
                "type": "access",
                "vlan": DEFAULT_PORT_VLAN,
                "voiceVlan": None,
            }
            for number in range(1, port_count(device["model"]) + 1)
        }
    return store[serial]


def validate_port_update(body: Any) -> tuple[dict[str, Any], str | None]:
    """The port fields a PUT may change, or an explanation of what is wrong with it."""
    if not isinstance(body, dict):
        return {}, "Body must be a JSON object"
    update: dict[str, Any] = {}
    for key, value in body.items():
        if key == "name" and isinstance(value, str):
            update[key] = value
        elif key == "enabled" and isinstance(value, bool):
            update[key] = value
        elif key == "type" and value in PORT_TYPES:
            update[key] = value
        elif key in ("vlan", "voiceVlan") and (
            (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 4094)
            or (key == "voiceVlan" and value is None)
        ):
            update[key] = value
        elif key in ("name", "enabled", "type", "vlan", "voiceVlan"):
            return {}, f"Invalid value for '{key}'"
    return update, None


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()  # noqa: S324 - not security related


def network_id(site_code: str) -> str:
    """Deterministic, Meraki-looking network id for a site."""
    return "L_" + str(int(_digest(site_code)[:15], 16))[:18]


def _mac(name: str) -> str:
    digits = _digest(name)[:6]
    return "e0:55:3d:" + ":".join(digits[i : i + 2] for i in (0, 2, 4))


def build_world(plan: dict[str, Any]) -> dict[str, Any]:
    """Describe the dashboard: organization, networks, devices and availability."""
    cfg = plan.get("meraki_mock")
    if not cfg:
        raise ValueError("The data file has no 'meraki_mock' section.")
    sites = {site["code"]: site for site in plan["sites"]}
    current = cfg["firmware"]["current"]

    networks = [
        {
            "id": network_id(site["code"]),
            "organizationId": cfg["org"]["id"],
            "name": site["name"],
            "productTypes": ["appliance", "switch", "wireless"],
            "timeZone": site["time_zone"],
            "tags": [],
            "notes": "",
            "url": f"https://mock.meraki.example/{site['code']}/n/overview",
        }
        for site in plan["sites"]
    ]

    devices: list[dict[str, Any]] = []
    for site in plan["sites"]:
        counters: dict[tuple[int, str], int] = {}
        for dev in expand_site(plan, site):
            slot = counters.get((dev.vid, dev.kind), 0)  # same numbering the sample IPs use
            counters[(dev.vid, dev.kind)] = slot + 1
            dtype = plan["device_types"][dev.type_key]
            if dev.kind not in PRODUCT_TYPE or dtype["manufacturer"] != "cisco-meraki":
                continue
            product = PRODUCT_TYPE[dev.kind]
            devices.append(
                {
                    "name": dev.name,
                    "serial": meraki_serial(dev.name),
                    "mac": _mac(dev.name),
                    "model": dtype["model"],
                    "networkId": network_id(site["code"]),
                    "productType": product,
                    "lanIp": f"10.{site['index']}.{dev.vid}.{IP_BASE[dev.kind] + slot}",
                    "firmware": current[product],
                    "address": site["address"],
                    "tags": ["plants-demo", site["code"]],
                    "notes": "",
                }
            )

    by_name = {device["name"]: device for device in devices}
    scenarios = {
        "firmware.behind": cfg["firmware"]["behind"],
        "status": cfg.get("status", {}),
        "lan_ip": cfg.get("lan_ip", {}),
        "network": cfg.get("network", {}),
        "model": cfg.get("model", {}),
        "removed": dict.fromkeys(cfg.get("removed", [])),
    }
    for key, section in scenarios.items():  # a typo here would silently do nothing
        unknown = sorted(set(section) - set(by_name))
        if unknown:
            raise ValueError(f"meraki_mock.{key} names devices not in the sample data: {unknown}")
    removed = set(scenarios["removed"])

    for name, firmware in cfg["firmware"]["behind"].items():
        by_name[name]["firmware"] = firmware
    for name, address in cfg.get("lan_ip", {}).items():
        by_name[name]["lanIp"] = address
    for name, code in cfg.get("network", {}).items():
        by_name[name]["networkId"] = network_id(code)
    for name, model in cfg.get("model", {}).items():
        by_name[name]["model"] = model
    devices = [device for device in devices if device["name"] not in removed]

    for item in cfg.get("extra", []):
        if item["site"] not in sites:
            raise ValueError(f"meraki_mock.extra '{item['name']}': unknown site {item['site']}")
        product = item.get("product_type") or MODEL_PRODUCT[item["model"][:2]]
        devices.append(
            {
                "name": item["name"],
                "serial": item["serial"],
                "mac": _mac(item["name"]),
                "model": item["model"],
                "networkId": network_id(item["site"]),
                "productType": product,
                "lanIp": item.get("lan_ip"),
                "firmware": item.get("firmware") or current.get(product, ""),
                "address": sites[item["site"]]["address"],
                "tags": ["plants-demo", item["site"]],
                "notes": "",
            }
        )

    dhcp_cfg = cfg.get("dhcp") or {}
    pool = int(dhcp_cfg.get("pool", 152))
    used_cfg = dhcp_cfg.get("used", {})
    unknown = sorted(set(used_cfg) - set(sites))
    if unknown:
        raise ValueError(f"meraki_mock.dhcp.used names sites not in the sample data: {unknown}")
    dhcp: dict[str, list[dict[str, Any]]] = {}
    for site in plan["sites"]:
        if site["code"] in dhcp_cfg.get("relay", []):
            continue
        served = [v["vid"] for v in site_vlans(plan, site) if v["vid"] in dhcp_cfg.get("vlans", [])]
        scopes = []
        for vid in served:
            used = used_cfg.get(site["code"], {}).get(vid)
            if used is None:
                used = pool * (20 + int(_digest(f"{site['code']}/{vid}")[:4], 16) % 41) // 100
            used = max(0, min(int(used), pool))
            scopes.append(
                {
                    "subnet": f"10.{site['index']}.{vid}.0/24",
                    "vlanId": vid,
                    "usedCount": used,
                    "freeCount": pool - used,
                }
            )
        dhcp[network_id(site["code"])] = scopes

    client_cfg = cfg.get("clients", {})
    unknown = sorted(set(client_cfg) - set(by_name))
    if unknown:
        raise ValueError(f"meraki_mock.clients names devices not in the sample data: {unknown}")
    clients = {
        d["name"]: int(client_cfg.get(d["name"], 5 + int(_digest(d["name"])[:4], 16) % 20))
        for d in devices
        if d["productType"] == "wireless"
    }

    uplink_cfg = cfg.get("uplinks", {})
    unknown = sorted(set(uplink_cfg) - set(sites))
    if unknown:
        raise ValueError(f"meraki_mock.uplinks names sites not in the sample data: {unknown}")
    uplinks: dict[str, dict[str, float]] = {}
    for circuit in plan.get("circuits", []):
        code = circuit["site"]
        if circuit.get("role") == "backup":
            percent = 2.0  # idle unless the primary fails
        else:
            percent = float(uplink_cfg.get(code, 15 + int(_digest(f"{code}/wan")[:4], 16) % 41))
        uplinks.setdefault(network_id(code), {})[circuit["port"].lower()] = (
            int(circuit["commit_mbps"]) * percent / 100
        )

    overrides = cfg.get("status", {})
    availability = [
        {
            "mac": device["mac"],
            "name": device["name"],
            "network": {"id": device["networkId"]},
            "productType": device["productType"],
            "serial": device["serial"],
            "status": overrides.get(device["name"], "online"),
            "tags": device["tags"],
        }
        for device in devices
    ]
    return {
        "org": {
            "id": cfg["org"]["id"],
            "name": cfg["org"]["name"],
            "url": "https://mock.meraki.example/o/overview",
        },
        "networks": networks,
        "devices": devices,
        "availabilities": availability,
        "dhcp": dhcp,
        "clients": clients,
        "uplinks": uplinks,
    }


def uplink_usage(world: dict[str, Any], network: str, resolution: int = 300) -> list[dict[str, Any]] | None:
    """Bytes sent and received on each WAN uplink over the last two intervals (None: unknown network)."""
    if not any(n["id"] == network for n in world["networks"]):
        return None
    rates = world.get("uplinks", {}).get(network, {})
    end = int(time.time()) // resolution * resolution
    history = []
    for step in (2, 1):
        start = end - step * resolution
        history.append(
            {
                "startTime": _iso(start),
                "endTime": _iso(start + resolution),
                "byInterface": [
                    {
                        "interface": name,
                        "received": int(mbps * 1_000_000 / 8 * resolution),
                        "sent": int(mbps * 0.3 * 1_000_000 / 8 * resolution),
                    }
                    for name, mbps in sorted(rates.items())
                ],
            }
        )
    return history


def _iso(epoch: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def device_clients(world: dict[str, Any], serial: str) -> list[dict[str, Any]] | None:
    """The clients an access point reports; none while it is not online. None: not an AP."""
    device = next((d for d in world["devices"] if d["serial"] == serial), None)
    if device is None or device["productType"] != "wireless":
        return None
    status = next((a["status"] for a in world["availabilities"] if a["serial"] == serial), "offline")
    if status != "online":
        return []
    prefix = ".".join(device["lanIp"].split(".")[:2]) if device.get("lanIp") else "10.0"
    return [
        {
            "id": f"k{_digest(device['name'] + str(n))[:6]}",
            "mac": _mac(f"{device['name']}/client{n}"),
            "description": f"client-{n:02d}",
            "ip": f"{prefix}.10.{30 + n}",
            "vlan": 10,
            "usage": {"sent": 1000 + 37 * n, "recv": 4000 + 91 * n},
        }
        for n in range(1, world.get("clients", {}).get(device["name"], 0) + 1)
    ]


def dhcp_subnets(world: dict[str, Any], serial: str) -> list[dict[str, Any]] | None:
    """The DHCP scopes an MX serves (empty where it only relays). None: not an MX."""
    device = next((d for d in world["devices"] if d["serial"] == serial), None)
    if device is None or device["productType"] != "appliance":
        return None
    return world.get("dhcp", {}).get(device["networkId"], [])


class MockMeraki(ThreadingHTTPServer):
    """The HTTP server. ``page_cap`` limits items per page whatever ``perPage`` says."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        world: dict[str, Any],
        api_key: str = DEFAULT_KEY,
        page_cap: int = 30,
        rate_limit_every: int = 0,
    ):
        super().__init__(address, _Handler)
        self.world = world
        self.api_key = api_key
        self.page_cap = page_cap
        self.rate_limit_every = rate_limit_every
        self.requests = 0
        self.rate_limited = 0
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}{API_PREFIX}"

    def count_request(self) -> bool:
        """Count a request; True if this one should be answered with HTTP 429."""
        with self._lock:
            self.requests += 1
            limited = bool(self.rate_limit_every) and self.requests % self.rate_limit_every == 0
            self.rate_limited += int(limited)
            return limited


class _Handler(BaseHTTPRequestHandler):
    server: MockMeraki
    server_version = "MockMeraki/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass  # the sync reports its own API usage

    def _send(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _authorised(self) -> bool:
        bearer = self.headers.get("Authorization", "")
        key = self.headers.get("X-Cisco-Meraki-API-Key") or (
            bearer[7:] if bearer.lower().startswith("bearer ") else ""
        )
        return key == self.server.api_key

    def do_GET(self) -> None:
        if not self._authorised():
            self._send(401, {"errors": ["Invalid API key"]})
            return
        if self.server.count_request():
            self._send(429, {"errors": ["Rate limit exceeded"]}, {"Retry-After": "1"})
            return

        url = urlparse(self.path)
        query = parse_qs(url.query)
        parts = [part for part in url.path.split("/") if part]
        world = self.server.world
        if parts[:2] != ["api", "v1"]:
            self._send(404, {"errors": ["Not found"]})
            return
        route = parts[2:]
        if route == ["organizations"]:
            self._send(200, [world["org"]])
        elif route[:1] == ["devices"] and len(route) in (4, 5) and route[2:4] == ["switch", "ports"]:
            with self.server._lock:
                ports = switch_ports(world, route[1])
                if ports is None:
                    self._send(404, {"errors": ["Device not found, or not a switch"]})
                elif len(route) == 4:
                    self._send(200, list(ports.values()))
                elif route[4] in ports:
                    self._send(200, ports[route[4]])
                else:
                    self._send(404, {"errors": ["Port not found"]})
        elif route[:1] == ["devices"] and len(route) == 3 and route[2] == "clients":
            with self.server._lock:
                found = device_clients(world, route[1])
            if found is None:
                self._send(404, {"errors": ["Device not found, or not an access point"]})
            else:
                self._send(200, found)
        elif route[:1] == ["devices"] and route[2:] == ["appliance", "dhcp", "subnets"]:
            with self.server._lock:
                found = dhcp_subnets(world, route[1])
            if found is None:
                self._send(404, {"errors": ["Device not found, or not an appliance"]})
            else:
                self._send(200, found)
        elif route[:1] == ["networks"] and route[2:] == ["appliance", "uplinks", "usageHistory"]:
            try:
                resolution = int(query.get("resolution", [300])[0])
            except ValueError:
                resolution = 0
            if resolution not in (60, 300, 600, 1800, 3600, 86400):
                self._send(400, {"errors": ["resolution must be one of 60, 300, 600, 1800, 3600, 86400"]})
                return
            with self.server._lock:
                found = uplink_usage(world, route[1], resolution)
            if found is None:
                self._send(404, {"errors": ["Network not found"]})
            else:
                self._send(200, found)
        elif len(route) >= 3 and route[0] == "organizations":
            if route[1] != world["org"]["id"]:
                self._send(404, {"errors": ["Organization not found"]})
                return
            tail = "/".join(route[2:])
            collections = {
                "networks": ("networks", "id", "networks"),
                "devices": ("devices", "serial", "devices"),
                "devices/availabilities": ("availabilities", "serial", "availabilities"),
            }
            if tail not in collections:
                self._send(404, {"errors": ["Not found"]})
                return
            world_key, id_field, limits = collections[tail]
            self._page(world[world_key], id_field, limits, url.path, query)
        else:
            self._send(404, {"errors": ["Not found"]})

    def _body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return None

    def do_PUT(self) -> None:
        if not self._authorised():
            self._send(401, {"errors": ["Invalid API key"]})
            return
        if self.server.count_request():
            self._send(429, {"errors": ["Rate limit exceeded"]}, {"Retry-After": "1"})
            return
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        route = parts[2:] if parts[:2] == ["api", "v1"] else []
        body = self._body()
        world = self.server.world

        if len(route) == 5 and route[0] == "devices" and route[2:4] == ["switch", "ports"]:
            update, problem = validate_port_update(body)
            with self.server._lock:
                ports = switch_ports(world, route[1])
                if ports is None:
                    self._send(404, {"errors": ["Device not found, or not a switch"]})
                elif route[4] not in ports:
                    self._send(404, {"errors": ["Port not found"]})
                elif problem:
                    self._send(400, {"errors": [problem]})
                else:
                    ports[route[4]].update(update)
                    self._send(200, ports[route[4]])
        elif len(route) == 3 and route[0] == "networks" and route[2] == "firmwareUpgrades":
            self._firmware_upgrade(route[1], body)
        else:
            self._send(404, {"errors": ["Not found"]})

    def _firmware_upgrade(self, network: str, body: Any) -> None:
        products = body.get("products") if isinstance(body, dict) else None
        if not isinstance(products, dict) or not products:
            self._send(400, {"errors": ["'products' is required"]})
            return
        wanted: dict[str, str] = {}
        for product, detail in products.items():
            version = (
                ((detail or {}).get("nextUpgrade") or {}).get("toVersion", {}).get("shortName")
                if isinstance(detail, dict)
                else None
            )
            if product not in ("appliance", "switch", "wireless") or not version:
                self._send(
                    400,
                    {"errors": [f"'{product}' needs nextUpgrade.toVersion.shortName"]},
                )
                return
            wanted[product] = version
        with self.server._lock:
            world = self.server.world
            if not any(n["id"] == network for n in world["networks"]):
                self._send(404, {"errors": ["Network not found"]})
                return
            for device in world["devices"]:
                if device["networkId"] == network and device["productType"] in wanted:
                    device["firmware"] = wanted[device["productType"]]
        reply = {
            product: {"nextUpgrade": {"time": "immediately (mock)", "toVersion": {"shortName": v}}}
            for product, v in wanted.items()
        }
        self._send(200, {"products": reply})

    def _page(
        self,
        items: list[dict[str, Any]],
        id_field: str,
        limits: str,
        path: str,
        query: dict[str, list[str]],
    ) -> None:
        low, high = PER_PAGE_RANGE[limits]
        try:
            requested = int(query.get("perPage", [1000])[0])
        except ValueError:
            requested = -1
        if not low <= requested <= high:
            self._send(400, {"errors": [f"perPage must be between {low} and {high}"]})
            return

        start = 0
        if "startingAfter" in query:
            marker = query["startingAfter"][0]
            position = next((i for i, item in enumerate(items) if item[id_field] == marker), None)
            if position is None:
                self._send(400, {"errors": ["startingAfter does not match any item"]})
                return
            start = position + 1
        size = min(requested, self.server.page_cap)
        page = items[start : start + size]

        host = self.headers.get("Host") or "{}:{}".format(*self.server.server_address[:2])
        base = f"http://{host}{path}"
        carried = {"perPage": requested}
        links = [f"<{base}?{urlencode(carried)}>; rel=first"]
        if start + size < len(items):
            nxt = urlencode({**carried, "startingAfter": page[-1][id_field]})
            links.append(f"<{base}?{nxt}>; rel=next")
        self._send(200, page, {"Link": ", ".join(links)})


def start(
    world: dict[str, Any],
    host: str = "127.0.0.1",
    port: int = 0,
    api_key: str = DEFAULT_KEY,
    page_cap: int = 30,
    rate_limit_every: int = 0,
) -> MockMeraki:
    """Run the mock in a background thread (port 0 picks a free one)."""
    server = MockMeraki((host, port), world, api_key, page_cap, rate_limit_every)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def stop(server: MockMeraki) -> None:
    server.shutdown()
    server.server_close()
