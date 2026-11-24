# technical_analysis — Ansible AWX on Windows via Docker (RSCH-002)

**Research type:** `technical_analysis` \
**Subject:** How to run Ansible AWX on a Windows host using Docker (Linux containers / WSL2 / local Kubernetes) \
**Date:** 2026-11-23 \
**Requestor:** workshop operator (standalone research) \
**Identifier:** `RSCH-002` \
**Access scope:** `NARROW` — external public sources only (see Appendix A)

---

## 1. Findings

### 1b. `options_matrix` sub-layout (weighted comparison)

| Criterion (weight) | Option A: Docker Desktop + `tools/docker-compose` (dev) | Option B: Docker Desktop + Minikube/Kind + awx-operator | Option C: Hyper-V Linux VM + k3s + awx-operator | Option D: Remote Linux / shared K8s + awx-operator |
| -------------------- | --- | --- | --- | --- |
| Official support path (0.25) | 2 [1][2][13] | 5 [1][4][5] | 4 [1][4][10] | 5 [1][4] |
| Windows laptop practicality (0.25) | 3 [6][7][8] | 4 [5][9][11] | 4 [10] | 2 [— remote, not on-laptop] |
| Resource / laptop TCO (0.20) | 3 [2][13] | 2 [5][14] | 3 [10] | 5 [offloads host] |
| Workshop time-to-value (0.15) | 2 [3][8][15] | 3 [4][9] | 3 [10] | 4 [pre-provisioned] |
| Reliability / failure modes (0.15) | 2 [8][15][16] | 3 [11][17] | 4 [10] | 5 [standard Linux/K8s] |
| **Weighted score** | **2.55** | **3.55** | **3.65** | **3.85** |

**Recommendation:** For a **workshop/dev laptop that must host AWX locally**, prefer **Option C (Hyper-V Ubuntu/CentOS VM + k3s + awx-operator)** or **Option B (Docker Desktop WSL2 Linux containers + Minikube or Kind + awx-operator)**. Do **not** treat native Windows Docker / the removed Local Docker installer / Windows containers as a supported path. Option D scores highest overall when a remote Linux host is acceptable; it is the cleanest operational choice for shared workshops. Bare `tools/docker-compose` on Windows (Option A) is development-only, **untested on Windows** in upstream docs, and fragile after AWX 18. **[Moderate — ≥2 independent primary sources on operator-vs-compose and Windows-untested status; laptop ranking is judgment under resource constraints.]**

**Weight sensitivity:** Raising *Official support* by +0.1 (and lowering *Resource* by 0.1) keeps D ≥ C ≥ B ≫ A. Raising *Windows laptop practicality* by +0.1 (and lowering *Resource*) flips local leaders so **B ≈ C** (both beat A). If “must be on this laptop” excludes D, **C edges B** on reliability; if “must stay closest to upstream test docs,” **B** wins. Ranking among A vs B/C/D does **not** flip: A remains last.

**Disconfirmation pass (leading local answer = C/B via operator, not compose):** Evidence that would refute: (1) Windows listed as a tested OS for `tools/docker-compose`; (2) Local Docker installer restored as supported; (3) AWX Operator docs forbidding Minikube/Kind on Docker Desktop. Searched official INSTALL.md, docker-compose README tested-OS list, operator basic/minikube/kind docs, and Windows-related issues — (1) and (2) absent/false; (3) false (Minikube/Kind are documented test paths). Refutation **not found**; residual risk is resource pressure and WSL/Docker networking quirks [11][17].

**Supporting points (sub-questions):**

