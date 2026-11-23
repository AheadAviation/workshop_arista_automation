"""Container lab control, through a scoped Docker proxy (INIT-002/SPEC-002).

**The security decision, stated plainly.** Reading and driving the containerlab topology needs
Docker API access. The obvious way is to mount `/var/run/docker.sock` into NetBox. That gives
the container complete control of the Docker daemon — which is root on the host — and it is the
single worst thing a CISO could see in a demo aimed at a hospital chain. So instead:

* NetBox reaches Docker only through `tecnativa/docker-socket-proxy`, which exposes a *read*
  API plus container start/stop and nothing else.
* The proxy is the only service holding the socket.
* Every call here is a fixed, named operation. There is no "run this container" endpoint, no
  `exec`, and no way to build or pull from the page.

**What this module can and cannot do**, so the page does not overclaim:

* can: list containers, report their state, start and stop the lab topology via the existing
  `clab` runner container;
* cannot: exec into a container, run arbitrary commands, pull images, or touch the host.

Deploy and destroy shell out to the `clab` runner the way `run.ps1` does, because containerlab
inside that container already owns the privilege — the plugin never gains it.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from . import conf

#: Containers this plugin will ever report on. Anything else is ignored, so a page cannot be
#: used as a general-purpose container browser.
LAB_PREFIX = "clab-netbox-arista-lab-"

#: The topology file, relative to the `clab` service's `/clab/topology` mount.
TOPOLOGY_IN_CONTAINER = "/clab/topology/topology-demo.clab.yml"

#: A node is "up" when Docker reports it running. Anything else is reported as-is rather than
#: guessed at, because "unknown" is more useful than a wrong green.
_RUNNING = "running"


def _proxy_get(path: str, timeout: float = 10.0) -> Any:
    """GET from the Docker proxy. Raises `LabError` with a readable reason on any failure."""
    import requests

    url = f"{conf.docker_proxy_url()}{path}"
    try:
        response = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise LabError(
            f"Could not reach the Docker proxy at {conf.docker_proxy_url()}: {exc}. "
            "Is the docker-proxy service running?"
        ) from exc
    if response.status_code == 403:
        raise LabError(
            f"The Docker proxy refused {path}. That endpoint is deliberately not enabled "
            "for this plugin."
        )
    if response.status_code >= 400:
        raise LabError(f"The Docker proxy answered {response.status_code} for {path}")
    try:
        return response.json()
    except ValueError as exc:
        raise LabError(f"The Docker proxy returned something that is not JSON for {path}") from exc


class LabError(RuntimeError):
    """A lab operation could not be completed. The message is fit to show a person."""


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #


def lab_status() -> dict[str, Any]:
    """The lab's containers, their state, and whether the topology is up.

    A container the plugin does not recognise is ignored rather than listed. If the proxy
    itself is unreachable, that is reported as a failure with the reason — never as an empty
    lab, because "no containers" and "I could not look" mean very different things to someone
    about to present.
    """
    try:
        containers = _proxy_get("/containers/json?all=1")
    except LabError as exc:
        return {
            "ok": False,
            "headline": "Could not read the container lab",
            "detail": str(exc),
            "nodes": [],
            "running": 0,
            "total": 0,
        }

    nodes: list[dict[str, Any]] = []
    for container in containers:
        names = container.get("Names") or []
        name = (names[0].lstrip("/") if names else "").strip()
        if not name.startswith(LAB_PREFIX):
            continue
        state = str(container.get("State") or "unknown")
        short = name[len(LAB_PREFIX):]
        nodes.append(
            {
                "name": short,
                "container": name,
                "state": state,
                "up": state == _RUNNING,
                "image": container.get("Image") or "",
                "status": container.get("Status") or "",
            }
        )

    nodes.sort(key=lambda n: (not n["up"], n["name"]))
    running = sum(1 for n in nodes if n["up"])
    if not nodes:
        headline = "No lab containers found"
        detail = "The demo topology is not deployed. Deploy it, or run .\\run.ps1 workbench-lab."
    elif running == len(nodes):
        headline = f"All {running} lab nodes are up"
        detail = ""
    else:
        headline = f"{running} of {len(nodes)} lab nodes are up"
        detail = "Nodes listed below in red are not running."

    return {
        "ok": True,
        "headline": headline,
        "detail": detail,
        "nodes": nodes,
        "running": running,
        "total": len(nodes),
    }


# --------------------------------------------------------------------------- #
# Deploy and destroy
# --------------------------------------------------------------------------- #


def _run_clab(verb: str) -> dict[str, Any]:
    """Run `containerlab <verb>` inside the repo's own `clab` runner container.

    The plugin does not get Docker privileges for this: it invokes `docker compose run` on the
    host, exactly as `run.ps1` does, and containerlab does the work inside the container that
    already owns that privilege.
    """
    if verb not in ("deploy", "destroy"):
        raise LabError(f"refusing an unknown containerlab verb {verb!r}")

    args = [
        "docker", "compose", "run", "--rm", "clab",
        "containerlab", verb,
    ]
    if verb == "deploy":
        args += ["--reconfigure"]
    else:
        args += ["--cleanup"]
    args += ["--topo", TOPOLOGY_IN_CONTAINER]

    try:
        proc = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            args,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except FileNotFoundError as exc:
        raise LabError(f"docker is not available: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise LabError(
            f"containerlab {verb} did not finish in ten minutes. Check `docker compose logs clab`."
        ) from exc

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise LabError(f"containerlab {verb} failed: {' / '.join(tail) or 'no output'}")

    return {
        "ok": True,
        "headline": f"containerlab {verb} completed",
        "detail": "",
        **lab_status(),
    }


def deploy() -> dict[str, Any]:
    """Bring the demo topology up."""
    try:
        return _run_clab("deploy")
    except LabError as exc:
        return {"ok": False, "headline": "Deploy failed", "detail": str(exc), "nodes": []}


def destroy() -> dict[str, Any]:
    """Tear the demo topology down."""
    try:
        return _run_clab("destroy")
    except LabError as exc:
        return {"ok": False, "headline": "Destroy failed", "detail": str(exc), "nodes": []}


__all__ = ["LabError", "LAB_PREFIX", "deploy", "destroy", "lab_status"]
