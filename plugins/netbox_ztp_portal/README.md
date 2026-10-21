# AHEAD ZTP Portal (`netbox_ztp_portal`)

A NetBox plugin that provides a self-service **Zero-Touch Provisioning** portal for
Arista EVPN/VXLAN spine-leaf fabrics, built for the AHEAD Arista automation workshop.

It is the productized successor to the "Bloom Mock Ups" Flask prototype: the same
framework-agnostic provisioning logic, re-pointed from Cisco Catalyst to Arista cEOS,
rebranded to AHEAD, and installed directly inside NetBox.

## Portals

- **New Site** — an EVPN/VXLAN fabric designer. Pick a site code, spine count, and
  number of leaf MLAG pairs; the portal computes the full device / interface / cable /
  custom-field set (mirroring the workshop's `netbox_data/fabric_topology.yml` schema)
  and writes it into NetBox tagged `containerlab`, ready for the existing
  generate → deploy pipeline to bring up as live cEOS nodes in ContainerLab.
- **New Block** — onboard an individual spine/leaf into an existing fabric by serial.
- **Fleet tracking** — bring-up progress across every portal-built fabric.

## Install (already wired for the workshop)

1. Package is `pip install -e`'d into the NetBox image (`docker/netbox/Dockerfile`).
2. Enabled in `docker/netbox/configuration/plugins.py` (`PLUGINS = [... 'netbox_ztp_portal']`).
3. Rebuild + restart: `./run.ps1 build && ./run.ps1 up`.
4. Browse to **http://localhost:8888/plugins/ztp/**.

## Deploy a fabric to ContainerLab (end to end)

1. **New Site** → choose a site code (e.g. `DC2`), spine count, leaf MLAG pairs →
   **Preview fabric** → **Build in NetBox**. This writes the site, cEOS-lab devices
   (tagged `containerlab`), interfaces, `/31` fabric cables, loopbacks, management
   IPs, ASNs, VLANs, the base-services config-context, and a per-pair MLAG
   config-context — everything the workshop pipeline needs.
2. `./run.ps1 generate` — renders `output/configs/<node>.cfg` from NetBox (the new
   devices are picked up automatically via the `containerlab` tag).
3. `docker compose run --rm ansible python scripts/generate_clab_topology_docker.py`
   — regenerates `clab/topology-docker.clab.yml` from NetBox.
4. `./run.ps1 deploy` — brings the cEOS nodes up in ContainerLab.
5. `./run.ps1 apply` — pushes each generated config into its node and applies it
   live with `configure replace`.

> **Why the separate `apply` step?** The ContainerLab runner runs docker-out-of-docker,
> so node flash directories it creates aren't visible to the host Docker daemon and
> startup-config can't be injected at boot. `run.ps1 apply` copies each config into the
> running node's flash and applies it with `configure replace` — which preserves the
> ContainerLab links (a `docker restart` would tear them down). Deploying ContainerLab
> natively in WSL2 instead of via the runner container avoids this and injects
> startup-config at boot.

Verify a node: `docker exec clab-netbox-arista-lab-dc2-spine-01 Cli -p 15 -c "show ip bgp summary"`.

## Configuration (`PLUGINS_CONFIG['netbox_ztp_portal']`)

| Setting | Default | Purpose |
|---|---|---|
| `netbox_url` | `http://localhost:8080` | NetBox API base the portal calls (itself). |
| `netbox_token` | workshop static token | API token used for writes. |
| `self_portal_tag` | `self-portal` | Tag applied to everything the portal creates. |
| `containerlab_tag` | `containerlab` | Tag the pipeline/inventory filters on. |
| `use_mock_infoblox` | `True` | Serve mock IPAM instead of a real Infoblox WAPI. |
| `ceos_device_type_slug` | `ceos-lab` | DeviceType used for generated nodes. |
