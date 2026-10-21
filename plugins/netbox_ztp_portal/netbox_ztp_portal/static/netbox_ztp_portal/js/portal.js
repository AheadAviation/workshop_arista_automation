/* AHEAD ZTP Portal - page behaviors. Feature-detects the current page by the
   presence of anchor elements, so one script serves all four pages. */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---- Overview: health badge -------------------------------------------
  async function initOverview() {
    const el = $("ztp-health");
    if (!el) return;
    const { data } = await ZTP.health();
    if (data.ok) {
      el.innerHTML = `<span class="badge ok">Connected</span> NetBox ${esc(data.netbox_version || "")}` +
        ` &middot; portal tag <code>${esc(data.self_portal_tag)}</code> &middot; lab tag <code>${esc(data.containerlab_tag)}</code>`;
    } else {
      const msg = typeof data.error === "object" ? JSON.stringify(data.error) : data.error;
      el.innerHTML = `<span class="badge err">Unavailable</span> ${esc(msg)}`;
    }
  }

  // ---- New Site: fabric designer ----------------------------------------
  function nextSteps(site) {
    return `# 1) Generate Arista configs from NetBox (new devices included automatically)\n` +
      `./run.ps1 generate\n\n` +
      `# 2) Regenerate the ContainerLab topology from NetBox\n` +
      `docker compose run --rm ansible python scripts/generate_clab_topology_docker.py\n\n` +
      `# 3) Deploy - brings ${esc(site)} cEOS nodes up alongside DC1\n` +
      `./run.ps1 deploy`;
  }

  function renderPreview(out, data) {
    const s = data.summary;
    let html = `<h3>${esc(s.site_code)} &middot; ${esc(s.fabric_name)}</h3>` +
      `<div class="grid three">` +
      tile("Devices", s.device_count) + tile("Cables", s.cable_count) +
      tile("Spine ASN", s.spine_asn) + tile("Leaf ASNs", s.leaf_asns.join(", ")) +
      tile("Mgmt range", s.mgmt_range) + tile("P2P net", s.p2p_network) +
      `</div>`;
    html += `<table><thead><tr><th>Device</th><th>Role</th><th>Mgmt IP</th><th>Router ID</th><th>ASN</th><th>VTEP</th></tr></thead><tbody>`;
    data.devices.forEach((d) => {
      const cf = d.custom_fields || {};
      html += `<tr><td>${esc(d.name)}</td><td><span class="pill ${esc(d.role)}">${esc(d.role)}</span></td>` +
        `<td>${esc(d.mgmt_ip)}</td><td>${esc(cf.router_id)}</td><td>${esc(cf.bgp_asn)}</td><td>${esc(cf.vtep_ip || "-")}</td></tr>`;
    });
    html += `</tbody></table>`;
    out.innerHTML = html;
    out.classList.remove("hidden");
  }

  function tile(label, value) {
    return `<div class="tile"><small>${esc(label)}</small><div class="v">${esc(value)}</div></div>`;
  }

  function val(id) { const el = $(id); return el && el.value.trim() ? el.value.trim() : null; }

  function planFromForm() {
    const plan = {
      site_code: ($("ztp-site-code").value || "DC2").trim().toUpperCase(),
      num_spines: parseInt($("ztp-spines").value, 10),
      num_leaf_pairs: parseInt($("ztp-pairs").value, 10),
    };
    if (val("ztp-desc")) plan.description = val("ztp-desc");
    if (val("ztp-region")) plan.region = val("ztp-region");
    const bc = {};
    if (val("ztp-ntp")) bc.ntp = { servers: val("ztp-ntp") };
    if (val("ztp-dns")) bc.dns = { domain: val("ztp-dns") };
    if (val("ztp-snmp")) bc.snmp = { community: val("ztp-snmp") };
    if (Object.keys(bc).length) plan.baseConfig = bc;
    return plan;
  }

  function initArchitect() {
    const previewBtn = $("ztp-preview");
    if (!previewBtn) return;
    const buildBtn = $("ztp-build");
    const previewOut = $("ztp-preview-out");
    const buildOut = $("ztp-build-out");
    const steps = $("ztp-nextsteps");

    previewBtn.addEventListener("click", async () => {
      previewBtn.disabled = true;
      previewOut.innerHTML = "Computing fabric&hellip;";
      previewOut.classList.remove("hidden");
      const { data } = await ZTP.preview(planFromForm());
      previewBtn.disabled = false;
      if (!data.ok) { previewOut.innerHTML = `<div class="result err">${esc(JSON.stringify(data.error))}</div>`; return; }
      renderPreview(previewOut, data);
      buildBtn.disabled = false;
    });

    buildBtn.addEventListener("click", async () => {
      if (!confirm("Write this fabric into NetBox? Devices will be tagged for ContainerLab deployment.")) return;
      buildBtn.disabled = true;
      buildOut.innerHTML = "Building in NetBox&hellip;";
      buildOut.classList.remove("hidden");
      const plan = planFromForm();
      const { data } = await ZTP.build(plan);
      buildBtn.disabled = false;
      if (!data.ok && !data.site) {
        buildOut.innerHTML = `<div class="result err"><strong>Build failed.</strong> ${esc(JSON.stringify(data.error || data.errors))}</div>`;
        return;
      }
      const c = (data.created || []).length, k = (data.skipped || []).length, e = (data.errors || []).length;
      let html = `<div class="result ${e ? "err" : "ok"}"><strong>${esc(plan.site_code)}</strong> ` +
        `&mdash; created ${c}, skipped ${k}, errors ${e}. ` +
        (data.site ? `<a href="${esc(data.site.url)}" target="_blank">Open site in NetBox &rarr;</a>` : "") + `</div>`;
      if (e) {
        html += `<details><summary>${e} error(s)</summary><pre>${esc(JSON.stringify(data.errors, null, 2))}</pre></details>`;
      }
      buildOut.innerHTML = html;
      steps.querySelector("pre").textContent = nextSteps(plan.site_code);
      steps.classList.remove("hidden");
    });
  }

  // ---- New Block: per-fabric pipeline view ------------------------------
  const STAGES = ["bound", "booting", "config-pushed", "active"];
  async function loadDevices(siteId, tbody) {
    tbody.innerHTML = `<tr><td colspan="5">Loading&hellip;</td></tr>`;
    const { data } = await ZTP.devices(siteId);
    if (!data.ok) { tbody.innerHTML = `<tr><td colspan="5" class="result err">${esc(JSON.stringify(data.error))}</td></tr>`; return; }
    if (!data.devices.length) { tbody.innerHTML = `<tr><td colspan="5" class="muted">No portal-built devices yet.</td></tr>`; return; }
    tbody.innerHTML = data.devices.map((d) =>
      `<tr><td><a href="${esc(d.url)}" target="_blank">${esc(d.name)}</a></td>` +
      `<td><span class="pill ${esc(d.role)}">${esc(d.role)}</span></td>` +
      `<td>${esc(d.site)}</td><td>${esc(d.mgmt_ip || "-")}</td>` +
      `<td><span class="pill ${esc(d.stage)}">${esc(d.stage)}</span></td></tr>`).join("");
  }

  const DEPLOY_PILL = {
    "not-deployed": ["bound", "not deployed"], "requested": ["booting", "queued"],
    "running": ["booting", "deploying…"], "deployed": ["active", "deployed"],
    "failed": ["err", "failed"],
  };

  async function loadFabricDeploy(tbody) {
    const { data } = await ZTP.deployStatus();
    if (!data.ok) { tbody.innerHTML = `<tr><td colspan="3" class="result err">${esc(JSON.stringify(data.error))}</td></tr>`; return; }
    if (!data.fabrics.length) { tbody.innerHTML = `<tr><td colspan="3" class="muted">No fabrics yet. Build one in <a href="../new-site/">New Site</a>.</td></tr>`; return; }
    tbody.innerHTML = data.fabrics.map((f) => {
      const [cls, label] = DEPLOY_PILL[f.status] || ["bound", f.status];
      const busy = f.status === "requested" || f.status === "running";
      const pill = cls === "err" ? `<span class="badge err">${esc(label)}</span>` : `<span class="pill ${cls}">${esc(label)}</span>`;
      const msg = f.message ? ` <span class="muted" style="font-size:.8rem">${esc(f.message)}</span>` : "";
      const btnLabel = f.status === "deployed" ? "Redeploy" : "Deploy";
      return `<tr><td><a href="${esc(f.url)}" target="_blank">${esc(f.name)}</a></td>` +
        `<td>${pill}${msg}</td>` +
        `<td><button class="btn ztp-deploy-btn" data-site="${esc(f.id)}" ${busy ? "disabled" : ""}>${btnLabel}</button></td></tr>`;
    }).join("");
    tbody.querySelectorAll(".ztp-deploy-btn").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("Queue this fabric for ContainerLab deployment? Make sure the host agent (./run.ps1 agent) is running.")) return;
      b.disabled = true; b.textContent = "Queuing…";
      const { data: d } = await ZTP.requestDeploy(b.getAttribute("data-site"));
      if (!d.ok) alert("Deploy request failed: " + JSON.stringify(d.error));
      loadFabricDeploy(tbody);
    }));
  }

  async function initDatacenter() {
    const sel = $("ztp-site-select");
    if (!sel) return;
    const devBody = $("ztp-devices");
    const depBody = $("ztp-fabric-deploy");
    const { data } = await ZTP.listSites();
    if (data.ok) {
      sel.innerHTML = `<option value="">All portal fabrics</option>` +
        data.sites.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("");
    }
    sel.addEventListener("change", () => loadDevices(sel.value, devBody));
    const refresh = () => { loadFabricDeploy(depBody); loadDevices(sel.value, devBody); };
    refresh();
    // Poll so deploy progress + device stages update live while watching.
    setInterval(refresh, 5000);
  }

  // ---- Fleet tracking ---------------------------------------------------
  async function initTracking() {
    const wrap = $("ztp-fabrics");
    if (!wrap) return;
    wrap.innerHTML = "Loading&hellip;";
    const { data } = await ZTP.tracking();
    if (!data.ok) { wrap.innerHTML = `<div class="result err">${esc(JSON.stringify(data.error))}</div>`; return; }
    if (!data.fabrics.length) { wrap.innerHTML = `<div class="muted">No fabrics built yet. Start with <a href="../new-site/">New Site</a>.</div>`; return; }
    wrap.innerHTML = data.fabrics.map((f) => {
      const stagePills = STAGES.filter((s) => f.stages[s]).map((s) =>
        `<span class="pill ${s}">${s}: ${f.stages[s]}</span>`).join(" ");
      const roles = Object.entries(f.roles).map(([r, n]) => `${n} ${r}`).join(", ");
      return `<div class="card"><div style="display:flex;justify-content:space-between;align-items:center">` +
        `<h3 style="margin:0"><a href="${esc(f.url)}" target="_blank">${esc(f.name)}</a></h3>` +
        `<strong>${f.percent}%</strong></div>` +
        `<div class="bar" style="margin:8px 0"><span style="width:${f.percent}%"></span></div>` +
        `<div class="muted" style="font-size:.85rem">${esc(roles)} &middot; ${f.active_count}/${f.device_count} active</div>` +
        `<div style="margin-top:8px">${stagePills}</div></div>`;
    }).join("");
  }

  document.addEventListener("DOMContentLoaded", () => {
    initOverview();
    initArchitect();
    initDatacenter();
    initTracking();
  });
})();
