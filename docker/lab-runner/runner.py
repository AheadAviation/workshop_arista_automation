"""A two-verb HTTP wrapper around containerlab (INIT-002/SPEC-002).

Exposes exactly `POST /deploy` and `POST /destroy`, nothing else. NetBox calls this instead of
holding Docker access, which keeps the plugin's security posture intact: the only service with
the Docker socket is this one, and the worst it can be asked to do is start or stop the demo
topology.

Deliberately not a general command runner. There is no path parameter, no shell, and no way to
name a different topology — the arguments are fixed here, and the topology is the demo's.
"""

from __future__ import annotations

import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

#: The topology, addressed by the path the **daemon** can see. The repo is bind-mounted at
#: /compose here, and the clab sidecar gets ./clab mounted at /clab/topology.
TOPOLOGY = "/clab/topology/topology-demo.clab.yml"

#: Where the repo is mounted, so `docker compose` finds the compose file.
COMPOSE_DIR = "/compose"

#: The SAME repo, but by its path on the *host*.
#:
#: This is not a duplicate — it is the docker-out-of-docker path problem. Compose resolves
#: relative bind mounts (`./clab:/clab/topology`) against the host filesystem, not against this
#: container's view of it. Running compose with `--project-directory /compose` made it look for
#: `/compose/clab` *on the host*, which does not exist, so containerlab reported
#: "Stat /clab/topology/topology-demo.clab.yml: no such file or directory".
#:
#: It is resolved at run time by asking the daemon where this container's `/compose` mount came
#: from (see `host_project_dir`), rather than configured — a hardcoded path breaks the moment
#: someone clones the repo somewhere else, and an env var is one more thing to get wrong.
COMPOSE_DIR = "/compose"

TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "900"))


def restore_host_project_dir() -> str:
    """The repo's path as the HOST sees it — needed for compose's bind mounts.

    Compose resolves relative bind mounts to absolute paths and hands them to the *daemon*, which
    runs on the host. The host does not have `/compose`; it has `C:\\Users\\...\\workshop_arista_automation`.
    So a nested `docker compose run` must be given host paths, and the only reliable way to learn
    them is to ask the daemon where this container's own `/compose` bind came from.

    This was the first thing tried, then abandoned in favour of the container path — which was
    wrong for the mounts and right only for locating the compose file. Both are needed: the file
    by its container path, the mounts by their host path.
    """
    cached = getattr(restore_host_project_dir, "_cached", "")
    if cached:
        return cached

    container = os.environ.get("HOSTNAME", "")
    try:
        proc = subprocess.run(  # noqa: S603 - argument list, no shell
            ["docker", "inspect", container, "--format", "{{json .Mounts}}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""

    if proc.returncode == 0:
        try:
            mounts = json.loads(proc.stdout or "[]")
        except ValueError:
            mounts = []
        for mount in mounts:
            if str(mount.get("Destination", "")).rstrip("/") == COMPOSE_DIR:
                found = str(mount.get("Source", ""))
                restore_host_project_dir._cached = found  # type: ignore[attr-defined]
                return found
    return ""


def host_project_dir() -> str:
    """The project directory to hand `docker compose` — the **container's** path, for `-f`.

    Resolves the opposite way to `restore_host_project_dir`, and both are needed: compose is told
    where the compose *file* is by its container path, and the file's mounts are rewritten to host
    paths by the override file below.
    """
    return COMPOSE_DIR


def run_clab(verb: str) -> dict:
    """Run `containerlab <verb>` through the repo's own clab runner container."""
    if verb not in ("deploy", "destroy"):
        return {"ok": False, "error": f"unknown verb {verb!r}"}

    # Compose is pointed at the compose FILE by its container path, and at the override file that
    # rewrites the clab service's mounts to HOST paths. Both are required: compose runs here and
    # reads the file here, but hands mount sources to the daemon, which runs on the host.
    host_repo = restore_host_project_dir()
    args = [
        "docker", "compose",
        "--project-directory", COMPOSE_DIR,
        "-f", f"{COMPOSE_DIR}/docker-compose.yml",
    ]
    if host_repo:
        args += ["-f", f"{COMPOSE_DIR}/docker-compose.lab-from-container.yml"]
    env = dict(os.environ, HOST_REPO=host_repo)

    args += ["run", "--rm", "clab", "containerlab", verb]
    if verb == "deploy":
        args.append("--reconfigure")
    else:
        args.append("--cleanup")
    args += ["--topo", TOPOLOGY]

    try:
        proc = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            args,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        return {"ok": False, "error": f"docker is not available: {exc}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"containerlab {verb} exceeded {TIMEOUT}s"}

    output = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
    return {
        "ok": proc.returncode == 0,
        "verb": verb,
        "returncode": proc.returncode,
        # The tail, because the useful line (a failure reason) is at the end, and the whole
        # deploy log is thousands of lines of container output.
        "output": "\n".join(output.splitlines()[-40:]),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        if self.path.rstrip("/") == "/health":
            self._send({"ok": True, "service": "lab-runner", "topology": TOPOLOGY})
            return
        self._send({"ok": False, "error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.rstrip("/")
        if path == "/deploy":
            self._send(run_clab("deploy"))
            return
        if path == "/destroy":
            self._send(run_clab("destroy"))
            return
        self._send({"ok": False, "error": "not found"}, status=404)

    def log_message(self, fmt: str, *args: object) -> None:
        # Containerlab output is the interesting part; keep the access log quiet.
        print(f"lab-runner: {fmt % args}", flush=True)


def main() -> None:
    port = int(os.environ.get("PORT", "8077"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"lab-runner listening on :{port}, topology {TOPOLOGY}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
