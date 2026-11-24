Research type: technical_analysis\
Subject: Laptop web GUI for the NetBox and dual-DC AVD workshop demo\
Date: 2026-11-16\
Requestor: workshop operator\
Identifier: RSCH-001\
Access scope: FULL\

## 1. Findings

Scores are integers from 1 to 5. Weighted score = (demo clarity × 0.30) + (workshop fit × 0.25) + (laptop burden × 0.20) + (security × 0.15) + (NAF practice × 0.10). Higher is better on every column, including burden (less to keep running scores higher).

| Option | Demo clarity (0.30) | Workshop fit (0.25) | Laptop burden (0.20) | Security (0.15) | NAF practice (0.10) | Weighted |
| --- | --- | --- | --- | --- | --- | --- |
| A. Extend the NetBox ZTP portal into the workshop console | 4 [2] | 4 [6] | 4 [8] | 2 [7] | 3 [11] | 3.60 |
| B. Add AWX as the job runner, with a thin status page | 3 [16] | 2 [1] | 2 [18] | 2 [17] | 3 [15] | 2.40 |
| C. Containerlab web topology UI, plus a small NetBox/day-2 orchestrator | 3 [19] | 3 [5] | 2 [20] | 2 [20] | 3 [15] | 2.65 |
| D. Purpose-built demo dashboard that calls `run.ps1` / the playbooks, no AWX | 5 [1] | 5 [5] | 4 [8] | 3 [8] | 5 [12] | 4.50 |

Disconfirmation did not overturn D: the portal Deploy action only writes a custom field, the host agent stops after generate, topology, containerlab deploy, and apply, and day-2 plus `dualdc` remain separate non-interactive commands [1][7][11][12]. D (4.50) is the control plane for one customer sitting because `run.ps1` already names import, generate, deploy, apply, diff, push, test, backup, rollback, day-2, and a dual-DC switch that leaves the NetBox fabric alone [1], Jinja generation reads NetBox [9], and `dualdc build` renders the dual-DC track from that track's own AVD playbooks [1][5]. `playbooks/generate_configs_avd.yml` is a separate single-fabric AVD path (`hosts: FABRIC`, output under `avd/intended`); `dualdc build` does not call it [10]. A (3.60) keeps the visitor inside NetBox, which is the Jinja-track source of truth [2][8]. The portal can design a site [6]. A deploy click writes `ztp_deploy_status=requested` [7], and the host agent then runs generate, topology, containerlab deploy, and apply [11]. Day-2 and the dual-DC track stay outside that agent. B (2.40) is a separate web UI, REST API, and task engine whose job templates and surveys pass extra variables into playbooks [16][17]; from AWX 18.0 the operator is the preferred install and the Docker path is a development install with no published release [18], and this compose file has no AWX service [8]. C (2.65) matches the lab picture: `containerlab graph` serves a NeXt UI from the topology file (default `:50080`, `--offline` when containers are not inspected) [19], and the runner image already installs containerlab [13], while the Containerlab Web GUI is another container that logs in to `clab-api-server` with a Linux user and does not run labs itself [20]. NAF names six blocks besides the network: Intent, Observability, Orchestration, Executor, Collector, and Presentation [15]. The demo’s Intent is NetBox desired state on the Jinja track [2] and AVD group_vars on the dual-DC track [4][5]; vendor configs are derived from that intent [9]; `diff` is the dry run before a push [1][22]; `test` reads live BGP, MLAG, and VXLAN state [2]; `day2` revert is the compensating step back to the saved NetBox values [12]. Presentation may be a dashboard and is not required to be the only pane [15]. Security stays a labeled lab: `run.ps1 up` prints `admin` / `admin`, compose sets `LOGIN_REQUIRED` false, and the portal API then skips its session check [1][7][8]. D adds no second control-plane login. AWX is itself a web UI and REST API [17]. The Containerlab Web GUI logs in to `clab-api-server` as a Linux user on that host [20]. Neither cited page describes a separate password vault; the lower security scores are for the extra login surface, not for a vault those pages do not document. Shifting demo clarity from 0.30 to 0.20 and laptop burden from 0.20 to 0.30 yields D 4.40, A 3.60, C 2.55, B 2.30, so the ranking does not flip.

## 2. Confidence

GRADE band: Moderate.

The order D, then A, then C, then B is anchored by files in this workshop: the command list, the portal deploy queue, the agent’s four steps, the two generate playbooks, and the dual-DC boundary [1][5][7][9][10][11]. Those internal claims are high confidence. The 0.10 NAF column uses one project’s framework text, and the AWX and Containerlab claims each come from that vendor’s own docs, so those external claims are Low confidence on the evidence rule (a single publisher). The numeric scores are judgments on top of those facts. A second reviewer could move A’s demo-clarity score by one point without changing the winner.

