# Enterprise Cloud setup (NFR)

The path from a bare NetBox Cloud instance to the demo running from the cloud.
Written for your own NFR instance, so the plugin is installed privately rather
than from the community catalogue.

## 1. What the cloud instance needs

Verified against a real instance (`/api/status/`):

| Component | Version seen | Needed |
|---|---|---|
| NetBox | 4.6.2 | 4.5+ |
| Django | 6.0.5 | — |
| `netbox_assurance_plugin` | 1.5.4 | for drift review |
| `netbox_diode_plugin` | 1.14.1 | for Discovery ingestion |
| `netbox_topology_views` | 4.5.1 | for the cabled fabric to draw |
| `netbox_branching` / `netbox_changes` | 1.1.0 | optional, for change staging |

Check yours:

```bash
curl -s -H "Authorization: Token $NETBOX_CLOUD_TOKEN" \
  "$NETBOX_CLOUD_URL/api/status/" | jq '.netbox-version, .plugins'
```

## 2. Build and install the plugin

```bash
cd plugin/netbox_demo
python -m build            # dist/netbox_demo-0.1.0-py3-none-any.whl
```

Install on the instance the same way any private plugin is installed there, then
enable it:

```python
PLUGINS = ["netbox_demo"]

PLUGINS_CONFIG = {
    "netbox_demo": {
        "lab_agent_url":   "http://100.x.y.z:8085",   # lab host's Tailscale address
        "lab_agent_token": "<same value as LAB_AGENT_TOKEN>",
        "site_slug":       "netbox-demo",
        "assurance_deviations_url": "/plugins/assurance/deviations/",
    },
}
```

### Two settings that are only correct for one platform

Set these by where the fabric runs. Getting them wrong does not fail loudly — it
breaks the demo at the push step, or makes drift impossible to clear.

| Setting | Local ContainerLab | Kubernetes (c9s) |
|---|---|---|
| `eapi_vrf` | `"MGMT"` (default) | `""` |
| `document_mgmt_interface` | `true` (default) | `false` |

**`eapi_vrf`.** Binding eAPI into a VRF is the correct production posture and
what local ContainerLab uses. On Kubernetes via c9s, the VRF-bound eAPI listener
lands in a network namespace c9s does not interpose: **port 443 stops answering
while SSH on 22 keeps working.** Pushing a config that re-binds eAPI into MGMT
therefore kills the demo's own control channel at the push step, and it looks like
the switch died. Set `""` on c9s so eAPI stays in the default VRF.

**`document_mgmt_interface`.** On c9s, cEOS binds `eth0` (`MGMT_INTF=eth0`) and
never adopts `Management0`, so `show ip interface brief` is empty and EOS does not
know the management address. Documenting that interface in NetBox creates drift
that can never clear, because the switch will never report it — which makes the
"zero drift" claim false for a reason that looks like a fabric fault. Set `false`
to leave it out of NetBox, out of rendered config, and out of drift comparison.
The address still exists; it is supplied by the platform.

For the Kubernetes profile:

```python
        "eapi_vrf": "",
        "document_mgmt_interface": False,
```

> Both defaults preserve the original local behaviour, so an existing local lab
> renders byte-identically after the change. A test asserts that.


Then run the migrations and restart:

```bash
python /opt/netbox/netbox/manage.py migrate netbox_demo
```

The plugin ships a real `0001_initial.py` generated inside NetBox 4.6.2. Never
replace it with an empty file: that marks the app migrated with no tables, and
every page then fails with `relation does not exist`.

## 3. Check the wiring before the demo

```bash
curl -s -H "Authorization: Token $NETBOX_CLOUD_TOKEN" \
  "$NETBOX_CLOUD_URL/api/plugins/demo/health/" | jq
```

Read `configured`, `lab_reachable` and `config_problem`. The response is designed
to say which half is wrong:

| Symptom | Cause |
|---|---|
| `configured: false` | a setting is missing; `config_problem` names it |
| `lab_reachable: false`, agent running | Tailscale down, or the token differs |
| `assurance.available: false` | Assurance not installed on this instance |

## 4. Diode credentials for Discovery

Discovery pushes into NetBox through Diode, so it needs its own credentials — the
plugin's API token is not one of them.

1. In NetBox Cloud, create a Diode client id / secret (Diode application).
2. Put them in `local-lab/.env` as `DIODE_CLIENT_ID` / `DIODE_CLIENT_SECRET`.
3. Set `DIODE_TARGET` to `https://<instance>/diode`.

`ORB_MODE=diode` (default) lets the Runbook's discovery step trigger a one-shot
sweep. `ORB_MODE=fleet` instead hands the agent to NetBox Labs Console Fleet
Management, where discovery jobs are scheduled and credentials live in your vault;
the Runbook then tells you to start the job from the Console.

## 5. Tailscale

The lab host and the cloud instance must reach each other over the tailnet:

```bash
tailscale ip -4                 # on the lab host, e.g. 100.89.183.114
curl -s http://100.89.183.114:8085/ping
```

`lab_agent_url` is that address with `:8085`. Do not open a public port: the
agent has no authentication beyond the bearer token, and binding it to the
tailnet is what keeps that acceptable.

Set `LAB_AGENT_BIND_IP` to the tailnet address so the agent does not listen on a
coffee-shop LAN.

## 6. First run

Follow **NetBox Demo → Runbook** top to bottom. Each step writes a `LabJob`
record, so after any failure open the job and read the output rather than guessing.

## Things that will look like bugs and are not

These all cost real time during development, so they are worth knowing:

- **A switch keeps its old config after "destroy".** ContainerLab leaves
  `clab-<lab>-<node>/flash` behind, and cEOS keeps its startup-config there. The
  destroy action removes those directories; if you destroy by hand, remove them too.
- **A switch boots with the default hostname and no eAPI.** ContainerLab copies
  `startup-config` into a path it then bind-mounts. When ContainerLab itself runs
  in a container, that path resolves on the *host* where it does not exist, Docker
  creates an empty directory, and the config is silently never applied. The agent
  writes the file through `docker exec` instead, which is why it works.
- **`vrf instance MGMT` must precede anything that references `vrf MGMT`.** EOS
  aborts the rest of a startup-config when a command references a VRF that does
  not exist yet, so ordering this wrong leaves the switch looking unbooted.
- **eAPI refuses connections for roughly a minute after a node restarts.** It is
  TLS on port 443 and only listens once EOS has finished starting.
- **`show bgp evpn summary` fails on a bare switch.** There is no BGP process yet,
  which is the point of the bare state. The fabric check reports "no BGP yet"
  rather than calling the switch broken.
- **NetBox Cloud has no writable local filesystem.** Demo progress and staged Day 2
  changes are rows in Postgres, not files, because several worker processes serve
  the UI and a file would disagree with itself.
- **`docker compose down` does not remove the switches.** The cEOS nodes belong to
  ContainerLab, not to this compose file, so use the Runbook's **Destroy the lab**
  (or `containerlab destroy`) to remove them. A leftover
  `netbox-demo-fabric_mgmt` network behaves the same way.
