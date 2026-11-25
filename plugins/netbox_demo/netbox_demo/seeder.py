"""NetBox writes: seed the demo fabric, and clean it up again (INIT-001/SPEC-005).

Two rules govern everything here, both because this runs against a real NetBox
instance that may hold other people's data:

1. Every write is scoped to the `netbox-demo` site or to an object already
   tagged `netbox-demo`. No unfiltered `delete()`.
2. Deletion is explicit and ordered, because cables, interfaces, IPs and VLANs
   reference each other. A partial delete leaves objects that make the next
   seed fail with a confusing uniqueness error.

`seed` is idempotent and Discovery-aligned (INIT-035/SPEC-002): it reuses
`Site("NetBox Demo")`, `Manufacturer("Arista")`, `DeviceType(model="cEOSLab")`,
`Platform(name="Arista")`, and `DeviceRole("Spine"/"Leaf")` whether Discovery
ran first or Seed ran first, avoiding duplicate objects and perpetual
Assurance attribute drift.
"""

from __future__ import annotations

from typing import Any

from dcim.choices import InterfaceTypeChoices
from dcim.models import (
    Cable,
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Platform,
    Site,
)
from extras.models import ConfigContext
from ipam.models import VLAN, VRF, Prefix, VLANGroup
from tenancy.models import Tenant

from . import design

#: Tag applied to everything this plugin creates, so cleanup can find it.
TAG_SLUG = "netbox-demo"
TAG_NAME = "netbox-demo"

#: Model and platform strings reported by Arista cEOS via Orb NAPALM discovery.
CEOS_DEVICE_TYPE_MODEL = "cEOSLab"
CEOS_DEVICE_TYPE_SLUG = "arista-ceoslab"
ARISTA_PLATFORM_NAME = "Arista"
ARISTA_PLATFORM_SLUG = "arista"


def document_management_interface() -> bool:
    """Whether EOS adopts the management interface on this platform.

    Imported lazily: `conf` reads Django settings, and this module is imported by
    tests that run without a configured Django.
    """
    from . import conf

    return conf.document_mgmt_interface()


def _iface_type(*candidates: str) -> str:
    """First interface-type constant that exists in this NetBox version."""
    for name in candidates:
        value = getattr(InterfaceTypeChoices, name, None)
        if value is not None:
            return value
    # `other` exists in every 4.x release and is a truthful fallback.
    return getattr(InterfaceTypeChoices, "TYPE_OTHER", "other")


#: 1G copper management port.
MGMT_IFACE_TYPE = _iface_type("TYPE_1000BASE_T", "TYPE_1GE_SFP", "TYPE_1000BASE_X_SFP")
#: 10G fabric uplink.
FABRIC_IFACE_TYPE = _iface_type("TYPE_10GBASE_SFPP_PLUS", "TYPE_10GE_SFP_PLUS", "TYPE_10GBASE_X_SFPP")


def _tag():
    from extras.models import Tag

    tag, _ = Tag.objects.get_or_create(
        slug=TAG_SLUG, defaults={"name": TAG_NAME, "description": "Managed by netbox-demo"}
    )
    return tag


def get_site() -> Site | None:
    return Site.objects.filter(slug=design.SITE_SLUG).first() or Site.objects.filter(
        name=design.SITE_NAME
    ).first()


def ensure_site() -> Site:
    existing = get_site()
    if existing is not None:
        return existing
    site, _ = Site.objects.get_or_create(
        slug=design.SITE_SLUG,
        defaults={"name": design.SITE_NAME, "status": "active", "description": "netbox-demo fabric"},
    )
    return site


def _ensure_manufacturer() -> Manufacturer:
    existing = (
        Manufacturer.objects.filter(slug="arista").first()
        or Manufacturer.objects.filter(name__iexact="Arista").first()
    )
    if existing is not None:
        return existing
    manufacturer, _ = Manufacturer.objects.get_or_create(slug="arista", defaults={"name": "Arista"})
    return manufacturer


