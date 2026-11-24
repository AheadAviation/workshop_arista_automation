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
        # AWX was dropped: its 24.6.1 Docker image does not work (see the initiative record).
        'awx_url': '',
        'awx_token': '',
        # Zammad. TWO addresses, because they are genuinely different and conflating them broke
        # every ticket link:
        #   itsm_url        — the API, from inside this container: the compose service name.
        #   itsm_public_url — where a BROWSER finds Zammad: the host-published port.
        # A link built from itsm_url is a Docker-internal name the browser cannot resolve, and
        # 127.0.0.1:8080 is not Zammad's published port either — it is 8090.
        'itsm_url': 'http://zammad-nginx:8080',
        'itsm_public_url': 'http://localhost:8090',
        'itsm_token': '4aroyvDrKCLlDhnA0C1wPppAakjqICFRHqvCcJo1bfTS5JzxyWUW12aggHjfhqTX',
        # Arista eAPI. The plugin reads and drives switches over HTTPS eAPI rather than
        # through Docker, which is why NetBox needs no Docker privilege for devices at all.
        'eapi_username': 'admin',
        'eapi_password': 'admin',
    },
}
