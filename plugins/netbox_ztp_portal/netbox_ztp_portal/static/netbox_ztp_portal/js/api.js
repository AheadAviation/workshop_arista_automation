/* AHEAD ZTP Portal - thin fetch wrapper over the plugin API (/api/plugins/ztp).
   Same-origin, authenticated via the NetBox session cookie. No dependencies. */
window.ZTP = (function () {
  const base = "/api/plugins/ztp";

  async function req(method, path, body) {
    const opts = { method, headers: { Accept: "application/json" }, credentials: "same-origin" };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let status = 0, data;
    try {
      const r = await fetch(base + path, opts);
      status = r.status;
      try { data = await r.json(); } catch (e) { data = { ok: false, error: "invalid JSON (HTTP " + status + ")" }; }
    } catch (e) {
      data = { ok: false, error: "network error: " + e.message };
    }
    return { status, data };
  }

  return {
    health: () => req("GET", "/health/"),
    listSites: () => req("GET", "/sites/"),
    preview: (plan) => req("POST", "/preview/", plan),
    build: (plan) => req("POST", "/sites/", plan),
    tracking: () => req("GET", "/tracking/"),
    devices: (siteId) => req("GET", "/devices/" + (siteId ? "?site_id=" + encodeURIComponent(siteId) : "")),
    deployStatus: () => req("GET", "/deploy/"),
    requestDeploy: (siteId) => req("POST", "/deploy/", { site_id: siteId }),
  };
})();