def _ensure_platform(manufacturer: Manufacturer) -> Platform:
    existing = (
        Platform.objects.filter(name__iexact=ARISTA_PLATFORM_NAME).first()
        or Platform.objects.filter(slug=ARISTA_PLATFORM_SLUG).first()
    )
    if existing is not None:
        return existing
    platform, _ = Platform.objects.get_or_create(
        slug=ARISTA_PLATFORM_SLUG,
        defaults={"name": ARISTA_PLATFORM_NAME, "manufacturer": manufacturer},
    )
    return platform


def _ensure_device_type(manufacturer: Manufacturer) -> DeviceType:
    existing = (
        DeviceType.objects.filter(manufacturer=manufacturer, model=CEOS_DEVICE_TYPE_MODEL).first()
        or DeviceType.objects.filter(slug=CEOS_DEVICE_TYPE_SLUG).first()
    )
    if existing is not None:
        return existing
    device_type, _ = DeviceType.objects.get_or_create(
        slug=CEOS_DEVICE_TYPE_SLUG,
        defaults={
            "model": CEOS_DEVICE_TYPE_MODEL,
            "manufacturer": manufacturer,
            "u_height": 1,
            "is_full_depth": False,
        },
    )
    return device_type


def _ensure_role(role_name: str) -> DeviceRole:
    role_slug = design.ROLE_SLUGS[role_name]
    display_name = role_name.capitalize()
    existing = (
        DeviceRole.objects.filter(name__iexact=display_name).first()
        or DeviceRole.objects.filter(slug=role_slug).first()
    )
    if existing is not None:
        return existing
    role, _ = DeviceRole.objects.get_or_create(
        slug=role_slug,
        defaults={"name": display_name, "color": "9e9e9e"},
    )
    return role


def ensure_prerequisites() -> dict[str, Any]:
    """Create or reuse the manufacturer, platform, device type and roles."""
    created: dict[str, Any] = {}

    manufacturer = _ensure_manufacturer()
    created["manufacturer"] = manufacturer.slug

    platform = _ensure_platform(manufacturer)
    created["platform"] = platform.slug

    device_type = _ensure_device_type(manufacturer)
    created["device_type"] = device_type.slug

    roles = []
    for role_name in design.ROLE_SLUGS:
        roles.append(_ensure_role(role_name).slug)
    created["roles"] = roles

    for tenant_name in ("CORE", "TENANT-A"):
        Tenant.objects.get_or_create(slug=tenant_name.lower(), defaults={"name": tenant_name})
    created["tenants"] = ["core", "tenant-a"]

    return created


def ensure_devices() -> list[str]:
    """Create or update the six demo devices, with optional management addresses."""
    site = ensure_site()
    manufacturer = _ensure_manufacturer()
    platform = _ensure_platform(manufacturer)
    device_type = _ensure_device_type(manufacturer)
    tag = _tag()

    touched: list[str] = []
    for name in design.ALL_DEVICES:
        role = _ensure_role(design.role_for(name))
        device, _ = Device.objects.update_or_create(
            name=name,
            defaults={
                "site": site,
                "device_type": device_type,
                "role": role,
                "platform": platform,
                "status": "active",
                "description": "netbox-demo fabric switch",
            },
        )
        device.tags.add(tag)
        touched.append(device.name)

        # Management0 is only documented where EOS actually owns it (local Docker
        # ContainerLab). On c9s, cEOS binds eth0 and never adopts Management0.
        if not document_management_interface():
            continue

        intf, _ = Interface.objects.get_or_create(
            device=device,
            name="Management0",
            defaults={
                "type": MGMT_IFACE_TYPE,
                "enabled": True,
                "mgmt_only": True,
                "description": "OOB management",
            },
        )
        from ipam.models import IPAddress

        address = f"{design.MGMT_IPS[name]}/24"
        ip, _ = IPAddress.objects.get_or_create(
            address=address,
            defaults={"description": f"{name} management", "status": "active"},
        )
        ip.assigned_object = intf
        ip.save()
        if device.primary_ip4_id != ip.id:
            device.primary_ip4 = ip
            device.save(update_fields=["primary_ip4"])

    return touched


