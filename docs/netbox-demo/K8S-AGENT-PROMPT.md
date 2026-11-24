# Agent prompt: ContainerLab fabric + Orb Agent on Kubernetes

Paste the block below into your Kubernetes agent. It is written to be
tool-agnostic and to make the agent verify assumptions rather than guess.

---

## Mission

Stand up an **Arista cEOS EVPN/VXLAN fabric** and the **NetBox Discovery (Orb
Agent)** that feeds it into NetBox Cloud, both running **inside an existing
Kubernetes cluster**. This is the Kubernetes half of a demo whose cloud half
already works on a laptop with Docker.

Deliver, in this order, stopping to report at each gate:

1. A working cEOS fabric in the cluster, reachable for management/eAPI.
2. An Orb Agent that discovers those switches and ingests them into NetBox Cloud
   through Diode.
3. Repeatable scripts or manifests for create/destroy, plus a short runbook.

## Read this before you design anything

These are verified against the current ContainerLab/c9s and NetBox documentation.
Do not substitute remembered behaviour from older releases — the API changed
substantially in c9s 0.9 and several widely-copied patterns are now invalid.

### Fabric runtime: use `containerlab --runtime c9s`

ContainerLab can deploy a topology into Kubernetes via Clabernetes (c9s). The CLI
stays the same; the lab runs as cluster resources.

- **Requires Kubernetes 1.31+** and **Clabernetes 0.11+**. ContainerLab checks the
  cluster's published Node schema before its first cluster call and fails closed
  on older releases.
- Deploy: `containerlab --runtime c9s deploy -t topo.clab.yml`
- The `c9s` runtime supports the whole lifecycle: `deploy`, `destroy`, `inspect`,
  `exec`, `start`, `stop`, `restart`, `save`, `events`, `logs`, `validate`,
  `deploy --dry-run`, `deploy --emit-crs`.
- Devices are **direct Pod containers** — no nested Docker, no launcher Pods.
- **Invalid on c9s 0.9+**: `clabverter`, `LauncherProfile`, `disableExpose`,
  launcher-based workflows. `NodeProfile` and `exposeType: None` replaced them.
  If any guide you find mentions those, it is for an incompatible release.
- **Rejected by the compiler**: `bridge`, `ovs-bridge`, `host`, `ext-container`
  pseudo-nodes; `mgmt-net:` and `macvlan` endpoints; `cpu-set`; `stages`; link
  `labels`/`vars`.
- **Accepted with warnings** (they have no Kubernetes meaning): `mgmt.network`,
  `mgmt.bridge`, `mgmt.mtu`, `mgmt.external-access`, and pinned host-side ports.

### Two things that will otherwise cost you hours

**1. Startup configuration does not behave like local ContainerLab.**
c9s copies local startup-config files into per-node ConfigMaps and stages them at
the path the node expects. An **inline** `startup-config` is staged as a *partial*
configuration merged over the kind's default. Each staged file is capped at
**950 KB**. These are **snapshots taken at deploy time** — not mutable bind
mounts — so changing a source file requires another `deploy`.

**2. Persistence makes the device's saved config win.**
By default deploy enables Clabernetes persistence: each node's artifact volume is
a per-node PersistentVolumeClaim. The startup config seeds the node **once**;
after that the config the device saved wins, so a pod restart does not revert it.
For a demo that must reset to a known state on every rebuild, set
**`enforce-startup-config`** so the declared configuration wins on every boot.
Persistence also needs a dynamically provisionable StorageClass; `--no-persistence`
gives ephemeral storage where every pod replacement factory-resets the node.

> **This is the single most important setting for the demo.** Choose deliberately,
> and say which you chose and why. On the laptop version of this same fabric, the
> equivalent failure — startup config silently not applying — produced switches
> that booted with the default hostname and no eAPI, which reads as "the switch
> didn't start" rather than "the config went somewhere else". Assume the same
> class of failure is possible here and design a check that proves the config
> actually applied (see Gate 1).

### Management addressing and how anything reaches the switches

Every node gets an allocated management address, honouring the topology's `mgmt`
subnet and any static `mgmt-ipv4`. c9s 0.9 carries that shared subnet over a
**routed cross-Pod mesh with proxy ARP** — it is *not* a bridged L2 segment
between Pods. Consequences:

