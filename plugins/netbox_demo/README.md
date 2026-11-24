# netbox-demo

Run an Arista EVPN/VXLAN ContainerLab fabric **from NetBox Cloud**.

NetBox Cloud holds the source of truth, the plugin and the drift review. Your
laptop holds the switches. NetBox Discovery finds the switches before NetBox
knows anything about them, and NetBox Assurance is what tells you whether the
fabric still matches what you documented.

## The shape of it

```
  NetBox Cloud  (source of truth, plugin, Assurance, Diode)
        │  ▲
        │  │  discovery, outbound only (Orb Agent → Diode)
        │  ▼
        │  lab actions over Tailscale, Bearer token, fixed paths
        ▼
  your lab host
    lab-agent      allowlisted control surface, holds the Docker socket
    ContainerLab   6 × Arista cEOS
    orb-agent      NetBox Discovery
```

NetBox Cloud never holds Docker credentials and never reaches a switch directly.
Three things follow from that, and they are the reason for the design:

1. **Discovery runs in the direction it should.** The switches are discovered and
   streamed into NetBox, so the demo starts from an empty NetBox and populates it.
2. **Cloud → lab is a narrow channel.** `lab-agent` exposes eight fixed actions
   with no path or command parameters. A compromised cloud instance can ask for
   those eight things and nothing else.
3. **Drift has two honest sources.** Assurance compares discovered reality against
   documentation; the Day 2 tab compares rendered intent against a live
   running-config. They answer different questions and the UI keeps them apart.

## What you need

| Thing | Why |
|---|---|
| NetBox Cloud with Assurance and Diode | the cloud half |
| Tailscale on the lab host and reachable from the cloud | the private path between them |
| Docker with the `ceos:4.32.0F` image imported | the switches |
| ContainerLab | run by the compose stack |
| A Diode client id and secret | Discovery's way in |

## Setup

**1. Local lab host**

```bash
cp .env.example .env         # then fill it in
cd local-lab
docker compose up -d lab-agent
curl -s http://localhost:8085/ping     # {"ok": true, ...}
```

Bind the agent to your Tailscale address so it is not listening on the local LAN:

```bash
LAB_AGENT_BIND_IP=100.89.183.114   # your tailscale ip -4
```

**2. NetBox Cloud**

Install the wheel and enable it. `lab_agent_token` must equal `LAB_AGENT_TOKEN`
on the lab host, or every call is refused with a 401.

```python
PLUGINS = ["netbox_demo"]
PLUGINS_CONFIG = {
    "netbox_demo": {
        "lab_agent_url": "http://100.89.183.114:8085",
        "lab_agent_token": "<same value as LAB_AGENT_TOKEN>",
        "site_slug": "netbox-demo",
    },
}
```

**3. Check the wiring**

```bash
curl -s -H "Authorization: Token $NETBOX_CLOUD_TOKEN" \
  https://<instance>.cloud.netboxapp.com/api/plugins/demo/health/ | jq
```

`lab_reachable: true` means the cloud can see the agent. If it is false, the
response says whether it is configuration or connectivity.

## The runbook

Open **NetBox Demo → Runbook** and work down the page. Each step does one thing
and records what happened.

| Step | What it does |
|---|---|
| Destroy the lab | Tears the fabric down and wipes persisted cEOS flash |
| Rebuild it bare | Six switches with management, eAPI, SNMP and LLDP only |
| Discover them into NetBox | Orb Agent reads the switches → Diode → NetBox |
| Document the fabric | Cabling, tenants, VLANs and the service baseline |
| Render and push | EOS config built from NetBox intent, applied to six switches |
| Check the fabric is healthy | BGP EVPN, VXLAN, MLAG and VLAN state from every switch |
| Confirm there is no drift | Assurance review |
| Show drift being caught | Unauthorized changes injected, then found |

The bare step matters: those switches have no fabric intent at all. Whatever
NetBox ends up knowing about them arrived through discovery or was documented
afterwards.

## Drift, honestly

Two different checks, deliberately not merged:

**Assurance** compares what discovery observed with what NetBox documents. It
covers inventory and attributes, and *it is where an operator approves a change*.
This plugin only reads it and links to it.

**The Drift tab** compares this plugin's rendered intent with a live
`show running-config`: VLANs, SVI addresses, server-port state, VXLAN mappings,
MLAG membership and the service baseline. It shows what would be pushed and why,
before anything is pushed.