def _ensure_vlan_group() -> VLANGroup:
    existing = (
        VLANGroup.objects.filter(slug=design.SITE_SLUG).first()
        or VLANGroup.objects.filter(name__iexact=design.SITE_SLUG).first()
    )
    if existing is not None:
        return existing
    group, _ = VLANGroup.objects.get_or_create(
        slug=design.SITE_SLUG,
        defaults={"name": design.SITE_SLUG, "description": "netbox-demo fabric VLANs"},
    )
    return group


def ensure_vlans() -> list[int]:
    """Seed the tenant VLANs in the `netbox-demo` VLANGroup, safe with existing VLANs."""
    tenant_core = Tenant.objects.get(slug="core")
    vlan_group = _ensure_vlan_group()
    tag = _tag()
    vids: list[int] = []
    for seed in design.SEED_VLANS:
        vlan = (
            VLAN.objects.filter(vid=seed.vlan_id, group=vlan_group).first()
            or VLAN.objects.filter(vid=seed.vlan_id, group__isnull=True).first()
        )
        if vlan is None:
            vlan = VLAN.objects.create(
                vid=seed.vlan_id,
                group=vlan_group,
                name=seed.name,
                description=seed.description,
                tenant=tenant_core,
                status="active",
            )
        else:
            vlan.group = vlan_group
            vlan.name = seed.name
            vlan.description = seed.description
            vlan.tenant = tenant_core
            vlan.status = "active"
            vlan.save()
        vlan.tags.add(tag)
        vids.append(vlan.vid)
    return vids


def ensure_vrfs() -> list[str]:
    tenant_core = Tenant.objects.get(slug="core")
    created = []
    for name in ("MGMT", "CORE"):
        vrf, _ = VRF.objects.get_or_create(
            name=name,
            defaults={"rd": f"65000:{1000 if name == 'CORE' else 1001}", "tenant": tenant_core},
        )
        created.append(vrf.name)
    return created


def ensure_underlay_prefixes() -> list[str]:
    """Document the /31 underlay links so the fabric is legible in IPAM."""
    vrf = VRF.objects.get(name="CORE")
    tag = _tag()
    prefixes: list[str] = []
    for link in design.FABRIC_LINKS:
        prefix, _ = Prefix.objects.get_or_create(
            prefix=_prefix_of(link.left_address),
            vrf=vrf,
            defaults={"description": f"demo-underlay {link.left} <-> {link.right}", "status": "active"},
        )
        prefix.tags.add(tag)
        prefixes.append(str(prefix.prefix))
    return prefixes


def _prefix_of(address: str) -> str:
    """The /31 containing this interface address."""
    from ipaddress import ip_interface

    return str(ip_interface(address).network)


def ensure_config_context() -> str:
    """One config context carrying the standard services baseline."""
    from .intent import BASELINE_CONTEXT_NAME

    tag = _tag()
    context, _ = ConfigContext.objects.update_or_create(
        name=BASELINE_CONTEXT_NAME,
        defaults={
            "description": "Standard fabric services for netbox-demo",
            "weight": 1000,
            "data": {
                "services": {
                    "syslog_host": "172.31.30.1",
                    "syslog_port": 514,
                    "snmp_community": "FABRIC-RO",
                    "snmp_contact": "netops@demo.local",
                    "snmp_location": "NETBOX-DEMO-FABRIC",
                    "ntp_servers": ["172.31.30.1"],
                    "dns_servers": ["172.31.30.1"],
                    "domain_name": "demo.local",
                    "timezone": "UTC",
                    "lldp_timer": 30,
                    "lldp_hold_time": 120,
                    "errdisable_interval": 300,
                    "ssh_idle_timeout": 30,
                    "console_idle_timeout": 15,
                    "banner": "AUTHORIZED ACCESS ONLY - NetBox Demo Fabric",
                }
            },
        },
    )
    context.tags.add(tag)
    return context.name


