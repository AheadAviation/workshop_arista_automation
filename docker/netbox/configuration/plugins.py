# NetBox Plugin Configuration
# https://docs.netbox.dev/en/stable/plugins/

PLUGINS = [
    'netbox_topology_views',
    'netbox_ztp_portal',
    'netbox_ops',
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
    'netbox_ops': {
        # Inside the NetBox container the app listens on 8080; 8888 is only the host-side
        # published port. Using 8888 here makes the plugin try to call itself over the
        # host's network from inside its own container, which fails.
        'netbox_url': 'http://localhost:8080',
        'netbox_token': '0123456789abcdef0123456789abcdef01234567',
        # Scoped Docker proxy. Never a socket path: see the security note in
        # sdd/initiatives/INIT-002-netbox-ops-plugin/.
        'docker_proxy_url': 'http://docker-proxy:2375',
        'lab_topology': 'clab/topology-demo.clab.yml',
        'demo_vlan': 150,
        'demo_vlan_name': 'PHILIPS_MEDICAL',
        # AWX and Zammad are filled in by their bootstrap jobs when those profiles run.
        # Empty is a valid state and the page says so rather than showing an empty list.
        'awx_url': '',
        'awx_token': '',
        'itsm_url': '',
        'itsm_token': '',
    },
}