- Devices can reach each other's management addresses directly.
- **From outside the cluster**, the access paths are Kubernetes Services
  (LoadBalancer by default), Pod IPs, and DNS. Not the raw `172.x` management
  address.

This decides where Orb Agent goes. See the next section.

## The three design decisions to make, and defend

### Decision 1 — Where does Orb Agent run?

Orb Agent performs the discovery and streams to Diode over gRPC(S). It needs to
reach each switch's eAPI (Aruba/Arista-style HTTPS on 443) and SNMP.

- **Preferred: in-cluster, as a Pod**, on the lab namespace, reaching switches via
  their Service/DNS names or Pod IPs. Keeps the whole thing inside the cluster and
  matches the routed management mesh.
- **Fallback: outside the cluster**, if and only if you expose switches via
  LoadBalancer Services and the agent host can reach them. NetBox Labs' own
  guidance for network discovery favours running the agent where it can reach the
  devices directly, which for a cluster-internal fabric means in-cluster.

Pick one, implement it, and **state in your report which you chose and what you
verified** — do not implement both.

Note: do **not** confuse this with NetBox Labs' Kubernetes *inventory* integration
(`nbl_kubernetes` worker backend, `orb-agent-pro` image), which inventories the
cluster itself. That is a different feature and not what this task needs.

### Decision 2 — What do the switches discover *into*?

Diode ingests and Assurance compares. Orb Agent needs Diode client credentials
and its target.

- Get `DIODE_TARGET`, `DIODE_CLIENT_ID`, `DIODE_CLIENT_SECRET` from **NetBox UI →
  Diode → Client Credentials / Settings**. These are **not** the NetBox API token.
- Target is normally `grpcs://<instance>/diode` (443).
- Put them in a **Kubernetes Secret**, not in a ConfigMap or the topology file.

### Decision 3 — Who owns the switch images?

`ceos:4.32.0F` exists locally on the developer's machine. Arista cEOS-lab images
are not redistributable, so they must be **pushed to a registry the cluster can
pull from**, with an image pull secret if the registry is private.

- Determine the registry, push the image, and reference it by digest or tag in the
  topology.
- Use `deploy --image-pull-secret <name>` if the registry needs credentials; the
  secret must exist in the lab namespace (`regcred` by convention).
- **If you cannot obtain the image, stop and say so** rather than substituting a
  different NOS. The demo's config templates are EOS-specific.

## The fabric to build

Six Arista cEOS switches, EVPN/VXLAN, two MLAG pairs. This mirrors a fabric that
already runs locally, so the device names and addressing are fixed:

| Device | Role | Mgmt (static) | eAPI |
|---|---|---|---|
| `demo-spine-01` | spine | 172.31.30.11/24 | 443 |
| `demo-spine-02` | spine | 172.31.30.12/24 | 443 |
| `demo-leaf-01` | leaf | 172.31.30.21/24 | 443 |
| `demo-leaf-02` | leaf | 172.31.30.22/24 | 443 |
| `demo-leaf-03` | leaf | 172.31.30.23/24 | 443 |
| `demo-leaf-04` | leaf | 172.31.30.24/24 | 443 |

Links (12 total):

- `spine-01` eth1-4 → `leaf-01..04` eth1
- `spine-02` eth1-4 → `leaf-01..04` eth2
- MLAG peers: `leaf-01:eth3↔leaf-02:eth3`, `leaf-01:eth4↔leaf-02:eth4`,
  `leaf-03:eth3↔leaf-04:eth3`, `leaf-03:eth4↔leaf-04:eth4`

The `demo-` prefix is deliberate: it keeps this fabric distinct from another lab
documented in the same NetBox instance. Keep it.

### Bare vs full configuration

Build the **bare** state first. This is the point of the demo: NetBox is empty,
Discovery populates it, and only then does intent get pushed.

A bare switch needs only: hostname, `vrf instance MGMT`, `ip routing vrf MGMT`,
`Management0` address, eAPI over HTTPS, `lldp run`, SNMP RO community. **No BGP,
no VXLAN, no MLAG** — those are what the demo adds afterwards.

Two ordering traps that break EOS startup-configs. Both are real; the first one
cost real time on the laptop version:

- **`vrf instance MGMT` and `ip routing vrf MGMT` must appear *before* anything that
  references `vrf MGMT`.** EOS aborts the remainder of a startup-config when a
  command references a VRF that does not exist yet. The symptom is a switch that
  keeps its default hostname with no eAPI, looking unbooted.