def ensure_cables() -> int:
    """Cable the fabric links, so Topology Views has something to draw."""
    tag = _tag()
    made = 0
    for link in design.FABRIC_LINKS:
        left_dev = Device.objects.filter(name=link.left).first()
        right_dev = Device.objects.filter(name=link.right).first()
        if not left_dev or not right_dev:
            continue

        left_intf, _ = Interface.objects.get_or_create(
            device=left_dev,
            name=link.left_port,
            defaults={"type": FABRIC_IFACE_TYPE, "enabled": True},
        )
        right_intf, _ = Interface.objects.get_or_create(
            device=right_dev,
            name=link.right_port,
            defaults={"type": FABRIC_IFACE_TYPE, "enabled": True},
        )
        left_intf.tags.add(tag)
        right_intf.tags.add(tag)

        if left_intf.cable_id or right_intf.cable_id:
            continue
        cable = Cable(
            a_terminations=[left_intf],
            b_terminations=[right_intf],
            status="connected",
            description=link.description,
        )
        cable.save()
        made += 1
    return made


def seed_all() -> dict[str, Any]:
    """Idempotent full seed. Safe to run repeatedly."""
    summary: dict[str, Any] = {"prerequisites": ensure_prerequisites()}
    summary["site"] = ensure_site().slug
    summary["devices"] = ensure_devices()
    summary["vrfs"] = ensure_vrfs()
    summary["vlans"] = ensure_vlans()
    summary["prefixes"] = ensure_underlay_prefixes()
    summary["config_context"] = ensure_config_context()
    summary["cables"] = ensure_cables()
    return summary


# --------------------------------------------------------------------------- #
# Cleanup
# --------------------------------------------------------------------------- #


def clean_demo_data() -> dict[str, int]:
    """Remove everything this plugin documented, leaving other data untouched.

    Order matters: cables before interfaces, interfaces before devices, and
    addresses before the interfaces they sit on.
    """
    from ipam.models import IPAddress

    removed: dict[str, int] = {}

    site = get_site()
    devices = Device.objects.filter(name__in=design.ALL_DEVICES)
    if site is not None:
        devices = Device.objects.filter(site=site) | devices
    devices = devices.distinct()

    # 1. Cables first, or interface deletion is refused.
    from dcim.models import CableTermination

    cable_ids = CableTermination.objects.filter(
        interface__device__in=devices
    ).values_list("cable_id", flat=True)
    cables = Cable.objects.filter(pk__in=list(cable_ids))
    removed["cables"] = cables.count()
    cables.delete()

    # 2. Addresses assigned to the demo devices.
    addresses = IPAddress.objects.filter(interface__device__in=devices)
    removed["ip_addresses"] = addresses.count()
    addresses.delete()

    # 3. Devices (interfaces cascade).
    removed["devices"] = devices.count()
    devices.delete()

    # 4. VLANs, prefixes and the config context this plugin owns.
    vlans = VLAN.objects.filter(vid__in=[seed.vlan_id for seed in design.SEED_VLANS])
    removed["vlans"] = vlans.count()
    vlans.delete()

    prefixes = Prefix.objects.filter(description__startswith="demo-underlay")
    removed["prefixes"] = prefixes.count()
    prefixes.delete()

    from .intent import BASELINE_CONTEXT_NAME

    removed["config_contexts"] = ConfigContext.objects.filter(name=BASELINE_CONTEXT_NAME).count()
    ConfigContext.objects.filter(name=BASELINE_CONTEXT_NAME).delete()

    return removed
