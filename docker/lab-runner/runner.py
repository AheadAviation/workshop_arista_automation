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

#: The only topology this service will act on. A request cannot change it.
TOPOLOGY = "/clab/topology/topology-demo.clab.yml"

#: Where the repo is mounted, so `docker compose` finds the compose file and the clab service.
COMPOSE_DIR = "/compose"

TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "900"))


def run_clab(verb: str) -> dict:
    """Run `containerlab <verb>` through the repo's own clab runner container."""
    if verb not in ("deploy", "destroy"):
        return {"ok": False, "error": f"unknown verb {verb!r}"}

    args = [
        "docker", "compose",
        "--project-directory", COMPOSE_DIR,
        "run", "--rm", "clab",
        "containerlab", verb,
    ]
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
