# NetBox Plugin Configuration
# https://docs.netbox.dev/en/stable/plugins/

PLUGINS = [
    'netbox_topology_views',
]

PLUGINS_CONFIG = {
    'netbox_topology_views': {
        # Allow saving device positions in the topology view
        'allow_coordinates_saving': True,
        # Automatically save coordinates when devices are moved
        'always_save_coordinates': True,
    }
}