- `username ... secret 0 <pw>` — keep the `0` for a plaintext secret.

Bare config template, per device (substitute hostname and address):

```
hostname demo-spine-01
!
no aaa root
!
username admin privilege 15 role network-admin secret 0 admin
!
vrf instance MGMT
ip routing vrf MGMT
!
management api http-commands
   protocol https
   no shutdown
   vrf MGMT
      no shutdown
!
interface Management0
   description OOB management
   vrf MGMT
   ip address 172.31.30.11/24
   no shutdown
!
ip routing
ip route vrf MGMT 0.0.0.0/0 172.31.30.1
!
lldp run
!
snmp-server community FABRIC-RO ro
snmp-server vrf MGMT
!
end
```

## Orb Agent policies

Two policies, targeting the six management addresses. Arista EOS is a NAPALM
driver (`eos`), which is what gives interface/VLAN/VRF/LAG detail.

```yaml
orb:
  config_manager:
    active: local
  backends:
    common:
      diode:
        target: ${DIODE_TARGET}
        client_id: ${DIODE_CLIENT_ID}
        client_secret: ${DIODE_CLIENT_SECRET}
        agent_name: netbox-demo-k8s-orb
    device_discovery:
    snmp_discovery:

  policies:
    device_discovery:
      demo_fabric_napalm:
        config:
          options:
            capture_running_config: true      # gives NetBox evidence of actual state
            sanitize_config: true             # strips secrets before ingest
            discover_vrfs: true
            create_unknown_vlans: true
            emit_prefix_vlan: svi-name
            emit_lag_membership: true
            port_scan_ports: [22, 80, 443]
            port_scan_timeout: 1.0
          defaults:
            site: netbox-demo
            role: switch
            tags: [netbox-demo, discovered]
            description: "Discovered by NetBox Discovery (netbox-demo)"
            interface_patterns:
              - match: "^Loopback"
                type: virtual
              - match: "^Management"
                type: 1000base-t
              - match: "^Vlan"
                type: virtual
              - match: "^Port-Channel"
                type: lag
              - match: "^Ethernet"
                type: 10gbase-x-sfpp
        scope:
          - driver: eos
            hostname: 172.31.30.11
            username: ${EOS_USER}
            password: ${EOS_PASS}
          # ... one entry per device: .12 .21 .22 .23 .24

    snmp_discovery:
      demo_fabric_snmp:
        config:
          defaults:
            site: netbox-demo
            role: switch
            tags: [netbox-demo, discovered]
        scope:
          targets:
            - host: 172.31.30.11
            - host: 172.31.30.12
            - host: 172.31.30.21
            - host: 172.31.30.22
            - host: 172.31.30.23
            - host: 172.31.30.24
          authentication:
            protocol_version: SNMPv2c
            community: ${SNMP_COMMUNITY}
```

Notes that matter for correctness (verified against `orb-agent` 2.17.0 on c9s):

- `config_manager.active` must be **`local`** and `policies:` must be nested
  under `orb:` (`active: simple` with top-level `policies:` is silently ignored
  by `orb-agent` 2.17.0 with `"no policies specified"`).
- `snmp_discovery` `targets:` requires objects (`- host: ...`), not bare strings.
- Options live under `config.options`, **not** `config.defaults`.
- `interface_patterns` keys are `match` and `type` (not `pattern`/`if_type`).
- SNMP auth uses `protocol_version`.
- **c9s Service ports:** expose **UDP 161** alongside TCP 443/22 so SNMP walks
  reach the switches.
- **c9s + cEOS discovery anchor (`Loopback0`):** keep the default `INTFTYPE` (do
  *not* set `INTFTYPE=Management`, which moves `Management0` into the device
  netns and breaks c9s `c9sr0` interposition) and declare a `Loopback0` IP on
  each switch so NAPALM `eos` has an IP-capable interface.
- **NetBox Assurance gate:** when `netbox_assurance_plugin` is enabled on NetBox
  Cloud, Diode ingestion writes entities into **Assurance → Deviations** rather
  than directly into `dcim.Device` until an operator reviews and applies them.
- If switches are only reachable via Service DNS names rather than the routed
  management addresses, use those names as `hostname` and adjust the SNMP targets
  to match. **Say which you did.**

