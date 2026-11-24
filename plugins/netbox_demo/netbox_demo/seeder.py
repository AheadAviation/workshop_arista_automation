"""NetBox writes: seed the demo fabric, and clean it up again (INIT-001/SPEC-005).

Two rules govern everything here, both because this runs against a real NetBox
Cloud instance that holds other people's data:

1. Every write is scoped to the `netbox-demo` site or to an object already
   tagged `netbox-demo`. No unfiltered `delete()`.
2. Deletion is explicit and ordered, because cables, interfaces, IPs and VLANs
   reference each other. A partial delete leaves objects that make the next
   seed fail with a confusing uniqueness error.

`seed` is idempotent: it may be run any number of times and converges.

The interface-type constants are looked up by name with a fallback, because
NetBox renamed them between 4.5 and 4.6 (`TYPE_1000BASE_T` became `TYPE_1GE_SFP`,
and so on). A missing constant would otherwise break seeding on one version and
not the other, which is exactly the kind of failure that shows up mid-demo.
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
from ipam.models import VLAN, VRF, Prefix
from tenancy.models import Tenant

from . import design

#: Tag applied to everything this plugin creates, so cleanup can find it.
TAG_SLUG = "netbox-demo"
TAG_NAME = "netbox-demo"


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
    return Site.objects.filter(slug=design.SITE_SLUG).first()


def ensure_site() -> Site:
    site, _ = Site.objects.get_or_create(
        slug=design.SITE_SLUG,
        defaults={"name": design.SITE_NAME, "status": "active", "description": "netbox-demo fabric"},
    )
    return site


def ensure_prerequisites() -> dict[str, Any]:
    """Create the manufacturer, platform, device type and roles the fabric needs."""
    created: dict[str, Any] = {}

    manufacturer, _ = Manufacturer.objects.get_or_create(
        slug="arista", defaults={"name": "Arista"}
    )
    created["manufacturer"] = manufacturer.slug

    platform, _ = Platform.objects.get_or_create(
        slug="arista-eos",
        defaults={"name": "Arista EOS", "manufacturer": manufacturer},
    )
    created["platform"] = platform.slug

    device_type, _ = DeviceType.objects.get_or_create(
        slug="arista-ceos-lab",
        defaults={
            "model": "cEOS-lab",
            "manufacturer": manufacturer,
            "u_height": 1,
            "is_full_depth": False,
        },
    )
    created["device_type"] = device_type.slug

    for role_name, role_slug in design.ROLE_SLUGS.items():
        DeviceRole.objects.get_or_create(
            slug=role_slug,
            defaults={"name": role_name.capitalize(), "color": "9e9e9e"},
        )
    created["roles"] = list(design.ROLE_SLUGS.values())

    for tenant_name in ("CORE", "TENANT-A"):
        Tenant.objects.get_or_create(slug=tenant_name.lower(), defaults={"name": tenant_name})
    created["tenants"] = ["core", "tenant-a"]

    return created


def ensure_devices() -> list[str]:
    """Create or update the six demo devices, with management addresses."""
    site = ensure_site()
    manufacturer = Manufacturer.objects.get(slug="arista")
    platform = Platform.objects.get(slug="arista-eos")
    device_type = DeviceType.objects.get(slug="arista-ceos-lab")
    tag = _tag()

    touched: list[str] = []
    for name in design.ALL_DEVICES:
        role = DeviceRole.objects.get(slug=design.ROLE_SLUGS[design.role_for(name)])
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

        # The management interface and address, so discovery has something to
        # reconcile — but only where EOS actually owns the interface.
        #
        # On c9s, cEOS binds eth0 (MGMT_INTF=eth0) and never adopts Management0,
        # so documenting a Management0 with 172.31.30.x creates drift that can
        # never clear: the switch will never report that interface or address, so
        # Assurance keeps flagging it and the "zero drift" claim becomes false for
        # a reason that looks like a fabric fault. The address still exists; it is
        # just supplied by the platform, so it is not NetBox's to document here.
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


def ensure_vlans() -> list[int]:
    """Seed the tenant VLANs, keeping an operator's edits intact."""
    tenant_core = Tenant.objects.get(slug="core")
    tag = _tag()
    vids: list[int] = []
    for seed in design.SEED_VLANS:
        vlan, _ = VLAN.objects.update_or_create(
            vid=seed.vlan_id,
            defaults={
                "name": seed.name,
                "description": seed.description,
                "tenant": tenant_core,
                "status": "active",
            },
        )
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

    # 1. Cables first, or interface deletion is refused. NetBox 4.6 replaced the
    #    a_terminations/b_terminations fields with a single `terminations` relation,
    #    so match on the termination's interface rather than on a side.
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