## 3. Verification

First independent pass: FAIL on citation accuracy (uses of [6], [10], [12], [17], and [20]). Scope reconciliation and the Moderate band passed. Those uses were corrected in this revision. Second pass, a fresh verifier re-reading only the corrected claims against the workshop files: PASS. That second pass did not re-fetch every external URL.

## 4. Recommended next steps

1. Add `scripts/demo_console.py` and a `demo` arm in `run.ps1` beside the existing switch. Bind it to localhost. Each button runs one existing command only: `import`, `generate`, `deploy`, `apply`, `diff`, `push`, `test`, `backup`, `rollback`, `day2`, and `dualdc`. Show that command’s stdout on the page. Do not accept a free-form shell string.
2. On the NetBox track, link to `http://localhost:8888` and to `/plugins/ztp/` for New Site design only. After `generate`, list `output/configs/*.cfg`. Show `scripts/config_diff.ps1` output (the `diff` command) before `push` can be clicked. Day-2 `apply` edits NetBox, then prints `generate`, `diff`, `push`, and `test` [12]. `revert` restores the saved NetBox values and then prints those same four commands. Revert is not the last printed step [12].
3. Give the dual-DC track its own panel that calls `run.ps1 dualdc build`, `start`, `apply`, and `stop`. Label its intent as the AVD group_vars under `labs/l3ls-dual-dc`, per `labs/l3ls-dual-dc/WORKSHOP.md` [5]. Leave `playbooks/generate_configs.yml` and NetBox on the other panel [4][9].
4. Add a side view with `containerlab graph --offline` from the existing `clab` service, once per topology file: `clab/topology-docker.clab.yml` (mounted at `/clab/topology/` in that service) and `labs/l3ls-dual-dc/clab/topology.clab.yml` (mounted at `/clab/dual-dc/`) [8][19]. Keep the Containerlab Web GUI and `clab-api-server` out of `docker-compose.yml`. The graph is the lab picture; NetBox and the AVD group_vars remain the two intent stores [2][5].
5. On the console, show the lab banner `run.ps1 up` already prints (`admin` / `admin`) and that `LOGIN_REQUIRED` is false [1][8]. Leave `plugins/netbox_ztp_portal/netbox_ztp_portal/api/views.py` `_auth_error` as the place a later lab would turn the portal session check back on [7].

## 5. References

[1] `C:\Users\BrianNelson\Projects\workshop_arista_automation\run.ps1` lines 28–69 (command list and `dualdc`), 91–99 (`up`, `admin` / `admin`), 121–134 (`import`, `generate`), 154–167 (`deploy`, `agent`), 201–232 (`day2`, `diff`, `push`, `test`, `backup`, `rollback`), 319–384 (`dualdc` build/start/apply/stop).

[2] `C:\Users\BrianNelson\Projects\workshop_arista_automation\wiki\Home.md` lines 39–64 (pipeline from `fabric_topology.yml` through NetBox, Jinja, Containerlab, push, and `test_all.yml`) and lines 72–79 (source of truth, validation, day 2).

[3] `C:\Users\BrianNelson\Projects\workshop_arista_automation\wiki\Architecture.md` lines 184–192 (documented services `netbox`, `postgres`, `redis`, `ansible`, `clab`).

[4] `C:\Users\BrianNelson\Projects\workshop_arista_automation\wiki\Dual-DC-L3LS.md` lines 1–11 (AVD track kept beside NetBox; dual-DC is not generated from NetBox) and lines 15–23 (`dualdc` commands).

[5] `C:\Users\BrianNelson\Projects\workshop_arista_automation\labs\l3ls-dual-dc\WORKSHOP.md` lines 1–14 (two sources of truth; Ansible container is not on the lab management network; host `docker exec` pushes configs) and lines 18–27 (build/start/apply/stop).

[6] `C:\Users\BrianNelson\Projects\workshop_arista_automation\plugins\netbox_ztp_portal\README.md` lines 10–18 (New Site, New Block, fleet tracking), 27–40 (Build in NetBox, then `generate`, topology script, `deploy`, `apply` still on the CLI), 52–60 (workshop API token and `containerlab` tag).

[7] `C:\Users\BrianNelson\Projects\workshop_arista_automation\plugins\netbox_ztp_portal\netbox_ztp_portal\api\views.py` lines 31–37 (`_auth_error` allows the call when `LOGIN_REQUIRED` is false) and lines 132–163 (`DeployView` sets `ztp_deploy_status` to `requested` for the host agent).

