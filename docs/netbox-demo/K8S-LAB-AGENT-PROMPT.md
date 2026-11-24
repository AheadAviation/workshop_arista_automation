# Agent prompt: Deploy `lab-agent` in Kubernetes for plugin-driven control

Paste the block below into your Kubernetes agent (working in
`~/k8/home_k3/demos/netbox-demo-fabric`).

---

## Mission

Deploy **`lab-agent`** in-cluster alongside the existing `netbox-demo-fabric`
cEOS switches and persistent **Orb Agent**, so the `netbox_demo` plugin in
NetBox Cloud (`https://faxh1287.cloud.netboxapp.com`) can drive the entire demo
lifecycle over HTTPS + Bearer token:

- **Health** (`GET /health`) — 6/6 cEOS pods + persistent Orb Agent status
- **Destroy** (`POST /actions/destroy-lab`) — runs the existing fabric destroy
  script **without removing Orb Agent or `lab-agent`**
- **Deploy Bare** (`POST /actions/deploy-bare-lab`) — runs the existing bare
  fabric deploy script (~93s rebuild, 6/6 switches with `demo-*` hostnames +
  `Loopback0` + eAPI)
- **Discover** (`POST /actions/run-discovery`) — triggers an immediate Orb Agent
  discovery sweep into Diode (persistent Orb stays running in-cluster)
- **Push Configs / Validate / eAPI / Rogue Drift** — talks directly to the 6
  switches over their in-cluster Service DNS (`demo-*-eapi`) on HTTPS 443

## Source files

Build from `local-lab/lab-agent/` in `netbox-demo`:

- `contract.py` — allowlisted devices (`demo-spine-01..02`, `demo-leaf-01..04`),
  constant-time Bearer auth, eAPI command allowlist, rogue drift scenarios
- `agent.py` — HTTP service with `LAB_BACKEND=k8s` support
- `Dockerfile.k8s` — Python 3.12 + `requests` + `kubectl` + `containerlab`

## Configuration (env vars)

Run `lab-agent` with:

| Env var | Value |
|---|---|
| `LAB_BACKEND` | `k8s` |
| `LAB_AGENT_PORT` | `8085` |
| `LAB_AGENT_TOKEN` | From Kubernetes Secret (backed by Vault — never in git/ConfigMap) |
| `K8S_NAMESPACE` | Namespace containing the 6 cEOS pods |
| `K8S_ORB_NAMESPACE` | Namespace containing the persistent Orb Agent Deployment |
| `K8S_ORB_DEPLOYMENT` | Name of the persistent Orb Agent Deployment |
| `K8S_EAPI_HOST_TEMPLATE` | Service DNS template matching your existing eAPI Services (default: `{device}-eapi.{namespace}.svc.cluster.local`) |
| `K8S_DEPLOY_CMD` | Command/script to deploy the bare fabric (mounted from ConfigMap/repo) |
| `K8S_DESTROY_CMD` | Command/script to destroy the fabric only (must NOT delete Orb Agent or `lab-agent`!) |
| `K8S_DISCOVERY_CMD` | Optional override; if omitted, `lab-agent` runs `kubectl rollout restart` + `rollout status` on `K8S_ORB_DEPLOYMENT` for an immediate sweep |
| `EOS_USER` / `EOS_PASS` | Switch eAPI credentials (from Secret) |

## Namespace & RBAC design (critical)

**Do NOT put `lab-agent` or `orb-agent` inside a namespace that `destroy` deletes.**
Either:
- Keep `lab-agent` + `orb-agent` in a persistent control namespace (e.g.
  `netbox-demo-control`) while `c9s` creates/destroys the fabric namespace, **or**
- Scope `destroy` to delete only the c9s Topology/cEOS resources, leaving
  `lab-agent`, `orb-agent`, and their Secrets intact.

Grant `lab-agent`'s ServiceAccount least-privilege RBAC for:
1. `pods` (`get`, `list`) in the fabric & Orb namespaces
2. `deployments` (`get`, `patch`) for `K8S_ORB_DEPLOYMENT` in `K8S_ORB_NAMESPACE`
3. Whatever `containerlab --runtime c9s` needs for `deploy`/`destroy` of the
   `netbox-demo-fabric` topology (c9s CRDs, ConfigMaps, Services, PVCs in the
   fabric namespace)

## Reachability from NetBox Cloud

NetBox Cloud (`faxh1287.cloud.netboxapp.com`) must reach `lab-agent` over HTTPS
on a stable URL (e.g. Cloudflare Tunnel, Tailscale Funnel, or existing cluster
Ingress with TLS). Every endpoint except `GET /ping` requires
`Authorization: Bearer <LAB_AGENT_TOKEN>`.

## Gates — stop and report at each

### Gate A — In-cluster `lab-agent` healthy
- `GET /ping` returns `200 {"ok": true, ...}`
- `GET /health` (with Bearer token) returns `200` with `backend: "k8s"`,
  `running_devices: 6`, `orb.running: true`
- `POST /actions/validate-fabric` returns `200` with `6/6` switches online and
  reporting their `demo-*` hostnames via eAPI

### Gate B — Full lifecycle via `lab-agent` API
- `POST /actions/destroy-lab` destroys the 6 switches while `lab-agent` and
  `orb-agent` stay `1/1 Running`
- `POST /actions/deploy-bare-lab` rebuilds 6/6 switches cleanly
- `POST /actions/validate-fabric` returns `6/6` online after boot
- `POST /actions/run-discovery` triggers a fresh Orb sweep into Diode

### Gate C — External HTTPS reachability for NetBox Cloud
- From outside the cluster, `GET https://<public-url>/ping` returns `200`
- Unauthenticated `GET https://<public-url>/health` returns `401`
- Authenticated `GET https://<public-url>/health` returns `200` with `6/6`
- Report the public URL (and confirm `LAB_AGENT_TOKEN` is stored in Vault, not git)