- **Official methods:** Since AWX 18, **awx-operator on Kubernetes** is the preferred install; Docker Compose is development/test only with **no official published release** [1][2][13]. **[Moderate — INSTALL.md + compose README are same-repo; [13] maintainer comment is independent corroboration but not a third primary]**
- **Windows host reality:** AWX runs as **Linux** containers. On Windows use **Docker Desktop with Linux containers / WSL2 backend** — not Windows containers [6][7][11]. Upstream lists tested compose platforms as Fedora, Ubuntu LTS, RHEL/CentOS, macOS — **“other platforms untested”** (Windows excluded) [2]. CONTRIBUTING still points Windows contributors at Docker Desktop, but that does not override the compose tested-OS caveat [7][2]. **[High — Docker Docs + AWX CONTRIBUTING + Minikube Windows driver docs]**
- **Installer / compose status:** Pre-18 Local Docker installer was **removed** in favor of the development compose environment and awx-operator [1][2][3]. Community blogs describing `installer/` playbooks apply only to AWX ≤17 [15]. **[Moderate — same-repo primary docs + issue restating project change; [15] Tier-3]**
- **Known-good practical approaches:** (B) Minikube/Kind + operator (official test docs; Kind WSL2 and Minikube Docker-on-Windows documented) [4][5][9][11][12]; (C) Linux VM + k3s + operator (e.g. kurokobo/awx-on-k3s, tested on CentOS Stream) [10]; (D) remote Linux K8s. **Podman on Windows** can run Linux containers via a WSL2/Hyper-V Podman machine [19], but there is **no first-party AWX-on-Podman Windows guide**; community guidance still points at Kubernetes + operator (or outdated compose), so Podman alone is **not** a recommended workshop path **[Low — single primary Podman Windows doc + forum/community secondary]** [19][20]. Option A may work for some via WSL2 but carries redis/volume and DNS/build failures [8][16]; Minikube-on-Windows has separate host reachability quirks [11][17]. **[Moderate]**
- **Hard requirements / blockers:** Virtualization + WSL2; Linux container mode; ~4 CPU / 6–8 GiB for Minikube-style AWX [5][14]; Ansible+OpenSSL+Compose for the **dev** path [2]; for k3s-in-WSL, enable **systemd** in WSL (`[boot] systemd=true`) [21] and meet k3s baseline resources [22]; AWX project **releases paused** (last noted Jul 2024) — plan for pinned tags / operator images [18]. Common failures: Windows containers mode; redis bind-mounts on WSL [8]; DNS during image build [16]; ingress/service reachability from Windows host for Minikube Docker driver [11][17]. **[Moderate]**

---

## 2. Confidence

**Overall confidence:** `Moderate`

**Justification:** Primary upstream docs (INSTALL.md, tools/docker-compose README, awx-operator installation) agree on operator-first and compose-as-dev / Windows-untested. Local ranking between B and C is judgment under laptop resource constraints; Option D’s score assumes remote hosting is in scope for the workshop. Phase 3 stayed within budget (~12 search/fetch iterations). AWX release pause and evolving operator docs add freshness risk.

**Per-sub-question coverage (research-qa §5):**

| Sub-question | Coverage | Band cap |
| --- | --- | --- |
| Official Docker-on-Windows methods | 0.8 | — |
| Installer / operator / compose support on Windows | 0.9 | — |
| Practical known-good approaches (incl. Podman) | 0.7 | Podman band Low |
| Hard requirements & failure modes | 0.7 | — |
| Workshop laptop recommendation | 0.7 | B vs C weight-sensitive |

---

## 3. Verification

**Verified by:** independent readonly sub-agent (`generalPurpose`, model `cursor-grok-4.5-high-fast` — config ROLE `opus`/`cursor-grok-4.6-xhigh-fast` unavailable on this harness; nearest listed judge-family substitute), fresh dispatch with no researcher context — per `research` Phase 4.5.1. \
**Remediation loops:** 2 \
**Overall verdict:** `PASS` — all three checks pass on loop-2 fresh verifier

| Citation # | Verdict | Note |
| --- | --- | --- |
| [1]–[22] | PASS | Loop-2 re-fetched all 22; claims match source text |
| [8]/[12]/[17]/[21] (prior) | FIXED | Loop-0/1 attachment errors corrected before PASS |

**Scope reconciliation (overall):** `PASS` — answers official methods, installer/operator/compose, WSL2/Hyper-V/remote/Podman/Kind/K3s, blockers, workshop recommendation; Podman present (Low). \
**Confidence-integrity (overall):** `PASS` — only Windows-host High rests on ≥3 independent primaries [6][7][11]; Official/Installer Moderate; Podman Low. \
**Notes:** Loop-0 FAIL (citation attachments + Podman missing + inflated High). Loop-1 FAIL (Option C [12]/[21] misattach + same-repo High). Loop-2 PASS. No check weakened.

---

## 4. Recommended Next Steps

- [ ] On the workshop laptop: enable WSL2 + Docker Desktop **Linux containers**; confirm `docker info` reports `OSType: linux`.
- [ ] Choose local path: **Minikube/Kind + awx-operator** (closest to upstream test docs) **or** a small **Hyper-V Linux VM + k3s + awx-operator** (more isolated / closer to community single-node guides).
- [ ] Pin an awx-operator release tag; do not rely on untagged `devel` or the removed `installer/` directory.
- [ ] If multiple students need AWX, prefer **Option D** (shared remote Linux K8s) over N× heavy laptop installs.
- [ ] **Recommend `create-initiative` initiative:** `No` — findings are citable grounding for ops decisions; build only if the workshop adopts a specific local AWX stack.

