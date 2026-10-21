"""Stage 3 (optional): demo groups, object permissions and users.

Shows the L1-vs-engineer-vs-approver split without touching the built-in
``admin`` account or any permission that already exists.

Passwords are never stored in the data file. Set DEMO_USER_PASSWORD, otherwise a
lab-only default is used and reported.
"""

from __future__ import annotations

import os
from typing import Any

from .common import Stats, console, upsert

DEFAULT_PASSWORD = "PlantsDemo-2026!"  # noqa: S105 - lab-only default, overridable via env

# Everything the add-on creates, readable by every demo group.
VIEW_TYPES = [
    "dcim.site",
    "dcim.region",
    "dcim.location",
    "dcim.device",
    "dcim.devicerole",
    "dcim.devicetype",
    "dcim.manufacturer",
    "dcim.platform",
    "dcim.interface",
    "dcim.cable",
    "ipam.vlan",
    "ipam.prefix",
    "ipam.ipaddress",
    "ipam.role",
    "ipam.vrf",
    "tenancy.tenant",
    "tenancy.tenantgroup",
    "tenancy.contact",
    "tenancy.contactgroup",
    "tenancy.contactrole",
    "tenancy.contactassignment",
    "extras.tag",
    "extras.journalentry",
    "extras.configcontext",
]


class RbacBuilder:
    def __init__(self, nb: Any, plan: dict[str, Any]):
        self.nb = nb
        self.plan = plan["rbac"]
        self.stats = Stats()

    def run(self, reset_passwords: bool = False) -> Stats:
        console.print("\n[bold]Stage 3: Demo RBAC[/bold]")
        password = os.environ.get("DEMO_USER_PASSWORD") or DEFAULT_PASSWORD
        group_ids: dict[str, int] = {}

        for name, cfg in self.plan["groups"].items():
            group = upsert(self.nb.users.groups, {"name": name}, {"name": name}, self.stats, name)
            if group is None:
                continue
            group_ids[name] = group.id
            self._permission(f"{name}-view", cfg["description"], VIEW_TYPES, ["view"], group.id)
            if cfg.get("edit"):
                self._permission(
                    f"{name}-edit",
                    cfg["description"],
                    cfg["edit"],
                    cfg.get("edit_actions", ["add", "change"]),
                    group.id,
                )

        for username, cfg in self.plan["users"].items():
            self._user(username, cfg, group_ids.get(cfg["group"]), password, reset_passwords)

        using = (
            "DEMO_USER_PASSWORD" if os.environ.get("DEMO_USER_PASSWORD") else "default lab password"
        )
        console.print(f"  [dim]{self.stats.line()}  (users use the {using})[/dim]")
        return self.stats

    def _permission(
        self, name: str, description: str, types: list[str], actions: list[str], group_id: int
    ) -> None:
        upsert(
            self.nb.users.permissions,
            {"name": name},
            {
                "name": name,
                "description": description,
                "enabled": True,
                "object_types": types,
                "actions": actions,
                "groups": [group_id],
            },
            self.stats,
            f"permission {name}",
            quiet=True,
        )

    def _user(
        self,
        username: str,
        cfg: dict[str, Any],
        group_id: Any,
        password: str,
        reset_passwords: bool,
    ) -> None:
        data = {
            "username": username,
            "first_name": cfg.get("first_name", ""),
            "last_name": cfg.get("last_name", ""),
            "is_active": True,
            "groups": [group_id] if group_id else [],
        }
        try:
            existing = self.nb.users.users.get(username=username)
            if existing is None:
                self.nb.users.users.create({**data, "password": password})
                self.stats.created += 1
                console.print(f"  [green]+[/green] user {username}")
            elif reset_passwords:
                existing.update({**data, "password": password})
                self.stats.updated += 1
                console.print(f"  [blue]~[/blue] user {username} (password reset)")
            else:
                upsert(self.nb.users.users, {"username": username}, data, self.stats, username)
        except Exception as exc:  # noqa: BLE001
            self.stats.errors += 1
            console.print(f"  [red]✗[/red] user {username}: {exc}")
