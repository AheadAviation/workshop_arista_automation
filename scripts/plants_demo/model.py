"""Stage 1: create the reference data the add-on needs.

Only creates NEW objects (manufacturers, device types, roles, custom fields...).
Nothing from the core Arista lab is read for update or modified here.
"""

from __future__ import annotations

from typing import Any

import pynetbox

from .common import Stats, console, expand_interfaces, upsert


class ModelBuilder:
    """Builds the reference data layer of the add-on."""

    def __init__(self, nb: Any, plan: dict[str, Any]):
        self.nb = nb
        self.plan = plan
        self.stats = Stats()
        self.ids: dict[str, dict[str, int]] = {}

    def _remember(self, kind: str, key: str, record: Any) -> None:
        if record is not None:
            self.ids.setdefault(kind, {})[key] = record.id

    def run(self) -> Stats:
        console.print("\n[bold]Stage 1: Data model[/bold]")
        self._tags()
        self._manufacturers()
        self._platforms()
        self._device_roles()
        self._prefix_roles()
        self._custom_fields()
        self._regions()
        self._tenants()
        self._contact_model()
        self._device_types()
        self._circuit_types()
        console.print(f"  [dim]{self.stats.line()}[/dim]")
        return self.stats

    def _circuit_types(self) -> None:
        console.print("[cyan]Circuit types[/cyan]")
        for item in self.plan.get("circuit_types", []):
            upsert(self.nb.circuits.circuit_types, {"slug": item["slug"]}, item, self.stats, item["name"])

    def ensure_extras(self) -> Stats:
        """Create just the tags and custom fields (cheap, idempotent).

        Lets a later feature add a field to an NetBox that was set up before the
        feature existed, without re-running the whole model stage.
        """
        self._tags()
        self._custom_fields()
        return self.stats

    # ------------------------------------------------------------------ simple
    def _tags(self) -> None:
        console.print("[cyan]Tags[/cyan]")
        for tag in self.plan.get("tags", []):
            rec = upsert(self.nb.extras.tags, {"slug": tag["slug"]}, tag, self.stats, tag["slug"])
            self._remember("tag", tag["slug"], rec)

    def _manufacturers(self) -> None:
        console.print("[cyan]Manufacturers[/cyan]")
        for item in self.plan.get("manufacturers", []):
            rec = upsert(
                self.nb.dcim.manufacturers, {"slug": item["slug"]}, item, self.stats, item["name"]
            )
            self._remember("manufacturer", item["slug"], rec)

    def _platforms(self) -> None:
        console.print("[cyan]Platforms[/cyan]")
        for item in self.plan.get("platforms", []):
            data = {"name": item["name"], "slug": item["slug"]}
            if item.get("manufacturer"):
                data["manufacturer"] = self.ids["manufacturer"][item["manufacturer"]]
            rec = upsert(
                self.nb.dcim.platforms, {"slug": item["slug"]}, data, self.stats, item["name"]
            )
            self._remember("platform", item["slug"], rec)

    def _device_roles(self) -> None:
        console.print("[cyan]Device roles[/cyan]")
        for item in self.plan.get("device_roles", []):
            rec = upsert(
                self.nb.dcim.device_roles, {"slug": item["slug"]}, item, self.stats, item["name"]
            )
            self._remember("role", item["slug"], rec)

    def _prefix_roles(self) -> None:
        console.print("[cyan]Prefix/VLAN roles[/cyan]")
        for item in self.plan.get("prefix_roles", []):
            rec = upsert(self.nb.ipam.roles, {"slug": item["slug"]}, item, self.stats, item["name"])
            self._remember("prefix_role", item["slug"], rec)

    # ------------------------------------------------------------ custom fields
    def _custom_fields(self) -> None:
        console.print("[cyan]Custom fields[/cyan]")
        for item in self.plan.get("custom_field_choice_sets", []):
            rec = upsert(
                self.nb.extras.custom_field_choice_sets,
                {"name": item["name"]},
                item,
                self.stats,
                f"choice set {item['name']}",
            )
            self._remember("choice_set", item["name"], rec)

        for item in self.plan.get("custom_fields", []):
            data = {
                **item,
                "required": False,
                "ui_visible": "if-set",
                "group_name": "Plants Demo",
            }
            if "choice_set" in data:
                data["choice_set"] = self.ids["choice_set"][data["choice_set"]]
            upsert(
                self.nb.extras.custom_fields, {"name": item["name"]}, data, self.stats, item["name"]
            )

    # ------------------------------------------------------------ organization
    def _regions(self) -> None:
        console.print("[cyan]Regions[/cyan]")
        for item in self.plan.get("regions", []):  # parents are listed before children
            data = {"name": item["name"], "slug": item["slug"]}
            if item.get("parent"):
                data["parent"] = self.ids["region"][item["parent"]]
            rec = upsert(
                self.nb.dcim.regions, {"slug": item["slug"]}, data, self.stats, item["name"]
            )
            self._remember("region", item["slug"], rec)

    def _tenants(self) -> None:
        console.print("[cyan]Tenants[/cyan]")
        for item in self.plan.get("tenant_groups", []):
            rec = upsert(
                self.nb.tenancy.tenant_groups,
                {"slug": item["slug"]},
                item,
                self.stats,
                item["name"],
            )
            self._remember("tenant_group", item["slug"], rec)
        for item in self.plan.get("tenants", []):
            data = {"name": item["name"], "slug": item["slug"]}
            if item.get("group"):
                data["group"] = self.ids["tenant_group"][item["group"]]
            rec = upsert(
                self.nb.tenancy.tenants, {"slug": item["slug"]}, data, self.stats, item["name"]
            )
            self._remember("tenant", item["slug"], rec)

    def _contact_model(self) -> None:
        console.print("[cyan]Contact groups and roles[/cyan]")
        for item in self.plan.get("contact_groups", []):
            rec = upsert(
                self.nb.tenancy.contact_groups,
                {"slug": item["slug"]},
                item,
                self.stats,
                item["name"],
            )
            self._remember("contact_group", item["slug"], rec)
        for item in self.plan.get("contact_roles", []):
            rec = upsert(
                self.nb.tenancy.contact_roles,
                {"slug": item["slug"]},
                item,
                self.stats,
                item["name"],
            )
            self._remember("contact_role", item["slug"], rec)

    # ------------------------------------------------------------ device types
    def _device_types(self) -> None:
        console.print("[cyan]Device types[/cyan]")
        for key, dtype in self.plan.get("device_types", {}).items():
            data = {
                "manufacturer": self.ids["manufacturer"][dtype["manufacturer"]],
                "model": dtype["model"],
                "slug": dtype["slug"],
                "u_height": dtype.get("u_height", 1 if dtype["kind"] != "ap" else 0),
                "is_full_depth": False,
            }
            rec = upsert(
                self.nb.dcim.device_types, {"slug": dtype["slug"]}, data, self.stats, dtype["model"]
            )
            self._remember("device_type", key, rec)
            if rec is not None:
                self._interface_templates(rec, dtype)

    def _interface_templates(self, device_type: Any, dtype: dict[str, Any]) -> None:
        # NetBox silently ignores unknown filter names, which would make `existing` span EVERY
        # device type. Query by the right filter AND re-check ownership in Python.
        try:
            found = self.nb.dcim.interface_templates.filter(device_type_id=device_type.id)
            existing = {
                t.name
                for t in found
                if t.device_type and t.device_type.id == device_type.id
            }
        except pynetbox.RequestError as exc:
            # NetBox 4.1 can reject a device type id it just created. Treat that
            # as "no templates yet" and let the create below report a real error.
            console.print(f"    [yellow]![/yellow] {dtype['model']}: {exc.error}")
            existing = set()
        missing = [
            {
                "device_type": device_type.id,
                "name": intf["name"],
                "type": intf["type"],
                "mgmt_only": intf["mgmt_only"],
            }
            for intf in expand_interfaces(dtype)
            if intf["name"] not in existing
        ]
        if not missing:
            return
        try:
            self.nb.dcim.interface_templates.create(missing)  # one bulk request
            self.stats.created += len(missing)
            console.print(f"    [green]+[/green] {len(missing)} interface templates")
        except Exception as exc:  # noqa: BLE001
            self.stats.errors += 1
            console.print(f"    [red]✗[/red] interface templates for {dtype['model']}: {exc}")
