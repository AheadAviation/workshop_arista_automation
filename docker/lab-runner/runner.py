"""A scoped HTTP wrapper around ContainerLab and Workshop automation (INIT-002/SPEC-002, INIT-004/SPEC-001).

Exposes fixed, parameter-free endpoints only:
  * Legacy demo endpoints (unchanged): `POST /deploy`, `POST /destroy`
  * Workshop Quick Start & Day-2 endpoints (`INIT-004/SPEC-001`):
      - `POST /workshop/clean`           — destroy wiki lab + remove generated configs & topology
      - `POST /workshop/topology`        — run `scripts/generate_clab_topology_docker.py`
      - `POST /workshop/generate-configs`— run `playbooks/generate_configs.yml`
      - `POST /workshop/deploy`          — deploy `/clab/topology/topology-docker.clab.yml`
      - `POST /workshop/destroy`         — destroy `/clab/topology/topology-docker.clab.yml`
      - `POST /workshop/push-configs`    — apply full rendered configs to all 6 cEOS switches
      - `POST /workshop/backup`          — save `running-config` to `flash:backup.cfg` on all 6 switches
      - `POST /workshop/rollback`        — `configure replace flash:backup.cfg` on all 6 switches
      - `POST /workshop/validate`        — run `playbooks/test_all.yml`
      - `POST /workshop/validate-quick`  — run `playbooks/test_all.yml --tags quick`

NetBox calls this instead of holding Docker access, keeping the plugin's security posture intact:
there is no path parameter, no shell interpolation, and no arbitrary command execution.
"""

from __future__ import annotations

import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

#: The demo topology (INIT-002), addressed by the path the clab sidecar sees.
TOPOLOGY = "/clab/topology/topology-demo.clab.yml"

#: The wiki Quick Start topology generated from NetBox (INIT-004/SPEC-001).
WORKSHOP_TOPOLOGY = "/clab/topology/topology-docker.clab.yml"

#: Where the repo is mounted read-only inside this container.
COMPOSE_DIR = "/compose"

#: Fixed allowlist of the six Arista cEOS fabric devices.
FABRIC_DEVICES = ("spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04")

#: Container name prefix used by ContainerLab for this fabric.
CONTAINER_PREFIX = "clab-netbox-arista-lab-"

TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "900"))


def restore_host_project_dir() -> str:
    """The repo's path as the HOST sees it — needed for compose's bind mounts."""
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
    """The project directory to hand `docker compose` — the container's path, for `-f`."""
    return COMPOSE_DIR