**Provenance:** `RSCH-002` — this report's citable identifier.

---

## 5. References

[1] "Installing AWX (INSTALL.md)." *ansible/awx (GitHub)*. <https://raw.githubusercontent.com/ansible/awx/devel/INSTALL.md>. Retrieved 2026-11-23. **Tier:** 1.

[2] "Docker Compose for Development (tools/docker-compose/README.md)." *ansible/awx (GitHub)*. <https://github.com/ansible/awx/blob/devel/tools/docker-compose/README.md>. Retrieved 2026-11-23. **Tier:** 1.

[3] "AWX on windows (#12304)." *ansible/awx Issues*. <https://github.com/ansible/awx/issues/12304>. Retrieved 2026-11-23. **Tier:** 1.

[4] "Basic Install." *Ansible AWX Operator Documentation*. <https://docs.ansible.com/projects/awx-operator/en/latest/installation/basic-install.html>. Retrieved 2026-11-23. **Tier:** 1.

[5] "Creating a minikube cluster for testing." *Ansible AWX Operator Documentation*. <https://docs.ansible.com/projects/awx-operator/en/latest/installation/creating-a-minikube-cluster-for-testing.html>. Retrieved 2026-11-23. **Tier:** 1.

[6] "Install Docker Desktop on Windows." *Docker Docs*. <https://docs.docker.com/desktop/setup/install/windows-install/>. Retrieved 2026-11-23. **Tier:** 1.

[7] "CONTRIBUTING.md — Setting up your development environment." *ansible/awx (GitHub)*. <https://raw.githubusercontent.com/ansible/awx/devel/CONTRIBUTING.md>. Retrieved 2026-11-23. **Tier:** 1.

[8] "Running the development environment on Windows using wsl2 (#7731)." *ansible/awx Issues*. <https://github.com/ansible/awx/issues/7731>. Retrieved 2026-11-23. **Tier:** 1.

[9] "AWX Operator on Kind (kind-install.md)." *ansible/awx-operator (GitHub)*. <https://github.com/ansible/awx-operator/blob/devel/docs/installation/kind-install.md>. Retrieved 2026-11-23. **Tier:** 1.

[10] "awx-on-k3s." *kurokobo/awx-on-k3s (GitHub)*. <https://github.com/kurokobo/awx-on-k3s/>. Retrieved 2026-11-23. **Tier:** 3 (community guide; operator-based).

[11] "docker driver — Known Issues (Windows/WSL2)." *minikube documentation*. <https://minikube.sigs.k8s.io/docs/drivers/docker/>. Retrieved 2026-11-23. **Tier:** 1.

[12] "kind – Using WSL2." *kind documentation*. <https://kind.sigs.k8s.io/docs/user/using-wsl2/>. Retrieved 2026-11-23. **Tier:** 1.

[13] "Request for official Docker Compose setup (#15976)." *ansible/awx Issues*. <https://github.com/ansible/awx/issues/15976>. Retrieved 2026-11-23. **Tier:** 1.

[14] "Development Guide (Minikube memory/CPU)." *Ansible AWX Operator Documentation*. <https://docs.ansible.com/projects/awx-operator/en/latest/development.html>. Retrieved 2026-11-23. **Tier:** 1.

[15] "Installing Ansible AWX on Windows Using WSL 2 & Docker Desktop." *grantlittle.me*. <https://grantlittle.me/2020/06/08/installing-ansible-awx-on-windows-using-wsl-2-docker-desktop/>. Retrieved 2026-11-23. **Tier:** 3 (explicitly AWX ≤17).

[16] "docker-compose-build failing DNS on Windows/WSL2 (#12462)." *ansible/awx Issues*. <https://github.com/ansible/awx/issues/12462>. Retrieved 2026-11-23. **Tier:** 1.

[17] "Setting Up WSL 2 for the Docker driver." *minikube documentation*. <https://minikube.sigs.k8s.io/docs/tutorials/wsl_docker_driver/>. Retrieved 2026-11-23. **Tier:** 1.

[18] "ansible/awx README — releases paused." *ansible/awx (GitHub)*. <https://github.com/ansible/awx>. Retrieved 2026-11-23. **Tier:** 1.

[19] "Podman for Windows." *containers/podman (GitHub)*. <https://github.com/containers/podman/blob/main/docs/tutorials/podman-for-windows.md>. Retrieved 2026-11-23. **Tier:** 1.