[8] `C:\Users\BrianNelson\Projects\workshop_arista_automation\docker-compose.yml` lines 20–82 (`netbox`, including `SUPERUSER_PASSWORD` `admin` and `LOGIN_REQUIRED` false), 84–110 (`netbox-worker`), 115–151 (`postgres`, `redis`), 156–191 (`ansible`), 196–210 (`clab`, privileged, host network, docker socket, both topology mounts). The static API token value is present in this file and is not repeated here.

[9] `C:\Users\BrianNelson\Projects\workshop_arista_automation\playbooks\generate_configs.yml` lines 1–6 and 14–39 (Jinja generation from NetBox via role `arista_config`, output `output/configs`).

[10] `C:\Users\BrianNelson\Projects\workshop_arista_automation\playbooks\generate_configs_avd.yml` lines 1–17 and 21–52 (`hosts: FABRIC`, `arista.avd.eos_designs` and `eos_cli_config_gen`, output under `avd/intended`). `run.ps1 dualdc build` calls `labs/l3ls-dual-dc/playbooks/build.yml` and `build-frr.yml`, not this playbook [1].

[11] `C:\Users\BrianNelson\Projects\workshop_arista_automation\scripts\ztp_agent.py` lines 143–179 (`deploy_site`: `generate_configs.yml`, `generate_clab_topology_docker.py`, `containerlab deploy`, then `apply_configs`; status ends at `deployed`).

[12] `C:\Users\BrianNelson\Projects\workshop_arista_automation\scripts\day2_demo.py` lines 1–6 (NetBox edit, revert restores NetBox, then generate, diff, push, validate) and lines 190–194 (those four commands).

[13] `C:\Users\BrianNelson\Projects\workshop_arista_automation\docker\clab-runner\Dockerfile` lines 37–49 (installs the `containerlab` package and runs `containerlab version`).

[14] Network Automation Forum, “About — Network Automation Forum,” https://networkautomation.forum/about, retrieved 2026-11-16. The page expands NAF as the Network Automation Forum, a community for automation, orchestration, and observability. Confidence: Low (single site).

[15] Network Automation Forum, “The NAF Framework,” https://github.com/Network-Automation-Forum/reference/blob/main/docs/Framework/Framework.md, raw file retrieved 2026-11-16. Six blocks: Intent (structured desired state, API, neutral model derived into vendor config), Observability (persist actual state; events when actual and intended differ), Orchestration (coordinate blocks, no direct device access, compensating actions, dry run), Executor (writes, dry run, idempotent), Collector (read live data), Presentation (authentication and authorization required; a GUI is allowed; a single pane of glass is not required). Same publisher as [14], so this external claim stays Low confidence.

[16] Ansible, “20. Job Templates,” AWX 24.6.1 user guide, https://docs.ansible.com/projects/awx/en/24.6.1/userguide/job_templates.html, retrieved 2026-11-16. A job template is the definition and parameters for running an Ansible job; surveys on Run or Check jobs set extra variables through questions. Confidence: Low (single vendor).

[17] Ansible, AWX `README.md`, https://github.com/ansible/awx/blob/devel/README.md, retrieved 2026-11-16. AWX is a web UI, REST API, and task engine on Ansible, and an upstream for Red Hat Ansible Automation Platform. The same file states the last release of that repository was 2024-07-02 and that releases are paused during a refactor. Confidence: Low (project’s own README).

[18] Ansible, AWX `INSTALL.md`, https://github.com/ansible/awx/blob/devel/INSTALL.md, retrieved 2026-11-16. From AWX 18.0 the AWX Operator is the preferred install. Docker is described as an alternative for development and test only, with no official published release. Confidence: Low (same publisher as [17]).

[19] Srl-labs, “graph command,” https://containerlab.dev/cmd/graph/, retrieved 2026-11-16. `containerlab graph -t <topology>` serves an HTML NeXt UI; `--srv` defaults to `:50080`; `--offline` graphs the topology file without inspecting running containers. Confidence: Low (single vendor).

[20] Srl-labs, “Containerlab Web GUI,” https://containerlab.dev/manual/gui/web/, retrieved 2026-11-16. The Web GUI container (`ghcr.io/srl-labs/containerlab-web`) connects to `clab-api-server` (port 8090 by default). It does not run labs and does not start the API server. Login uses a Linux user on that host in group `clab_api` or `SUPERUSER_GROUP`. On Windows the API server runs in the Linux VM that owns the container runtime. Confidence: Low (single vendor).

