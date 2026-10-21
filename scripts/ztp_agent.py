#!/usr/bin/env python3
"""AHEAD ZTP host agent.

Bridges the ZTP portal (which runs inside the NetBox container and cannot touch
Docker) to the host's Docker / ContainerLab. It watches NetBox for portal-built
sites whose `ztp_deploy_status` custom field is "requested" (set by the New Block
"Deploy" button) and runs the full pipeline for them:

    generate configs -> regenerate topology -> containerlab deploy -> apply configs

updating the site's status custom field as it goes so the GUI shows live progress.

Run it once on the host, from the repo root, and leave it running:

    ./run.ps1 agent            (or: python scripts/ztp_agent.py)

Stdlib only. Talks to NetBox over REST with the workshop token.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NODE_PREFIX = "clab-netbox-arista-lab-"
STATUS_CF = "ztp_deploy_status"
MSG_CF = "ztp_deploy_message"
TOPO = "/clab/topology/topology-docker.clab.yml"


def log(msg):
    print(f"[ztp-agent] {msg}", flush=True)


class NB:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def _req(self, method, path, body=None):
        url = f"{self.url}/api/{path.lstrip('/')}"
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(url, data=data, method=method)
        r.add_header("Authorization", f"Token {self.token}")
        r.add_header("Accept", "application/json")
        if data:
            r.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    def requested_sites(self):
        out, url = [], "dcim/sites/?tag=self-portal&limit=100"
        while url:
            try:
                d = self._req("GET", url)
            except urllib.error.HTTPError as e:
                # NetBox 4.1 returns 400 if the tag slug is not yet an allowed
                # filter choice (tag has not been created). Treat as no sites.
                if e.code == 400:
                    return []
                log(f"NetBox HTTP {e.code}: {e}")
                return []
            except urllib.error.URLError as e:
                log(f"NetBox unreachable: {e}")
                return []
            except (ConnectionResetError, TimeoutError, OSError) as e:
                log(f"NetBox connection dropped ({e}); will retry")
                return []
            for s in d.get("results", []):
                if (s.get("custom_fields") or {}).get(STATUS_CF) == "requested":
                    out.append(s)
            nxt = d.get("next")
            url = nxt.split("/api/", 1)[-1] if nxt else None
        return out

    def set_status(self, site_id, status, message=""):
        try:
            self._req("PATCH", f"dcim/sites/{site_id}/",
                      {"custom_fields": {STATUS_CF: status, MSG_CF: message}})
        except urllib.error.URLError as e:
            log(f"failed to set status on site {site_id}: {e}")


def run(cmd, step):
    """Run a host command from the repo root; return (ok, tail_of_output)."""
    log(f"  $ {' '.join(cmd)}")
    p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-8:])
    if p.returncode != 0:
        log(f"  ! {step} failed (rc={p.returncode})\n{tail}")
        return False, tail
    return True, tail


def running_nodes():
    p = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], cwd=str(REPO),
                       capture_output=True, text=True)
    return {n for n in p.stdout.split() if n.startswith(NODE_PREFIX)}


def cli_ready(node):
    r = subprocess.run(["docker", "exec", node, "Cli", "-p", "15", "-c", "show hostname"],
                       cwd=str(REPO), capture_output=True, text=True)
    return r.returncode == 0 and "hostname" in (r.stdout + r.stderr).lower()


def apply_configs(slug):
    """Push each rendered config into its running node via Cli configure-merge.
    (configure replace on this cEOS build silently drops hostname/VLANs/switchport.)
    Waits for each freshly-booted node's CLI to be ready first."""
    cfg_dir = REPO / "output" / "configs"
    nodes = running_nodes()
    targets = [(cfg, NODE_PREFIX + cfg.stem) for cfg in sorted(cfg_dir.glob("*.cfg"))
               if (NODE_PREFIX + cfg.stem) in nodes and cfg.stem.startswith(f"{slug}-")]

    # Wait for cEOS to finish booting (CLI responsive) before applying.
    log(f"  waiting for {len(targets)} node(s) to finish booting…")
    deadline = time.time() + 180
    for _, node in targets:
        while time.time() < deadline and not cli_ready(node):
            time.sleep(6)

    applied, failed = 0, 0
    for cfg, node in targets:
        body = "configure\n" + cfg.read_text(encoding="utf-8")
        r = subprocess.run(["docker", "exec", "-i", node, "Cli", "-p", "15"],
                           cwd=str(REPO), input=body, capture_output=True, text=True)
        blob = (r.stdout + r.stderr).lower()
        if "invalid input" in blob or "% error" in blob:
            failed += 1
        else:
            applied += 1
    return applied, failed


def deploy_site(nb, site):
    sid, name, slug = site["id"], site["name"], site["slug"]
    log(f"Deploying {name} (site {sid}) ...")
    # Steps 1-2 must succeed (they only touch NetBox + files).
    for msg, cmd in [
        ("rendering configs from NetBox", ["docker", "compose", "run", "--rm", "ansible",
                                           "ansible-playbook", "playbooks/generate_configs.yml"]),
        ("regenerating ContainerLab topology", ["docker", "compose", "run", "--rm", "ansible",
                                                "python", "scripts/generate_clab_topology_docker.py"]),
    ]:
        nb.set_status(sid, "running", msg + "…")
        ok, tail = run(cmd, msg)
        if not ok:
            nb.set_status(sid, "failed", f"{msg} failed: {tail[-160:]}")
            return

    # Deploy: judge by node presence, not exit code. ContainerLab's cEOS
    # postdeploy config push (scrapli) can panic/return non-zero even though the
    # node containers are created fine - and we push configs ourselves anyway.
    nb.set_status(sid, "running", "booting cEOS nodes…")
    ok, tail = run(["docker", "compose", "run", "--rm", "clab", "containerlab",
                    "deploy", "--reconfigure", "--topo", TOPO], "booting cEOS nodes")
    site_prefix = f"{NODE_PREFIX}{slug}-"
    nodes = [n for n in running_nodes() if n.startswith(site_prefix)]
    if not nodes:
        nb.set_status(sid, "failed", f"no {slug} nodes came up: {tail[-160:]}")
        return
    if not ok:
        log(f"  (containerlab returned non-zero, but {len(nodes)} {slug} nodes are running - continuing)")

    nb.set_status(sid, "running", "applying device configs…")
    applied, failed = apply_configs(slug)
    if failed:
        nb.set_status(sid, "failed", f"applied {applied} configs, {failed} failed")
        return
    nb.set_status(sid, "deployed", f"{applied} nodes live; BGP/EVPN converging")
    log(f"  {name}: deployed ({applied} nodes).")


def main():
    ap = argparse.ArgumentParser(description="AHEAD ZTP host agent (watch + deploy).")
    ap.add_argument("--url", default=os.environ.get("NETBOX_URL", "http://localhost:8888"))
    ap.add_argument("--token", default=os.environ.get("NETBOX_TOKEN",
                    "0123456789abcdef0123456789abcdef01234567"))
    ap.add_argument("--interval", type=int, default=5, help="Poll interval seconds (default 5)")
    ap.add_argument("--once", action="store_true", help="Process current requests then exit")
    args = ap.parse_args()

    nb = NB(args.url, args.token)
    log(f"watching {args.url} for deploy requests (Ctrl+C to stop). Repo: {REPO}")
    try:
        while True:
            try:
                for site in nb.requested_sites():
                    deploy_site(nb, site)
            except Exception as e:
                log(f"poll error (will retry): {e}")
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log("stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
