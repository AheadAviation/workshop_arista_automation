# Agent prompt: Deploy NetBox Enterprise + `netbox_demo` plugin + `lab-agent` in `home_k3`

Paste the block below into your Kubernetes agent (working in
`~/k8/home_k3/demos/netbox-demo-fabric`).

> **Security note:** Do **NOT** paste the 1Password link, `license.yaml`, or
> Replicated download token into git. Store `license.yaml` and all tokens in
> Vault and inject them via Kubernetes Secrets / ExternalSecrets.

---

## Mission

Deploy **NetBox Enterprise** (with **Diode**, **Assurance**, and the custom
**`netbox_demo` plugin**) and **`lab-agent`** into the existing `home_k3`
Kubernetes cluster alongside the running 6-switch cEOS fabric and persistent
**Orb Agent**, so the entire demo runs closed-loop inside one cluster with zero
external tunnels.

```
┌────────────────────────────────────────────────────────────────────┐
│  home_k3 Kubernetes Cluster                                        │
│                                                                    │
│  [netbox-enterprise ns]                                            │
│   • NetBox Enterprise (Helm) + Diode + Assurance + netbox_demo     │
│        │                                      ▲                    │
│        │ cluster-internal HTTP + Bearer token │ in-cluster Diode   │
│        ▼                                      │                    │
│  [netbox-demo-control ns]  (NEVER deleted by destroy-lab)          │
│   • lab-agent (LAB_BACKEND=k8s, port 8085)                         │
│   • orb-agent (persistent Deployment)                              │
│        │                                                           │
│        │ Service DNS (demo-*-eapi:443 & UDP 161)                   │
│        ▼                                                           │
│  [netbox-demo-fabric ns]   (created/destroyed on demand by c9s)    │
│   • 6× Arista cEOS switches (demo-spine-01..02, demo-leaf-01..04)  │
└────────────────────────────────────────────────────────────────────┘
```

## Critical installation rule

- **Use the Existing Cluster (Helm) install path** from the NetBox Enterprise
  self-service portal — **do NOT run the `embedded/netbox-enterprise` binary**
  (`sudo ./netbox-enterprise install`) on a `home_k3` node (Embedded Cluster
  installs k0s and conflicts with k3s).
- Pull `license.yaml` and Helm registry credentials from Vault (I will provide
  `license.yaml` / Helm pull credentials out-of-band; never commit them to git).

## Sources to pull from GitHub

Repository: `git@github.com:AheadAviation/workshop_arista_automation.git`
Branch: **`feat/netbox-demo-plugin`**

1. **Custom NetBox plugin:** `plugins/netbox_demo/`
   - Pip install spec:
     `git+https://github.com/AheadAviation/workshop_arista_automation.git@feat/netbox-demo-plugin#subdirectory=plugins/netbox_demo`
     (or build a `.whl` with `python -m build` and bake it into the NetBox
     Enterprise custom plugin image / custom packages config).
   - Required `PLUGINS` & `PLUGINS_CONFIG`:
     ```python
     PLUGINS = ["netbox_demo"]  # appended to NetBox Enterprise's enabled plugins
     PLUGINS_CONFIG = {
         "netbox_demo": {
             "lab_agent_url": "http://lab-agent.netbox-demo-control.svc.cluster.local:8085",
             "lab_agent_token": "<from-Vault-secret>",
             "site_slug": "netbox-demo",
             "assurance_deviations_url": "/plugins/assurance/deviations/",
             "verify_tls": False,
             "eapi_vrf": "",
             "document_mgmt_interface": False,
         }
     }
     ```
2. **In-cluster `lab-agent`:** `local-lab/lab-agent/` (`agent.py`, `contract.py`, `Dockerfile.k8s`)
   - Full `lab-agent` env var & RBAC spec is in `docs/netbox-demo/K8S-LAB-AGENT-PROMPT.md`
     on the same branch.

## Gates — stop and report at each

### Gate 0 — Preflight (report findings, then wait for go/no-go)
Report with actual command output:
- `kubectl get nodes -o wide` and available CPU / RAM per node (NetBox Enterprise
  + Postgres + Redis + Diode + Assurance typically needs 4–8 vCPU and 8–16 GB RAM
  headroom on top of the 6 cEOS switches)
- Available StorageClass(es) supporting `ReadWriteOnce` PVCs
- Ingress controller / TLS / DNS name to expose the NetBox Enterprise UI on the LAN
- Confirmation of namespace layout so `destroy-lab` **never** touches
  `netbox-enterprise`, `lab-agent`, or `orb-agent`

### Gate 1 — NetBox Enterprise running via Helm
- Deploy NetBox Enterprise via Helm into `netbox-enterprise` namespace with
  **Diode** and **Assurance** enabled
- Confirm NetBox UI is reachable over HTTPS on the LAN and `/api/status/` lists
  `netbox_diode_plugin` and `netbox_assurance_plugin`

### Gate 2 — `netbox_demo` plugin + `lab-agent` wired in-cluster
- Build & deploy `lab-agent` (`Dockerfile.k8s`, `LAB_BACKEND=k8s`) into
  `netbox-demo-control` namespace with:
  - `LAB_AGENT_TOKEN` stored in Vault + mounted as a Secret
  - `K8S_DEPLOY_CMD` / `K8S_DESTROY_CMD` wired to the existing fabric scripts
  - `K8S_ORB_NAMESPACE` / `K8S_ORB_DEPLOYMENT` wired to the persistent Orb Agent
  - Least-privilege RBAC for fabric pods, Orb rollout restart, and c9s deploy/destroy
- Install/enable `netbox_demo` in NetBox Enterprise, run `manage.py migrate netbox_demo`
- Verify from inside NetBox Enterprise:
  - `GET /api/plugins/demo/health/` returns `200` with `configured: true`,
    `lab_reachable: true`, `running_devices: 6`, `orb.running: true`
  - Sidebar shows **NetBox Demo** (`Runbook`, `Drift`, `Day 2 changes`,
    `Config preview`, `Live config`, `Jobs`)

### Gate 3 — Repoint persistent Orb Agent to in-cluster NetBox Enterprise Diode
- Create a Diode client in NetBox Enterprise, store its secret in Vault
- Update persistent `orb-agent` to target NetBox Enterprise's in-cluster Diode
  endpoint (`active: local`, nested `orb.policies`, `targets: [{host: ...}]`)
- Confirm Orb logs show `Successfully ingested` for all 6 switches and
  **Assurance → Deviations** shows the discovered `demo-*` devices

### Gate 4 — Full closed-loop lifecycle test
- Trigger `destroy-lab` → `deploy-bare-lab` → `validate-fabric` → `run-discovery`
  through `lab-agent` (or `/plugins/demo/`) and confirm:
  - NetBox Enterprise, `lab-agent`, and `orb-agent` stay `1/1 Running` throughout
  - All 6 switches rebuild cleanly (~93s) and rediscover into Assurance
  - Git tree scanned clean — zero licenses, tokens, or secrets committed