[21] NetBox Labs, “Introduction to NetBox,” https://netboxlabs.com/docs/netbox/introduction/, retrieved 2026-11-16. NetBox is described as a network source of truth that represents desired state, and the page says automated import of live network state is strongly discouraged. The same page lists configuration management among things NetBox is not. Confidence: Low (single vendor). Agrees with the workshop’s own wording [2].

[22] `C:\Users\BrianNelson\Projects\workshop_arista_automation\scripts\config_diff.ps1` lines 1–2 (preview a push against running cEOS; apply nothing). Invoked by `run.ps1 diff` [1].

## Appendix A — Gate briefing

Domains: repo_tree + external + Glean. Operator-confirmed 2026-11-16. The principal git email was resolved by the caller and is not repeated here. No live NetBox and no live AWX were queried. No API token was sent to public search or fetch.

Repo tree used: `C:\Users\BrianNelson\Projects\workshop_arista_automation` (`run.ps1`, `wiki\Home.md`, `wiki\Architecture.md`, `wiki\Dual-DC-L3LS.md`, `labs\l3ls-dual-dc\WORKSHOP.md`, `plugins\netbox_ztp_portal`, `docker-compose.yml`, `playbooks\generate_configs.yml`, `playbooks\generate_configs_avd.yml`, `scripts\ztp_agent.py`, `scripts\day2_demo.py`, `scripts\config_diff.ps1`, `docker\clab-runner\Dockerfile`). The second local path in scope is `C:\Users\BrianNelson\Projects\atd-automation-l3ls-dual-dc`; scoring used the copy already under `labs\l3ls-dual-dc` in the workshop repo.

External pages retrieved 2026-11-16 are [14] through [21]. Glean was used only to resolve the acronym before the operator fixed it. Glean is not a scoring source in this report.

## Appendix B — technical_analysis identifiers

Research type: technical_analysis.

Subject identifiers:

- Workshop repo: `C:\Users\BrianNelson\Projects\workshop_arista_automation`
- Dual-DC repo path in scope: `C:\Users\BrianNelson\Projects\atd-automation-l3ls-dual-dc` (vendored track scored from `labs\l3ls-dual-dc` inside the workshop repo)
- Resolved NAF name: Network Automation Forum framework (https://networkautomation.forum/about and https://github.com/Network-Automation-Forum/reference/blob/main/docs/Framework/Framework.md) [14][15]

The operator rejected the internal “Network Automation Foundation and Roadmap” deck as an expansion of NAF. That deck is not a source and is not the best-practice bar in this report.

Shared-research note: research done here stays local to this project.

### Q1. Manual steps still in `run.ps1`, and what the ZTP portal already does

`run.ps1` help still requires a human, or a caller of those commands, for stack start (`up`), NetBox import (`import`), Jinja render (`generate`), Containerlab deploy (`deploy`), live `configure` apply (`apply`), day-2 (`day2`), dry run (`diff`), push (`push`), validation (`test`), flash backup (`backup`), rollback (`rollback`), and the dual-DC set `build` / `start` / `apply` / `stop` [1]. `generate` runs `playbooks/generate_configs.yml` only [1][9]. Regenerating `topology-docker.clab.yml` is a separate Python script, listed in the portal README between `generate` and `deploy` [6]. `pipeline` is generate plus deploy, not the day-2 loop [1].

The ZTP portal, at `/plugins/ztp/`, designs a new EVPN/VXLAN site into NetBox (devices, cables, IPs, ASNs, config contexts, tag `containerlab`), onboards one spine or leaf (New Block), and shows fleet tracking [6]. `DeployView` does not call Containerlab. It sets site custom field `ztp_deploy_status` to `requested` [7]. `run.ps1 agent` must be running; `scripts/ztp_agent.py` then renders configs, regenerates the topology, deploys Containerlab, and applies configs, and marks the site `deployed` [1][11]. Diff, test, backup, rollback, day-2, and `dualdc` are absent from that agent function [11][12].

### Q2. Which control plane should run those steps on a laptop

Use option D: a localhost dashboard that calls the existing `run.ps1` commands. It can show both tracks in one sitting without a new controller. Option A is the closest alternative and remains the right place to design a site (New Site), because that write already lands in NetBox [6]. Option B adds AWX, whose supported install since 18.0 is the AWX Operator, with Docker limited to unpublished development use [18], on a machine whose compose file is already NetBox, Postgres, Redis, a worker, Ansible, and a privileged Containerlab runner [8]. Several demo steps are host `docker exec` or PowerShell, and the Ansible container is not attached to the lab management network [1][5]. Option C’s Web GUI needs `clab-api-server`, a Linux login, and a Linux VM on Windows [20]. The graph command in the image this repo already builds is the lab view [13][19].

### Q3. What the NAF framework says a proper automation demo must show

The framework text, retrieved 2026-11-16, defines six building blocks and says to design around those functions, with version control, CI/CD, and machine-friendly APIs as the recommended practice for every block [15].

- Intent stores desired state, including configuration and operational expectations, behind an API, in a neutral model that is derived into vendor-specific configuration.
- Executor applies changes guided by that intended state, and it should offer a dry run and idempotent runs. It does the writes (SSH, NETCONF, gNMI, REST).
- Collector reads live state, including metrics and logs.
- Observability persists actual state and should raise events when actual configuration or operational state differs from intended state.
- Orchestration coordinates the other blocks, does not touch devices itself, and should support a dry run, logs of past and current workflows, and compensating actions when a step fails.
- Presentation is how humans interact (dashboard, GUI, or CLI). It must authenticate and authorize. The text says this does not imply a single pane of glass.

Mapped onto this workshop, a sitting shows Intent (NetBox for the Jinja fabric [2]; AVD group_vars for dual-DC [5]), the derived EOS files [9], the dry run [22], the live check [2], and a day-2 change that is edited in NetBox and then generated, diffed, pushed, and tested. Revert puts the saved NetBox values back and is followed by those same four commands [12]. The dashboard is Presentation. It links to NetBox and to the Containerlab graph so those stay visible as their own blocks [15][21].

### Q4. Where a Containerlab topology view sits

Put `containerlab graph` beside the two intent stores, as a picture of the lab topology file. For the Jinja track that file is produced from NetBox cables and devices (`generate_clab_topology_docker.py` in the documented pipeline) [2][6]. For dual-DC the topology file is the lab under `labs/l3ls-dual-dc/clab`, mounted into the `clab` service, and the intent remains AVD group_vars [4][5][8]. `--offline` draws the file without treating running containers as the source of design [19]. NetBox’s own introduction says it holds desired state and is not a configuration manager or a monitoring system [21], which matches keeping the graph in the lab runtime. The Web GUI is a second control application in front of `clab-api-server` [20]. It is the wrong place to edit either source of truth.

### Q5. What the demo must leave visible

The SE still walks the pipeline. The page shows the command name, the NetBox object or the AVD file that was edited, the generated `.cfg` list, the diff that applied nothing [22], the test output for reachability, BGP, MLAG, routes, and VXLAN [1][2], and a day-2 revert that restores the saved NetBox values before the same generate, diff, push, and test commands [12]. The two tracks stay labeled, because folding dual-DC into NetBox would hide the AVD model the lab exists to show [4]. Deploy and apply stay separate: the portal README states that apply uses `configure replace` so Containerlab links survive [6]. The `admin` / `admin` login and `LOGIN_REQUIRED=false` stay on screen as lab facts [1][8]. A single button that jumps from an empty NetBox to “fabric healthy” hides Intent, the derived config, the dry run, and the compensating step [15].

### Disconfirmation

Leading option before the check: D.

Evidence that would make D wrong: the ZTP portal and `ztp_agent.py` already perform diff, validation, day-2 revert, and `dualdc`, so a second UI would only duplicate a finished console; or the `run.ps1` steps are interactive and cannot be invoked one at a time.

What the files show: `DeployView.post` writes `requested` and returns [7]. `deploy_site` runs generate, topology regeneration, containerlab deploy, and apply, then sets `deployed` [11]. `day2_demo.py` edits NetBox and prints `generate`, `diff`, `push`, and `test` for the operator to run [12]. `dualdc` is its own switch and calls the lab’s AVD playbooks [1][5]. `import`, `generate`, `deploy`, `diff`, `push`, `test`, `day2`, and `dualdc` are non-interactive `docker compose` or script calls [1]. The interactive arms are `shell` and the long-running `agent`, and the dashboard does not need to wrap those. The disconfirming evidence did not show up. D stands.

### Weight sensitivity

Baseline weights: demo clarity 0.30, workshop fit 0.25, laptop burden 0.20, security 0.15, NAF practice 0.10.

Shifted weights: demo clarity 0.20, laptop burden 0.30, other weights unchanged.

| Option | Baseline | Shifted |
| --- | --- | --- |
| D | 4.50 | 4.40 |
| A | 3.60 | 3.60 |
| C | 2.65 | 2.55 |
| B | 2.40 | 2.30 |

The ranking does not flip. D remains first. A remains second.