## Gates — stop and report at each

Do not proceed past a gate that fails. Each one is a claim I expect you to have
actually checked, not inferred.

### Gate 0 — Preflight (report findings, then wait for a go/no-go)

Report, with the command you used:

- Kubernetes server version (must be ≥1.31)
- Whether Clabernetes ≥0.11 is installed; if not, the install plan
- Default StorageClass, and whether `ReadWriteOnce` PVCs will bind
- Whether a LoadBalancer implementation exists (or NodePort / none)
- Registry + credentials for the cEOS image, and confirmation the cluster can
  pull it
- Whether the `demo-` device names collide with anything already in the cluster
- Node resources: does any single node have headroom for 6 cEOS containers?
  (Give CPU/memory per node and your sizing estimate.)

**If the cluster cannot satisfy 1.31 / c9s ≥0.11 / storage, stop here** and give the
upgrade path. Do not work around it silently.

### Gate 1 — Fabric up, and the config actually applied

Deploy, then prove all of the following. This gate is specifically designed to
catch the silent "config didn't apply" failure:

- `containerlab --runtime c9s inspect` shows **6/6 nodes running**.
- For **each** device, eAPI on 443 answers and **`show hostname` returns the
  expected `demo-*` name**. A default hostname means the startup config did not
  apply — investigate before continuing.
- `show version` confirms the expected cEOS version.
- `show ip interface brief vrf MGMT` shows the intended management address.
- eAPI is reachable **from wherever you decided Orb Agent runs** (not just from
  your workstation) — prove it from that network position.
- Re-running deploy does not duplicate or break nodes (idempotency).

Provide the commands and their real output.

### Gate 2 — Discovery ingests into NetBox Cloud

- Orb Agent pod is Running and its logs show a successful Diode connection
  (no auth errors, no TLS failures).
- Run discovery once, synchronously, and report the exit state.
- In NetBox Cloud: confirm the **6 devices appear** with interfaces, IPs, and the
  site/role/tags from the policy defaults.

### Gate 3 — Repeatable lifecycle

- `destroy` removes the lab's resources **and** its managed namespace; confirm no
  orphaned Nodes, PVCs, or namespaces remain.
- A clean `deploy` afterwards reaches Gate 1 again.
- Provide the two scripts (or Makefile targets) you used.

## Things that will look like bugs and are not

Record which of these you hit; they are worth knowing for the demo.

- **Switch boots with default hostname / no eAPI** → startup config did not apply.
  Check `enforce-startup-config`, the 950 KB staging limit, and VRF ordering.
- **Config change had no effect** → staged startup files are deploy-time
  snapshots. Deploy again.
- **Switch reverted to saved config after a pod restart** → that is persistence
  working as designed; use `enforce-startup-config` if you want declared state to
  win.
- **`show bgp evpn summary` fails** → expected on a bare switch. There is no BGP
  process yet, and that is the point. Don't report the switch as broken.
- **eAPI refuses connections for ~60s after a node starts** → EOS is still coming
  up. Retry before concluding anything.
- **`mgmt.network` / `mgmt.bridge` warnings** → those fields have no Kubernetes
  meaning; c9s ignores them.
- **Management address unreachable from your laptop** → expected. Use the Service
  (LoadBalancer/port-forward) or test from inside the cluster.

## Constraints

- **Do not** put credentials in a ConfigMap, the topology file, or git. Use
  Kubernetes Secrets.
- **Do not** substitute a different network OS if cEOS is unavailable — stop and
  say so.
- **Do not** change the device names, the `demo-` prefix, or the management
  addressing. The cloud-side plugin and its rendered configs depend on them.
- **Do not** install or configure NetBox itself. The cloud instance already
  exists; you are only feeding it.
- **Do not** claim a gate passed without pasting the command and its output. If
  something is untested, say it is untested.

## Out of scope

- Pushing full EVPN/VXLAN intent to the switches. That is done from NetBox Cloud
  after discovery, and is the next phase.
- NetBox Assurance deviation workflow — it runs in the cloud.
- Cluster provisioning, CNI changes, or installing a LoadBalancer implementation.

## Report format

For each gate: **claim → command → output → pass/fail**. Then:

- The three decisions you made, with your reasoning.
- Everything you could not verify, stated plainly.
- The exact file tree you created, and the one command that rebuilds from nothing.