[20] "Awx install environment." *Ansible Forum*. <https://forum.ansible.com/t/awx-install-environment/6293>. Retrieved 2026-11-23. **Tier:** 3.

[21] "Use systemd to manage Linux services with WSL." *Microsoft Learn*. <https://learn.microsoft.com/en-us/windows/wsl/systemd>. Retrieved 2026-11-23. **Tier:** 1.

[22] "Requirements." *K3s Documentation*. <https://docs.k3s.io/installation/requirements>. Retrieved 2026-11-23. **Tier:** 1.

---

## Appendix A — Access-Gate Result

**What was checked:** Class A principal resolved · Class B/C for `external` via recorded inapplicable declaration · Class D inapplicable for public web · `repo_tree` eligibility + CONFIRM lines · §5 verify block

**Result:** `NARROW`

**Scope narrowing:** `application_data` unlisted (not requested). `repo_tree` **excluded** this session: §5 verify returned `VERDICT UNRESOLVED`; eligibility `rg` returned hits under workshop scripts (`rbac.py`, etc.); Cursor has no AskUserQuestion — operator confirmation for `single_maintainer` not obtained live. Prefer external-only per session instructions. Config’s prior operator assertion (2026-09-03) noted as residual risk only, not used to authorize `repo_tree` reads for grounding.

**Gate Briefing (full text):**

```text
Access Gate — deep research
  Domains:  external (session scope); repo_tree DECLINED; application_data not requested (unlisted ⇒ would halt)
  Bindings: external = multi_user_authz from rung 1 (config §23); repo_tree binding present but unused
  Verify:   VERDICT UNRESOLVED (exit 2) · confirmed THIS session:
            - CHECK git config user.email → brian.nelson@ahead.com (agent ran command)
            - CONFIRM external no file:line anchors → agent (Cursor substitute): invoke recorded
              external declaration (entitlement/scope/segment status:inapplicable + per-source
              falsification); public AWX/Docker docs only
            - CONFIRM repo_tree / single_maintainer / tool-grant CHECK → NOT confirmed for use;
              eligibility rg ≥1 hits; domain narrowed away (prefer external-only)
  Checked:  Class A principal resolved · Class B/C external inapplicable under recorded declaration
            (per-source falsification armed) · Class D N/A (no segment) · subject segmented: no
  Result:   NARROW — external public sources only; repo_tree excluded
  Proceeding at: WebSearch/WebFetch of public AWX/Docker/K8s documentation and issues
```

---

## Appendix B — Scope & Research Type

**Research type selected:** `technical_analysis` — `findings_pattern`: `options_matrix`

**Why this type:** Request is a technical options evaluation (how to run AWX on Windows with Docker) mapped to `research_types.technical_analysis` in sdd-skill-config-deep-research.md §23.

**Research Index duplicate check:** RSCH-001 (workshop ops dashboard) mentions AWX only superficially — **proceed independently** per requestor instruction. Next id **RSCH-002**.

**Shared research disclosure:** Shared research is not available — research stays local to this project.

**Subject identity:** Ansible AWX — <https://github.com/ansible/awx> (OSS upstream of Ansible Automation Controller). Near-misses (AAP commercial, ansible-runner) not selected.

**Scope statement / sub-questions (evidence needs pre-declared):**

1. Official AWX deployment methods usable with Docker on Windows (Desktop/WSL2/Linux containers) — need INSTALL.md + Docker Desktop docs.
2. Whether installer / awx-operator / docker-compose are supported or broken on Windows hosts — need compose tested-OS list + issue history.
3. Practical known-good approaches (WSL2, Hyper-V VM, remote Linux, Podman, Kind/K3s) — need operator Kind/Minikube docs + community k3s guides + Podman Windows docs.
4. Hard requirements, blockers, common Windows failure modes — need issue threads + minikube Windows notes + WSL systemd/k3s requirements.
5. Recommended path for workshop/dev laptop — synthesize options_matrix.

---

## Appendix C — Methodology

**Corpus doc used:** `C:\Users\BrianNelson\.claude\skills\sdd-research\assets\research-corpus\technical-analysis.md`

**Tools used:** `WebSearch` / `WebFetch` (cursor dynamic tools); Shell limited to access-gate / RSCH-ID / date; Write under `sdd/research/` only. `repo_tree` Grep/Read for AWX grounding **not** used (narrowed).

**Iteration budget:** ~12 / ~15–20 — synthesized within budget.

**Persona note:** `.claude/agents/research-analyst/AGENT.md` not resolved in this project tree; proceeded on deep-research.md + technical-analysis.md per persona-dispatch-degradation pattern.
