"""Install NetBox ConfigTemplates so device "Render Config" works out of the box.

**Why this is needed.** `DeviceRenderConfigView` shows "No configuration template has been
assigned for this device" when the database has no `ConfigTemplate`. Fresh NetBox has none, so
the Render Config tab is empty until something creates one — which is what this does.

**What a template receives**, established by reading the view's source:

    context_data = device.get_config_context()   # ConfigContext data, merged
    context_data["device"] = device

plus every NetBox model class namespaced by app (`dcim.Device`, `ipam.VLAN`, …).

**The shapes below are the real ones**, read out of this fabric's config context rather than
guessed. VLANs are not NetBox `ipam.VLAN` objects here — this repo models L2 segments inside the
`tenants` context, as `tenants[].l2_segments[]` with `vlan_number` / `vlan_name` / `vlan_ip` /
`mtu`. A template written against `ipam.VLAN` renders an empty VLAN block and looks broken.

**Idempotent.** Re-running updates the template in place rather than creating duplicates.
"""

EOS_TEMPLATE = """\
{#-
  Arista EOS configuration rendered from NetBox.

  Written for NetBox's native "Render Config" tab. Reads the device's own merged config context,
  so this is the source of truth rendering itself rather than a parallel model.

  Deliberately readable: this is what a presenter puts on a screen, and a wall of generated
  stanzas makes the point worse rather than better.
-#}
!
! {{ device.name }} — rendered from NetBox
! {{ device.device_type.manufacturer.name }} {{ device.device_type.model }}
!
vlan internal order ascending range 4000 4079
service routing protocols model multi-agent
!
hostname {{ device.name }}
!
{% if device.primary_ip4 %}
interface Management0
   description OoB MGMT
   ip address {{ device.primary_ip4.address.ip }}/{{ device.primary_ip4.address.prefix_length }}
   no shutdown
!
{% endif %}
{% if management is defined and management.gateway is defined %}
ip route vrf {{ management.vrf | default('MGMT') }} 0.0.0.0/0 {{ management.gateway }} name MGMT_DEFAULT_ROUTE
!
{% endif %}
! ============================ Tenant VLANs ============================
{% for tenant in tenants | default([]) %}
! Tenant {{ tenant.name }}
{% for seg in tenant.l2_segments | default([]) %}
vlan {{ seg.vlan_number }}
   name {{ seg.vlan_name }}
!
interface Vlan{{ seg.vlan_number }}
   description {{ seg.description | default(seg.vlan_name) }}
   no shutdown
   vrf {{ tenant.name }}
   ip address virtual {{ seg.vlan_ip }}
   mtu {{ seg.mtu | default(1500) }}
!
{% endfor %}
{% endfor %}
! ============================ Interfaces ==============================
{% for iface in device.interfaces.all() %}
interface {{ iface.name }}
   description {{ iface.description | default('') }}
{% for addr in iface.ip_addresses.all() %}
   ip address {{ addr.address }}
{% endfor %}
{% if iface.enabled %}
   no shutdown
{% else %}
   shutdown
{% endif %}
!
{% endfor %}
! ================================= BGP ================================
{% if device.cf.bgp_asn %}
router bgp {{ device.cf.bgp_asn }}
{% if device.cf.router_id %}
   router-id {{ device.cf.router_id }}
{% endif %}
!
{% endif %}
end
"""


def install() -> None:
    from dcim.models import Device
    from extras.models import ConfigTemplate

    template, created = ConfigTemplate.objects.get_or_create(
        name="Arista EOS (NetBox)",
        defaults={
            "description": (
                "EOS configuration rendered from NetBox device data and config context. "
                "Assigned to every Arista device so the Render Config tab works with no setup."
            ),
            "template_code": EOS_TEMPLATE,
        },
    )
    if not created:
        # Keep the code current on re-run rather than leaving a stale version behind.
        template.template_code = EOS_TEMPLATE
        template.save()

    assigned = 0
    for device in Device.objects.filter(device_type__manufacturer__name__iexact="Arista"):
        if device.config_template_id != template.pk:
            device.config_template = template
            device.save(update_fields=["config_template"])
        assigned += 1

    print(f"ConfigTemplate {'created' if created else 'updated'}: {template.name}")
    print(f"Assigned to {assigned} Arista device(s)")
    if assigned == 0:
        # Expected on a brand-new database: the template exists, but there are no devices to
        # assign it to yet. The plugin's "Add devices" action calls this again afterwards, so
        # this is a state, not a failure.
        print("(no devices yet — the template will be assigned after the fabric is imported)")

    # Configure netbox-topology-views defaults so both the sidebar link and "View Topology Map"
    # draw the fabric immediately with switch icons instead of an empty canvas.
    try:
        from django.contrib.auth import get_user_model
        from django.contrib.contenttypes.models import ContentType
        from dcim.models import DeviceRole
        from netbox_topology_views.models import IndividualOptions, RoleImage

        for user in get_user_model().objects.all():
            opts, _ = IndividualOptions.objects.get_or_create(user_id=user.id)
            opts.draw_default_layout = True
            opts.show_cables = True
            opts.show_unconnected = True
            opts.show_neighbors = True
            opts.save()

        ct = ContentType.objects.get_for_model(DeviceRole)
        for slug, img in (
            ("spine", "netbox_topology_views/img/core-router.svg"),
            ("leaf", "netbox_topology_views/img/access-switch.svg"),
            ("border", "netbox_topology_views/img/distribution-switch.svg"),
        ):
            role = DeviceRole.objects.filter(slug=slug).first()
            if role:
                RoleImage.objects.update_or_create(
                    content_type=ct, object_id=role.id, defaults={"image": img}
                )
        print("Configured netbox-topology-views default layout and role icons")
    except Exception as exc:
        print(f"Skipped topology-views defaults: {exc}")


install()
