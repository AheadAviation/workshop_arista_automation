# NetBox Plugin Configuration
# https://docs.netbox.dev/en/stable/plugins/

PLUGINS = [
    'netbox_topology_views',
    'netbox_ztp_portal',
]

PLUGINS_CONFIG = {
    'netbox_topology_views': {
        # Allow saving device positions in the topology view
        'allow_coordinates_saving': True,
        # Automatically save coordinates when devices are moved
        'always_save_coordinates': True,
    },
    'netbox_ztp_portal': {
        # The portal calls NetBox's own REST API (loopback to self inside the
        # container) with the workshop's static superuser token.
        'netbox_url': 'http://localhost:8080',
        'netbox_token': '0123456789abcdef0123456789abcdef01234567',
        'self_portal_tag': 'self-portal',
        'containerlab_tag': 'containerlab',
        'use_mock_infoblox': True,
        'ceos_device_type_slug': 'ceos-lab',
        'ceos_device_type_model': 'cEOS-lab',
        'manufacturer': 'Arista',
    },
}
