"""Read intent out of NetBox and build `DeviceIntent` objects (INIT-001/SPEC-005).

`design.py` says what the fabric should be; this module says what NetBox says it
is. Rendering always goes through here, so a change made in NetBox (a new tenant
VLAN, a different service baseline) reaches the switches without a code change.
"""

from __future__ import annotations

from typing import Any

from dcim.models import Device, Interface
from ipam.models import VLAN
from tenancy.models import Tenant

from . import design
from .fabric import (
    DeviceIntent,
    FabricInterface,
    MlagDomain,
    Peer,
    ServerPort,
    ServiceBaseline,
    TenantVlan,
)

#: Config-context key the standard baseline is stored under.
BASELINE_CONTEXT_NAME = "fabric-global"


def demo_devices() -> list[Device]:
    """The devices this plugin owns, in stable fabric order."""
    names = design.ALL_DEVICES
    devices = {
        device.name: device
        for device in Device.objects.filter(name__in=names).select_related(
            "device_type", "role", "site"
        )
    }
    return [devices[name] for name in names if name in devices]


def missing_devices() -> list[str]:
    """Devices the plugin expects but NetBox does not have yet."""
    present = set(Device.objects.filter(name__in=design.ALL_DEVICES).values_list("name", flat=True))
    return [name for name in design.ALL_DEVICES if name not in present]


def service_baseline() -> ServiceBaseline:
    """The standard baseline from config context, falling back to the defaults."""
    from extras.models import ConfigContext

    context = ConfigContext.objects.filter(name=BASELINE_CONTEXT_NAME).first()
    data: dict[str, Any] = {}
    if context and isinstance(context.data, dict):
        data = context.data.get("services", {}) or {}

    if not data:
        return ServiceBaseline()

    defaults = ServiceBaseline()
    return ServiceBaseline(
        syslog_host=str(data.get("syslog_host", defaults.syslog_host)),
        syslog_port=int(data.get("syslog_port", defaults.syslog_port)),
        snmp_community=str(data.get("snmp_community", defaults.snmp_community)),
        snmp_contact=str(data.get("snmp_contact", defaults.snmp_contact)),
        snmp_location=str(data.get("snmp_location", defaults.snmp_location)),
        ntp_servers=tuple(data.get("ntp_servers", defaults.ntp_servers)),
        dns_servers=tuple(data.get("dns_servers", defaults.dns_servers)),
        domain_name=str(data.get("domain_name", defaults.domain_name)),
        timezone=str(data.get("timezone", defaults.timezone)),
        lldp_timer=int(data.get("lldp_timer", defaults.lldp_timer)),
        lldp_hold_time=int(data.get("lldp_hold_time", defaults.lldp_hold_time)),
        errdisable_interval=int(data.get("errdisable_interval", defaults.errdisable_interval)),
        ssh_idle_timeout=int(data.get("ssh_idle_timeout", defaults.ssh_idle_timeout)),
        console_idle_timeout=int(data.get("console_idle_timeout", defaults.console_idle_timeout)),
        banner=str(data.get("banner", defaults.banner)),
    )


def tenant_vlans() -> list[TenantVlan]:
    """Tenant segments: seeded VLANs plus any VLAN an operator adds in NetBox.

    VNI, SVI and MTU come from the VLAN's own description/custom fields where
    present, so adding a VLAN in the UI is enough to make it deployable.
    """
    vlans: list[TenantVlan] = []
    seeded = {seed.vlan_id: seed for seed in design.SEED_VLANS}

    for vlan in VLAN.objects.filter(vid__in=seeded.keys()).order_by("vid"):
        seed = seeded[vlan.vid]
        vlans.append(
            TenantVlan(
                vlan_id=vlan.vid,
                name=vlan.name,
                vni=seed.vni,
                svi_address=seed.svi,
                mtu=seed.mtu,
            )
        )

    # VLANs added after seeding: derive VNI and SVI from the VLAN itself.
    extra = VLAN.objects.exclude(vid__in=seeded.keys()).filter(
        custom_field_data__demo_tenant=True
    ).order_by("vid")
    for vlan in extra:
        svi = str(vlan.custom_field_data.get("demo_svi") or "")
        if not svi:
            continue
        vlans.append(
            TenantVlan(
                vlan_id=vlan.vid,
                name=vlan.name,
                vni=int(vlan.custom_field_data.get("demo_vni") or design.vni_for_vlan(vlan.vid)),
                svi_address=svi,
                mtu=int(vlan.custom_field_data.get("demo_mtu") or 9000),
            )
        )
    return vlans


def _fabric_interfaces(device_name: str) -> list[FabricInterface]:
    links = []
    for link in design.FABRIC_LINKS:
        if link.left == device_name:
            links.append(
                FabricInterface(
                    name=link.left_port,
                    description=link.description,
                    address=link.left_address,
                    mtu=design.UNDERLAY_MTU,
                )
            )
        elif link.right == device_name:
            links.append(
                FabricInterface(
                    name=link.right_port,
                    description=link.description,
                    address=link.right_address,
                    mtu=design.UNDERLAY_MTU,
                )
            )
    return links


