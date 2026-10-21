"""A small, read-only client for the Cisco Meraki Dashboard API (v1).

It talks to whatever ``base_url`` it is given: the bundled mock, or the real
service at https://api.meraki.com/api/v1. Nothing here depends on NetBox.

Handled the way the real API requires:
  * credentials: ``Authorization: Bearer <key>``;
  * pagination: follow the ``Link: <...>; rel=next`` header until it stops;
  * rate limiting: on HTTP 429, wait for ``Retry-After`` and try again.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import requests

DEFAULT_BASE_URL = "https://api.meraki.com/api/v1"


class MerakiError(Exception):
    """Anything that stops us reading from the dashboard, with a plain explanation."""


class MerakiClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        per_page: int = 1000,
        max_retries: int = 4,
        timeout: float = 30,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.per_page = per_page
        self.max_retries = max_retries
        self.timeout = timeout
        self.sleep = sleep
        self.calls = 0
        self.retries = 0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": "netbox-arista-lab-plants-demo/1.0",
            }
        )

    # ------------------------------------------------------------------ http
    def _get(self, url: str, params: dict[str, Any] | None) -> requests.Response:
        return self._request("GET", url, params=params)

    def _request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> requests.Response:
        for attempt in range(self.max_retries + 1):
            self.calls += 1
            try:
                if method == "GET":
                    response = self.session.get(url, params=params, timeout=self.timeout)
                else:
                    response = self.session.request(
                        method, url, params=params, json=body, timeout=self.timeout
                    )
            except requests.RequestException as exc:
                raise MerakiError(f"Cannot reach the Meraki API at {url}: {exc}") from exc
            if response.status_code == 429 and attempt < self.max_retries:
                self.retries += 1
                try:
                    wait = min(float(response.headers.get("Retry-After", 1)), 30.0)
                except ValueError:
                    wait = 1.0
                self.sleep(wait)
                continue
            break

        if response.status_code in (401, 403):
            raise MerakiError(
                f"The Meraki API rejected the API key (HTTP {response.status_code}). "
                "Check the key and that it can read this organization."
            )
        if response.status_code == 429:
            raise MerakiError(f"Still rate limited after {self.max_retries} retries (HTTP 429).")
        if response.status_code >= 400:
            try:
                detail = "; ".join(response.json().get("errors", []))
            except (ValueError, AttributeError):
                detail = response.text[:200]
            raise MerakiError(f"HTTP {response.status_code} from {url}: {detail or 'no detail'}")
        return response

    def get_all(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """GET a collection, following every page."""
        url: str | None = self.base_url + path
        query: dict[str, Any] | None = {"perPage": self.per_page, **(params or {})}
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        while url:
            if url in seen:
                raise MerakiError(f"Pagination loop detected at {url}")
            seen.add(url)
            response = self._get(url, query)
            data = response.json()
            if not isinstance(data, list):
                raise MerakiError(f"Expected a list from {url}, got {type(data).__name__}")
            items.extend(data)
            url = response.links.get("next", {}).get("url")
            query = None  # the next link already carries every parameter
        return items

    # ------------------------------------------------------------- endpoints
    def organizations(self) -> list[dict[str, Any]]:
        return self.get_all("/organizations")

    def networks(self, org_id: str) -> list[dict[str, Any]]:
        return self.get_all(f"/organizations/{org_id}/networks")

    def devices(self, org_id: str) -> list[dict[str, Any]]:
        return self.get_all(f"/organizations/{org_id}/devices")

    def availabilities(self, org_id: str) -> list[dict[str, Any]]:
        """Online / alerting / offline / dormant per device.

        (The older ``/devices/statuses`` endpoint is deprecated in the current API.)
        """
        page = min(self.per_page, 1000)  # this endpoint accepts at most 1000 per page
        return self.get_all(f"/organizations/{org_id}/devices/availabilities", {"perPage": page})

    def device_clients(self, serial: str, timespan: int = 900) -> list[dict[str, Any]]:
        """Clients seen on a device in the last ``timespan`` seconds."""
        return self._request(
            "GET", f"{self.base_url}/devices/{serial}/clients", params={"timespan": timespan}
        ).json()

    def dhcp_subnets(self, serial: str) -> list[dict[str, Any]]:
        """An MX's DHCP scopes, each with usedCount and freeCount."""
        return self._request("GET", f"{self.base_url}/devices/{serial}/appliance/dhcp/subnets").json()

    def uplink_usage(self, network_id: str, timespan: int = 600, resolution: int = 300) -> list[dict[str, Any]]:
        """Bytes sent and received per WAN uplink, one entry per ``resolution`` seconds."""
        return self._request(
            "GET",
            f"{self.base_url}/networks/{network_id}/appliance/uplinks/usageHistory",
            params={"timespan": timespan, "resolution": resolution},
        ).json()

    # ---------------------------------------------------------- write calls
    # Used by the change and level-1 demos only. The sync stays read-only.
    def _write(self, path: str, body: dict[str, Any]) -> Any:
        return self._request("PUT", self.base_url + path, body=body).json()

    def switch_port(self, serial: str, port_id: str) -> dict[str, Any]:
        return self._request("GET", f"{self.base_url}/devices/{serial}/switch/ports/{port_id}").json()

    def update_switch_port(self, serial: str, port_id: str, **fields: Any) -> dict[str, Any]:
        return self._write(f"/devices/{serial}/switch/ports/{port_id}", fields)

    def upgrade_firmware(self, network_id: str, product: str, version: str) -> dict[str, Any]:
        """Ask for ``version`` on every ``product`` device in the network."""
        body = {"products": {product: {"nextUpgrade": {"toVersion": {"shortName": version}}}}}
        return self._write(f"/networks/{network_id}/firmwareUpgrades", body)