def restore_compose_project_name() -> str:
    """Read this container's `com.docker.compose.project` label so nested compose runs share the
    exact same Compose project and network (where `http://netbox:8080` lives)."""
    cached = getattr(restore_compose_project_name, "_cached", "")
    if cached:
        return cached

    container = os.environ.get("HOSTNAME", "")
    try:
        proc = subprocess.run(  # noqa: S603 - argument list, no shell
            [
                "docker",
                "inspect",
                container,
                "--format",
                "{{index .Config.Labels \"com.docker.compose.project\"}}",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""

    if proc.returncode == 0:
        found = (proc.stdout or "").strip()
        if found and found != "<no value>":
            restore_compose_project_name._cached = found  # type: ignore[attr-defined]
            return found
    return ""


def _compose_base() -> tuple[list[str], dict[str, str]]:
    """Build the base `docker compose` argument list and environment with host mount override."""
    host_repo = restore_host_project_dir()
    project_name = restore_compose_project_name()
    args = ["docker", "compose"]
    if project_name:
        args += ["-p", project_name]
    args += [
        "--project-directory",
        COMPOSE_DIR,
        "-f",
        f"{COMPOSE_DIR}/docker-compose.yml",
    ]
    if host_repo:
        args += ["-f", f"{COMPOSE_DIR}/docker-compose.lab-from-container.yml"]
    env = dict(os.environ, HOST_REPO=host_repo)
    return args, env


def _run_subprocess(
    args: list[str],
    *,
    action: str,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout: int | None = None,
    tail_lines: int = 60,
) -> dict:
    """Run a fixed subprocess command and return a structured JSON-safe dict."""
    limit = timeout if timeout is not None else TIMEOUT
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            args,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=limit,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        return {"ok": False, "action": action, "error": f"command not available: {exc}", "output": ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "action": action, "error": f"{action} exceeded {limit}s", "output": ""}

    output = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
    lines = output.splitlines()
    return {
        "ok": proc.returncode == 0,
        "action": action,
        "returncode": proc.returncode,
        "output": "\n".join(lines[-tail_lines:]),
    }


def run_clab(verb: str, *, topology: str = TOPOLOGY) -> dict:
    """Run `containerlab <verb>` through the repo's own clab runner container."""
    if verb not in ("deploy", "destroy"):
        return {"ok": False, "error": f"unknown verb {verb!r}"}

    args, env = _compose_base()
    args += ["run", "--rm", "clab", "containerlab", verb]
    if verb == "deploy":
        args.append("--reconfigure")
    else:
        args.append("--cleanup")
    args += ["--topo", topology]

    result = _run_subprocess(args, action=f"containerlab {verb}", env=env, tail_lines=40)
    result["verb"] = verb
    return result


def run_ansible_action(action: str, service_cmd: list[str], *, tail_lines: int = 80) -> dict:
    """Run a fixed command inside the `ansible` compose service without starting dependencies."""
    args, env = _compose_base()
    args += ["run", "--rm", "--no-deps", "ansible", *service_cmd]
    return _run_subprocess(args, action=action, env=env, tail_lines=tail_lines)


def workshop_clean() -> dict:
    """Tear down any running workshop or demo lab and remove generated configs & topology file."""
    destroy_workshop = run_clab("destroy", topology=WORKSHOP_TOPOLOGY)
    destroy_demo = run_clab("destroy", topology=TOPOLOGY)
    clean_files_cmd = [
        "python",
        "-c",
        (
            "import glob, os, shutil; "
            "removed = []; "
            "[os.remove(p) or removed.append(p) for p in glob.glob('/ansible/output/configs/*.cfg')]; "
            "topo = '/ansible/clab/topology-docker.clab.yml'; "
            "os.path.exists(topo) and (os.remove(topo) or removed.append(topo)); "
            "[shutil.rmtree(d, ignore_errors=True) or removed.append(d) "
            "for d in glob.glob('/ansible/clab/clab-*') if os.path.isdir(d)]; "
            "print(f'Removed {len(removed)} generated file(s)/lab state dir(s)')"
        ),
    ]
    files_res = run_ansible_action("clean generated files", clean_files_cmd, tail_lines=20)
    combined = "\n".join(
        part
        for part in (
            destroy_workshop.get("output", ""),
            destroy_demo.get("output", ""),
            files_res.get("output", ""),
        )
        if part
    )
    return {
        "ok": files_res["ok"],
        "action": "workshop_clean",
        "lab_destroy": destroy_workshop,
        "demo_destroy": destroy_demo,
        "files_clean": files_res,
        "output": combined,
    }


def workshop_import_netbox() -> dict:
    """Quick Start Step 3: run `scripts/full_import.py` inside the ansible container."""
    return run_ansible_action(
        "import_netbox",
        ["python", "scripts/full_import.py"],
        tail_lines=80,
    )


def workshop_generate_topology() -> dict:
    """Quick Start Step 4: generate `clab/topology-docker.clab.yml` from NetBox."""
    return run_ansible_action(
        "generate_topology",
        ["python", "scripts/generate_clab_topology_docker.py"],
        tail_lines=60,
    )


def workshop_generate_configs() -> dict:
    """Quick Start Step 5 / Day 2: render device configs from NetBox via Ansible."""
    return run_ansible_action(
        "generate_configs",
        ["ansible-playbook", "playbooks/generate_configs.yml"],
        tail_lines=80,
    )


def workshop_push_configs() -> dict:
    """Quick Start Step 7 / Day 2: push full rendered configs to all 6 running cEOS switches."""
    configs_dir = Path(COMPOSE_DIR) / "output" / "configs"
    results: list[dict] = []
    failures = 0

    for dev in FABRIC_DEVICES:
        cfg_path = configs_dir / f"{dev}.cfg"
        if not cfg_path.exists():
            results.append({"device": dev, "ok": False, "error": f"missing {cfg_path.name}"})
            failures += 1
            continue

        cfg_lines = cfg_path.read_text(encoding="utf-8").splitlines()
        while cfg_lines and cfg_lines[-1].strip() in ("", "!", "end"):
            cfg_lines.pop()
        cfg_text = "\n".join(cfg_lines)
        container = f"{CONTAINER_PREFIX}{dev}"
        payload = f"configure\n{cfg_text}\nend\nwrite memory\n"
        res = _run_subprocess(
            ["docker", "exec", "-i", container, "Cli", "-p", "15"],
            action=f"push {dev}",
            input_text=payload,
            timeout=120,
            tail_lines=20,
        )
        out = res.get("output", "")
        has_cli_error = any(marker in out for marker in ("% Invalid input", "% Error", "% Ambiguous"))
        ok = bool(res.get("ok")) and not has_cli_error
        if not ok:
            failures += 1
        results.append(
            {
                "device": dev,
                "ok": ok,
                "output": out,
                "error": "" if ok else (res.get("error") or out[-300:] or "CLI error"),
            }
        )

    return {
        "ok": failures == 0,
        "action": "push_configs",
        "devices": results,
        "succeeded": len(results) - failures,
        "failed": failures,
        "output": "\n".join(
            f"{r['device']}: {'OK' if r['ok'] else 'FAIL - ' + r['error']}" for r in results
        ),
    }


def _switch_cli_all(action: str, cli_command: str) -> dict:
    """Run one fixed EOS CLI command on all 6 fabric switches via `docker exec`."""
    results: list[dict] = []
    failures = 0
    for dev in FABRIC_DEVICES:
        container = f"{CONTAINER_PREFIX}{dev}"
        res = _run_subprocess(
            ["docker", "exec", container, "Cli", "-p", "15", "-c", cli_command],
            action=f"{action} {dev}",
            timeout=90,
            tail_lines=15,
        )
        out = res.get("output", "")
        ok = bool(res.get("ok")) and "% " not in out
        if not ok:
            failures += 1
        results.append(
            {
                "device": dev,
                "ok": ok,
                "output": out,
                "error": "" if ok else (res.get("error") or out[-200:] or "CLI error"),
            }
        )

    return {
        "ok": failures == 0,
        "action": action,
        "devices": results,
        "succeeded": len(results) - failures,
        "failed": failures,
        "output": "\n".join(
            f"{r['device']}: {'OK' if r['ok'] else 'FAIL - ' + r['error']}" for r in results
        ),
    }


def workshop_backup() -> dict:
    """Day 2: save running-config to flash:backup.cfg on all 6 switches."""
    return _switch_cli_all("backup", "copy running-config flash:backup.cfg")


def workshop_rollback() -> dict:
    """Day 2: restore flash:backup.cfg on all 6 switches via configure replace."""
    return _switch_cli_all("rollback", "configure replace flash:backup.cfg")


def workshop_validate(*, quick: bool = False) -> dict:
    """Quick Start Step 9 / Day 2: run `playbooks/test_all.yml` (full or `--tags quick`)."""
    cmd = ["ansible-playbook", "-i", "inventory/hosts.yml", "playbooks/test_all.yml"]
    if quick:
        cmd += ["--tags", "quick"]
    return run_ansible_action("validate_quick" if quick else "validate", cmd, tail_lines=120)


#: Fixed POST dispatch table — no path parameters, no dynamic dispatch.
POST_ROUTES = {
    "/deploy": lambda: run_clab("deploy", topology=TOPOLOGY),
    "/destroy": lambda: run_clab("destroy", topology=TOPOLOGY),
    "/workshop/clean": workshop_clean,
    "/workshop/import": workshop_import_netbox,
    "/workshop/topology": workshop_generate_topology,
    "/workshop/generate-configs": workshop_generate_configs,
    "/workshop/deploy": lambda: run_clab("deploy", topology=WORKSHOP_TOPOLOGY),
    "/workshop/destroy": lambda: run_clab("destroy", topology=WORKSHOP_TOPOLOGY),
    "/workshop/push-configs": workshop_push_configs,
    "/workshop/backup": workshop_backup,
    "/workshop/rollback": workshop_rollback,
    "/workshop/validate": lambda: workshop_validate(quick=False),
    "/workshop/validate-quick": lambda: workshop_validate(quick=True),
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
            self._send(
                {
                    "ok": True,
                    "service": "lab-runner",
                    "topology": TOPOLOGY,
                    "workshop_topology": WORKSHOP_TOPOLOGY,
                    "endpoints": sorted(POST_ROUTES),
                }
            )
            return
        self._send({"ok": False, "error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.rstrip("/")
        handler = POST_ROUTES.get(path)
        if handler is None:
            self._send({"ok": False, "error": "not found"}, status=404)
            return
        self._send(handler())

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"lab-runner: {fmt % args}", flush=True)


def main() -> None:
    port = int(os.environ.get("PORT", "8077"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"lab-runner listening on :{port}, topology {TOPOLOGY}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
