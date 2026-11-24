/* AHEAD ZTP Portal - thin fetch wrapper over the plugin API (/api/plugins/ztp).
   Same-origin, authenticated via the NetBox session cookie. No dependencies.

   Sends the CSRF token on every unsafe method. The API used to be `csrf_exempt` because this
   wrapper did not send one; exempting a session-authenticated write endpoint means any page a
   logged-in user visits could POST here on their behalf. The token costs four lines and closes
   that, so the API no longer exempts anything. */
window.ZTP = (function () {
  const base = "/api/plugins/ztp";

  /** The CSRF token NetBox renders into the page, or "" outside a browser context. */
  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  async function req(method, path, body) {
    const opts = { method, headers: { Accept: "application/json" }, credentials: "same-origin" };
    if (method !== "GET" && method !== "HEAD") {
      const token = csrfToken();
      if (token) opts.headers["X-CSRFToken"] = token;
    }
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