def _peers(device_name: str) -> list[Peer]:
    peers: list[Peer] = []
    if device_name in design.SPINES:
        for link in design.FABRIC_LINKS:
            if link.left == device_name:
                peers.append(
                    Peer(
                        address=link.right_address.split("/")[0],
                        remote_as=design.asn_for(link.right),
                        description=f"to {link.right}",
                        peer_group="LEAF_IPV4",
                    )
                )
    elif device_name in design.LEAVES:
        for link in design.FABRIC_LINKS:
            if link.right == device_name:
                peers.append(
                    Peer(
                        address=link.left_address.split("/")[0],
                        remote_as=design.SPINE_ASN,
                        description=f"to {link.left}",
                        peer_group="SPINE_IPV4",
                    )
                )
        # EVPN overlay to the spines' loopbacks.
        for spine in design.SPINES:
            peers.append(
                Peer(
                    address=design.ROUTER_IDS[spine],
                    remote_as=design.SPINE_ASN,
                    description=f"EVPN to {spine}",
                    peer_group="SPINE_EVPN",
                )
            )
    return peers


def _server_ports(device_name: str) -> list[ServerPort]:
    """Server ports whose VLANs come from NetBox rather than the design table."""
    if device_name not in design.LEAVES:
        return []
    ports: list[ServerPort] = []
    for intf in Interface.objects.filter(device__name=device_name, name=design.SERVER_PORT):
        vlan_ids: tuple[int, ...] = ()
        mode = "access"
        access_vlan: int | None = None

        untagged = getattr(intf, "untagged_vlan", None)
        if untagged is not None:
            access_vlan = untagged.vid
        tagged = list(intf.tagged_vlans.all()) if hasattr(intf, "tagged_vlans") else []
        if tagged:
            mode = "trunk"
            vlan_ids = tuple(sorted(v.vid for v in tagged))

        if access_vlan is None and not vlan_ids:
            # Not yet documented: render the port administratively down rather
            # than guessing a VLAN.
            ports.append(
                ServerPort(
                    name=intf.name,
                    description=intf.description or "Server-Port",
                    mode="access",
                    access_vlan=None,
                    enabled=False,
                )
            )
            continue

        ports.append(
            ServerPort(
                name=intf.name,
                description=intf.description or "Server-Port",
                mode=mode,
                access_vlan=access_vlan,
                trunk_vlans=vlan_ids,
                enabled=True,
            )
        )
    return ports


def _mlag(device_name: str) -> MlagDomain | None:
    pair = design.mlag_for(device_name)
    if pair is None:
        return None
    domain_id, local, peer = pair
    return MlagDomain(
        domain_id=domain_id,
        peer_address=peer,
        local_address=local,
        peer_link_members=design.MLAG_PEER_PORTS.get(device_name, ()),
    )


def intent_for(device_name: str) -> DeviceIntent:
    """Build one device's intent from NetBox plus the design table."""
    role = design.role_for(device_name)
    return DeviceIntent(
        hostname=device_name,
        role=role,
        asn=design.asn_for(device_name),
        router_id=design.ROUTER_IDS[device_name],
        mgmt_address=f"{design.MGMT_IPS[device_name]}/24",
        mgmt_gateway=design.MGMT_GATEWAY,
        vtep_address=design.VTEP_IPS.get(device_name),
        underlay_mtu=design.UNDERLAY_MTU,
        fabric_interfaces=_fabric_interfaces(device_name),
        peers=_peers(device_name),
        vlans=tenant_vlans() if role == "leaf" else [],
        server_ports=_server_ports(device_name),
        mlag=_mlag(device_name),
        services=service_baseline(),
        base_l2_vni=design.BASE_L2_VNI,
        anycast_gateway_mac=design.ANYCAST_GATEWAY_MAC,
    )


def intent_for_all() -> dict[str, DeviceIntent]:
    """Intent for every documented demo device, in fabric order."""
    return {device.name: intent_for(device.name) for device in demo_devices()}


def render_kwargs() -> dict[str, Any]:
    """Platform options for rendering, resolved in one place.

    Kept together so no call site can render with one platform assumption and
    compare with another — a mismatch there shows up as drift that does not exist.
    """
    from . import conf

    return {
        "eapi_vrf": conf.eapi_vrf(),
        "mgmt_interface": conf.document_mgmt_interface(),
    }


def tenant_summary() -> list[dict[str, Any]]:
    """Small table for the UI: tenant VLANs currently in NetBox."""
    return [
        {
            "vid": vlan.vid,
            "name": vlan.name,
            "tenant": getattr(vlan.tenant, "name", "") if vlan.tenant else "",
        }
        for vlan in VLAN.objects.filter(
            vid__in=[seed.vlan_id for seed in design.SEED_VLANS]
        ).order_by("vid")
    ]


def tenants_in_use() -> list[Tenant]:
    return list(Tenant.objects.filter(name__in=["CORE", "TENANT-A"]).order_by("name"))