Where Assurance is not installed, the UI says so rather than reporting zero
deviations. "Nothing found" and "nothing looked" are different answers.

## Day 2

**NetBox Demo → Day 2 changes** stages a change in two steps, always:

1. **Save to NetBox only** — writes the source of truth, pushes nothing. The
   switches now disagree with intent, which is the point: open Drift and see it.
2. **Approve and apply** — requires a named approver, then writes NetBox and
   pushes to the switches.

There is no path that changes a switch without a preview and a name attached.

## Safety

- No inbound firewall ports; the agent is reached over Tailscale.
- Eight fixed actions, no parameterised paths, no shell interpolation.
- Bearer token compared in constant time; empty token fails closed.
- Device names and commands are checked against fixed allowlists.
- Day 2 writes are limited to `Ethernet6`, VLANs and the service baseline.
- NetBox cleanup is scoped to the `netbox-demo` site and its own tag.

## Layout

```
plugin/netbox_demo/     the NetBox plugin (runs in the cloud)
  netbox_demo/
    design.py           the fabric plan: addressing, ASNs, tenants
    fabric.py           the shape of one device's intent
    renderer.py         intent -> EOS text (pure)
    intent.py           NetBox -> intent
    seeder.py           idempotent NetBox seeding and scoped cleanup
    day2.py             Day 2 planners and the drift detector
    lab.py              the only module that talks to lab-agent
    assurance.py        reads Assurance, never writes it
    views.py, urls.py, navigation.py, api/
local-lab/              the lab host
  lab-agent/            the control surface (contract, agent, tests)
  orb-agent/            NetBox Discovery policies
  clab/                 ContainerLab topology
ansible/roles/          the original Arista Jinja2 templates (reference)
sdd/initiatives/        the initiative and specs this was built from
```

## Tests

```bash
python -m pytest plugin/netbox_demo/tests -q      # design, renderer, Day 2, drift
python -m pytest local-lab/lab-agent -q           # auth, allowlists, commands
```

Both suites run with no NetBox, no Docker and no switches, because those are the
parts that can regress without anyone noticing. 107 tests.

Then the two things a unit test cannot see — that the plugin loads in a real
NetBox and that the switches actually come up:

```bash
# 1. Plugin loads, every page renders, seeding/Day 2/cleanup work.
docker compose -f harness/docker-compose.yml up -d --build
docker compose -f harness/docker-compose.yml exec -T netbox \
  python /opt/netbox/netbox/manage.py migrate netbox_demo
docker compose -f harness/docker-compose.yml exec -T netbox \
  python /opt/netbox/netbox/manage.py shell < harness/check.py
# expect: 37/37 checks passed

# 2. The same checks against the built wheel, not the source tree.
cd plugin/netbox_demo && python -m build --wheel && cd ../..
docker compose -f harness/docker-compose.wheel.yml -p wheelcheck up -d --build
docker compose -f harness/docker-compose.wheel.yml -p wheelcheck exec -T netbox \
  python /opt/netbox/netbox/manage.py shell < harness/check.py
```

Harness state: NetBox 4.6.2 (the version NetBox Cloud runs), on ports 8899 and
8898 so both can run at once.

## Verified, not assumed

Both suites were run against real infrastructure, not just written:

- **NetBox 4.6.2**: 37/37 harness checks, from the source tree *and* from the
  built wheel. Every page renders, the API answers, seeding is idempotent,
  Day 2 stages and saves, cleanup is scoped.
- **A real 6-switch cEOS fabric**: destroy, deploy, boot, eAPI read, fabric
  check — driven entirely through `lab-agent` over HTTP with a bearer token.

Four bugs were found by running it and are fixed:

| Bug | What it looked like |
|---|---|
| ContainerLab's startup-config copy lands in a path the daemon resolves on the host | Switches booted unnamed, with no eAPI — as if they had not started |
| `vrf MGMT` referenced before `vrf instance MGMT` | EOS aborted the rest of the startup config |
| NetBox 4.6 renamed interface-type constants and requires peppered API tokens | Seeding failed with `AttributeError`; token creation raised `ValueError` |
| NetBox 4.6 replaced `a_terminations` with `terminations` | Scoped cleanup crashed on cable deletion |

