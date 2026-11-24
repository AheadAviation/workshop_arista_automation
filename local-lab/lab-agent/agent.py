"""Local Lab Agent for NetBox Demo (INIT-001/SPEC-003).

Runs on the ContainerLab host and is reached from NetBox Enterprise Cloud over
Tailscale. NetBox Cloud never holds Docker credentials; this service exposes only
fixed, allowlisted, Bearer-token-authenticated operations:

    GET  /ping                      liveness (unauthenticated)
    GET  /health                    agent, ContainerLab and Orb Agent status
    POST /actions/destroy-lab       destroy topology, wipe cEOS flash and configs
    POST /actions/deploy-bare-lab   write Day-0 configs and deploy the fabric
    POST /actions/run-discovery     one-shot Orb Agent sweep into NetBox Cloud
    POST /actions/push-configs      push rendered EOS configs from NetBox intent
    POST /actions/validate-fabric   parallel eAPI health checks
    POST /actions/eapi              allowlisted per-device eAPI proxy
    POST /actions/inject-rogue-drift   inject reversible CLI drift
    POST /actions/remediate-drift      remove that drift again

Every handler returns `{"ok": bool, ...}`. The HTTP status mirrors `ok` so a
caller cannot mistake a failed lab action for a successful one.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests
import urllib3

from contract import (
    AGENT_VERSION,
    CEOS_IMAGE,
    DEVICE_MGMT_IPS,
    FABRIC_DEVICES,
    LEAVES,
    MGMT_GATEWAY,
    MGMT_SUBNET,
    ROGUE_DRIFT_DEVICE,
    ROGUE_DRIFT_SCENARIOS,
    TOPOLOGY_NAME,
    render_topology_yaml,
    validate_device,
    validate_eapi_commands,
    verify_bearer_token,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BIND_HOST = os.environ.get("LAB_AGENT_BIND_HOST", "0.0.0.0")
BIND_PORT = int(os.environ.get("LAB_AGENT_PORT", "8085"))
AGENT_TOKEN = os.environ.get("LAB_AGENT_TOKEN", "")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "900"))

#: Repo folder mounted at /local-lab inside this container.
LAB_DIR = Path(os.environ.get("LAB_COMPOSE_DIR", "/local-lab"))
CLAB_DIR = LAB_DIR / "clab"
BARE_CONFIGS_DIR = CLAB_DIR / "bare-configs"
GENERATED_CONFIGS_DIR = CLAB_DIR / "generated-configs"
TOPOLOGY_FILE = CLAB_DIR / "topology-generated.clab.yml"

LAB_BACKEND = os.environ.get("LAB_BACKEND", "docker").strip().lower()
K8S_NAMESPACE = os.environ.get("K8S_NAMESPACE", TOPOLOGY_NAME).strip()
K8S_ORB_NAMESPACE = os.environ.get("K8S_ORB_NAMESPACE", K8S_NAMESPACE).strip()
K8S_ORB_DEPLOYMENT = os.environ.get("K8S_ORB_DEPLOYMENT", "orb-agent").strip()
K8S_EAPI_HOST_TEMPLATE = os.environ.get(
    "K8S_EAPI_HOST_TEMPLATE", "{device}-eapi.{namespace}.svc.cluster.local"
).strip()
K8S_TOPO_FILE = os.environ.get("K8S_TOPO_FILE", "/lab/topology.clab.yml").strip()
K8S_DEPLOY_CMD = os.environ.get("K8S_DEPLOY_CMD", "").strip()
K8S_DESTROY_CMD = os.environ.get("K8S_DESTROY_CMD", "").strip()
K8S_DISCOVERY_CMD = os.environ.get("K8S_DISCOVERY_CMD", "").strip()
DEVICE_EAPI_HOSTS_RAW = os.environ.get("DEVICE_EAPI_HOSTS", "").strip()

CONTAINER_PREFIX = f"clab-{TOPOLOGY_NAME}-"
EOS_USER = os.environ.get("EOS_USER", "admin")
EOS_PASS = os.environ.get("EOS_PASS", "admin")
SNMP_COMMUNITY = os.environ.get("SNMP_COMMUNITY", "FABRIC-RO")
ORB_MODE = os.environ.get("ORB_MODE", "diode").strip().lower()
ORB_DISCOVERY_TIMEOUT = int(os.environ.get("ORB_DISCOVERY_TIMEOUT", "240"))


def _parse_eapi_host_overrides(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if str(v).strip()}


def device_eapi_host(device: str) -> str:
    """Resolve the eAPI host/IP for an allowlisted fabric switch."""
    dev = validate_device(device)
    overrides = _parse_eapi_host_overrides(DEVICE_EAPI_HOSTS_RAW)
    if dev in overrides:
        return overrides[dev]
    if LAB_BACKEND == "k8s":
        return K8S_EAPI_HOST_TEMPLATE.format(device=dev, namespace=K8S_NAMESPACE)
    return DEVICE_MGMT_IPS[dev]


# --------------------------------------------------------------------------- #
# Day-0 bare configuration
# --------------------------------------------------------------------------- #


def render_bare_startup_config(device: str) -> str:
    """Minimal cEOS config: management reachability, eAPI, SNMP and LLDP only.

    Nothing here is fabric intent. It exists so the switch is reachable and
    discoverable while NetBox is still empty, which is the point of the demo:
    discovery populates NetBox, not the other way around.

    `vrf instance MGMT` and `ip routing vrf MGMT` must come *before* anything that
    references the VRF. EOS aborts the remainder of a startup-config when a command
    references a VRF that does not exist yet, which leaves the switch at its
    default hostname with no eAPI — and looks like a switch that simply did not
    boot rather than a config-order mistake.
    """
    mgmt_ip = DEVICE_MGMT_IPS[device]
    return (
        f"! Day-0 bootstrap for {device} (no fabric intent - discovery populates NetBox)\n"
        f"hostname {device}\n"
        "!\n"
        "no aaa root\n"
        "!\n"
        f"username {EOS_USER} privilege 15 role network-admin secret 0 {EOS_PASS}\n"
        "!\n"
        "vrf instance MGMT\n"
        "ip routing vrf MGMT\n"
        "!\n"
        "management api http-commands\n"
        "   protocol https\n"
        "   no shutdown\n"
        "   vrf MGMT\n"
        "      no shutdown\n"
        "!\n"
        "interface Management0\n"
        "   description OOB management\n"
        "   vrf MGMT\n"
        f"   ip address {mgmt_ip}/24\n"
        "   no shutdown\n"
        "!\n"
        "ip routing\n"
        f"ip route vrf MGMT 0.0.0.0/0 {MGMT_GATEWAY}\n"
        "!\n"
        "lldp run\n"
        "!\n"
        f"snmp-server community {SNMP_COMMUNITY} ro\n"
        "snmp-server vrf MGMT\n"
        "!\n"
        "end\n"
    )


def write_bare_configs() -> list[str]:
    """Write one Day-0 config per switch and the matching topology file."""
    BARE_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for dev in FABRIC_DEVICES:
        path = BARE_CONFIGS_DIR / f"{dev}.cfg"
        path.write_text(render_bare_startup_config(dev), encoding="utf-8")
        written.append(path.name)

    TOPOLOGY_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOPOLOGY_FILE.write_text(render_topology_yaml(), encoding="utf-8")
    written.append(TOPOLOGY_FILE.name)
    return written


# --------------------------------------------------------------------------- #
# Subprocess plumbing
# --------------------------------------------------------------------------- #


def _host_lab_dir() -> str:
    """Host-side path of the mounted lab folder, for nested compose bind mounts."""
    cached = getattr(_host_lab_dir, "_cached", "")
    if cached:
        return cached
    container = os.environ.get("HOSTNAME", "")
    if not container:
        return ""
    try:
        proc = subprocess.run(
            ["docker", "inspect", container, "--format", "{{json .Mounts}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    try:
        mounts = json.loads(proc.stdout or "[]")
    except ValueError:
        return ""
    for mount in mounts:
        if str(mount.get("Destination", "")).rstrip("/") == str(LAB_DIR).rstrip("/"):
            found = str(mount.get("Source", ""))
            _host_lab_dir._cached = found  # type: ignore[attr-defined]
            return found
    return ""


def _compose_cmd() -> tuple[list[str], dict[str, str]]:
    """Build the `docker compose` invocation, including the in-container override."""
    env = dict(os.environ)
    host_dir = _host_lab_dir()
    args = [
        "docker",
        "compose",
        "-p",
        TOPOLOGY_NAME,
        "--project-directory",
        str(LAB_DIR),
        "-f",
        str(LAB_DIR / "docker-compose.yml"),
    ]
    if host_dir:
        # Compose resolves relative bind mounts against the project directory,
        # which is a container path here and does not exist on the host daemon.
        # The override rewrites them to the real host path.
        env["HOST_LOCAL_LAB_DIR"] = host_dir
        override = LAB_DIR / "docker-compose.from-container.yml"
        if override.exists():
            args += ["-f", str(override)]
    return args, env


def _run(
    args: list[str],
    *,
    action: str,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    tail: int = 80,
) -> dict[str, Any]:
    limit = timeout or REQUEST_TIMEOUT
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=limit,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        return {"ok": False, "action": action, "error": f"command not found: {exc}", "output": ""}
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "action": action,
            "error": f"{action} exceeded {limit}s",
            "output": "",
        }

    output = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
    return {
        "ok": proc.returncode == 0,
        "action": action,
        "returncode": proc.returncode,
        "output": "\n".join(output.splitlines()[-tail:]),
    }


def _docker_ps() -> list[dict[str, str]]:
    try:
        proc = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.State}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    rows: list[dict[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append({"name": parts[0], "status": parts[1], "state": parts[2]})
    return rows


def _kubectl_pods(namespace: str) -> list[dict[str, Any]]:
    try:
        proc = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        return []
    items = data.get("items", [])
    return items if isinstance(items, list) else []


def _k8s_pod_ready(pod: dict[str, Any]) -> tuple[bool, str]:
    status = pod.get("status", {}) if isinstance(pod, dict) else {}
    phase = str(status.get("phase", "Unknown"))
    if phase != "Running":
        return False, phase
    conditions = status.get("conditions", []) or []
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions if isinstance(c, dict))
    return ready, "Running (Ready)" if ready else "Running (NotReady)"


def _match_fabric_device_from_pod(pod: dict[str, Any]) -> str | None:
    meta = pod.get("metadata", {}) if isinstance(pod, dict) else {}
    name = str(meta.get("name", ""))
    labels = meta.get("labels", {}) or {}
    for label_key in ("clabernetes/topologyNode", "app.kubernetes.io/name", "app"):
        val = str(labels.get(label_key, ""))
        if val in FABRIC_DEVICES:
            return val
    # Fallback: longest device name contained in pod name (avoids prefix collisions).
    for dev in sorted(FABRIC_DEVICES, key=len, reverse=True):
        if dev in name:
            return dev
    return None


def _inspect_lab_k8s() -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Inspect cEOS pods and the persistent Orb Agent pod via kubectl."""
    fabric_by_dev: dict[str, dict[str, str]] = {}
    orb: dict[str, Any] = {"mode": ORB_MODE, "running": False, "status": "not found"}

    pods = _kubectl_pods(K8S_NAMESPACE)
    orb_pods = pods if K8S_ORB_NAMESPACE == K8S_NAMESPACE else _kubectl_pods(K8S_ORB_NAMESPACE)

    for pod in pods:
        dev = _match_fabric_device_from_pod(pod)
        if not dev:
            continue
        meta = pod.get("metadata", {}) or {}
        ready, status_text = _k8s_pod_ready(pod)
        phase = str((pod.get("status") or {}).get("phase", "Unknown")).lower()
        fabric_by_dev[dev] = {
            "device": dev,
            "container": str(meta.get("name", "")),
            "mgmt_ip": device_eapi_host(dev),
            "status": status_text,
            "state": "running" if ready else phase,
        }

    for pod in orb_pods:
        meta = pod.get("metadata", {}) or {}
        name = str(meta.get("name", ""))
        labels = meta.get("labels", {}) or {}
        if (
            K8S_ORB_DEPLOYMENT in name
            or "orb-agent" in name
            or labels.get("app") == K8S_ORB_DEPLOYMENT
            or labels.get("app.kubernetes.io/name") == K8S_ORB_DEPLOYMENT
        ):
            ready, status_text = _k8s_pod_ready(pod)
            orb = {
                "mode": ORB_MODE,
                "container": name,
                "running": ready,
                "status": status_text,
            }
            if ready:
                break

    fabric = [fabric_by_dev[d] for d in FABRIC_DEVICES if d in fabric_by_dev]
    return fabric, orb


def inspect_lab() -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Return the demo fabric containers/pods and the Orb Agent state."""
    if LAB_BACKEND == "k8s":
        return _inspect_lab_k8s()

    fabric: list[dict[str, str]] = []
    orb: dict[str, Any] = {"mode": ORB_MODE, "running": False, "status": "not found"}
    for row in _docker_ps():
        name = row["name"]
        if name.startswith(CONTAINER_PREFIX):
            dev = name[len(CONTAINER_PREFIX) :]
            if dev in FABRIC_DEVICES:
                fabric.append(
                    {
                        "device": dev,
                        "container": name,
                        "mgmt_ip": DEVICE_MGMT_IPS.get(dev, ""),
                        "status": row["status"],
                        "state": row["state"],
                    }
                )
        elif "orb-agent" in name:
            orb = {
                "mode": ORB_MODE,
                "container": name,
                "running": row["state"] == "running",
                "status": row["status"],
            }
    fabric.sort(key=lambda item: FABRIC_DEVICES.index(item["device"]))
    return fabric, orb


# --------------------------------------------------------------------------- #
# eAPI
# --------------------------------------------------------------------------- #


def call_eapi(device: str, commands: list[str], *, fmt: str = "json", timeout: int = 20) -> dict[str, Any]:
    """Run commands on one switch over Arista eAPI."""
    dev = validate_device(device)
    host = device_eapi_host(dev)
    payload = {
        "jsonrpc": "2.0",
        "method": "runCmds",
        "params": {"version": 1, "cmds": commands, "format": fmt},
        "id": "netbox-demo-lab-agent",
    }
    try:
        resp = requests.post(
            f"https://{host}/command-api",
            json=payload,
            auth=(EOS_USER, EOS_PASS),
            verify=False,
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
    except requests.exceptions.SSLError as exc:
        return {"ok": False, "device": dev, "state": "booting", "error": f"eAPI TLS not ready: {exc}"}
    except requests.exceptions.ConnectionError as exc:
        return {"ok": False, "device": dev, "state": "offline", "error": f"Unreachable at {host}: {exc}"}
    except requests.exceptions.Timeout:
        return {"ok": False, "device": dev, "state": "booting", "error": f"eAPI timed out on {dev}"}
    except ValueError as exc:
        return {"ok": False, "device": dev, "state": "error", "error": f"Bad eAPI response: {exc}"}
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator verbatim
        return {"ok": False, "device": dev, "state": "error", "error": str(exc)}

    if "error" in body:
        err = body["error"]
        return {
            "ok": False,
            "device": dev,
            "state": "eapi_error",
            "error": err.get("message", str(err)),
            "data": err.get("data"),
        }
    return {"ok": True, "device": dev, "state": "online", "result": body.get("result", [])}


def _push_one(device: str, config_text: str) -> dict[str, Any]:
    """Write the rendered config locally and apply it to one switch."""
    dev = validate_device(device)
    GENERATED_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    (GENERATED_CONFIGS_DIR / f"{dev}.cfg").write_text(config_text, encoding="utf-8")

    lines = [
        line.rstrip()
        for line in config_text.splitlines()
        if line.strip() and not line.strip().startswith("!") and line.strip() != "end"
    ]
    cmds = ["enable", "configure terminal", *lines, "end", "write memory"]
    res = call_eapi(dev, cmds, fmt="text", timeout=60)
    if res["ok"]:
        return {"device": dev, "ok": True, "lines": len(lines)}
    return {"device": dev, "ok": False, "error": res.get("error", "push failed")}


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #


def _action_destroy_lab_k8s() -> dict[str, Any]:
    """Destroy the c9s fabric in Kubernetes (leaving persistent Orb Agent intact)."""
    cmd = (
        ["sh", "-lc", K8S_DESTROY_CMD]
        if K8S_DESTROY_CMD
        else ["containerlab", "--runtime", "c9s", "destroy", "--topo", K8S_TOPO_FILE]
    )
    res = _run(cmd, action="destroy-lab", timeout=REQUEST_TIMEOUT)
    return {
        "ok": res["ok"],
        "action": "destroy-lab",
        "backend": "k8s",
        "clab_ok": res["ok"],
        "wiped": [],
        "output": res.get("output", ""),
        "error": res.get("error", ""),
    }


def action_destroy_lab() -> dict[str, Any]:
    """Destroy the fabric, then remove every trace of persisted switch state."""
    if LAB_BACKEND == "k8s":
        return _action_destroy_lab_k8s()

    base, env = _compose_cmd()
    res = _run(
        [
            *base,
            "run",
            "--rm",
            "--entrypoint",
            "containerlab",
            "clab",
            "destroy",
            "--cleanup",
            "--topo",
            f"/clab/{TOPOLOGY_FILE.name}",
        ],
        action="destroy-lab",
        env=env,
    )

    wiped: list[str] = []
    # ContainerLab leaves `clab-<lab>-<node>/flash` behind. cEOS keeps its
    # startup-config there, so a "destroyed" lab can silently come back with old
    # config unless the flash folders are removed too.
    for pattern in ("clab-*",):
        for child in CLAB_DIR.glob(pattern):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                wiped.append(child.name)
    for cfg in GENERATED_CONFIGS_DIR.glob("*.cfg"):
        cfg.unlink(missing_ok=True)
        wiped.append(f"generated-configs/{cfg.name}")

    note = "Lab destroyed." if res["ok"] else "ContainerLab reported an error; state cleaned anyway."
    return {
        "ok": True,
        "action": "destroy-lab",
        "clab_ok": res["ok"],
        "wiped": wiped,
        "output": "\n".join(
            [
                note,
                res.get("output", ""),
                f"Removed {len(wiped)} residual item(s): {', '.join(wiped) or 'none'}",
            ]
        ).strip(),
    }


def _attach_agent_to_mgmt_network() -> str:
    """Join the fabric's management network so eAPI is reachable from here.

    ContainerLab creates `netbox-demo-fabric_mgmt` during deploy. The agent is not
    a ContainerLab node, so it has to be attached explicitly; without this every
    eAPI call fails with "connection refused" and looks like a dead switch.
    """
    network = f"{TOPOLOGY_NAME}_mgmt"
    container = os.environ.get("HOSTNAME", "")
    if not container:
        return "Could not determine own container name; attach the mgmt network manually."

    try:
        inspect = subprocess.run(
            ["docker", "inspect", container, "--format", "{{json .NetworkSettings.Networks}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "Docker unavailable; attach the mgmt network manually."

    if inspect.returncode == 0 and network in (inspect.stdout or ""):
        return f"Already attached to {network}."

    connect = subprocess.run(
        ["docker", "network", "connect", network, container],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if connect.returncode == 0:
        return f"Attached to {network}."
    return f"Could not attach to {network}: {(connect.stderr or '').strip()}"


def _push_startup_config(device: str) -> dict[str, Any]:
    """Place a node's startup-config where the switch will actually read it.

    ContainerLab copies startup-config into `<labdir>/<node>/flash/`, then asks the
    Docker daemon to bind-mount that path into the node. When ContainerLab itself
    runs in a container, `<labdir>` is a *container* path, so the daemon resolves it
    against the host filesystem where it does not exist — and silently creates an
    empty directory instead of failing. The switch then boots with its default
    config, no eAPI and the default hostname, which reads as "the switch did not
    boot" rather than "the config went to the wrong directory".

    Writing through stdin sidesteps path translation entirely: neither Docker nor
    the daemon resolves a path on the caller's side. `docker cp -` is not used
    because it requires a directory as the destination.
    """
    container = f"{CONTAINER_PREFIX}{device}"
    source = BARE_CONFIGS_DIR / f"{device}.cfg"
    try:
        content = source.read_text(encoding="utf-8")
    except OSError as exc:
        return {"device": device, "ok": False, "error": f"cannot read {source}: {exc}"}

    try:
        result = subprocess.run(
            ["docker", "exec", "-i", container, "sh", "-c", "cat > /mnt/flash/startup-config"],
            input=content,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"device": device, "ok": False, "error": str(exc)}

    if result.returncode != 0:
        return {"device": device, "ok": False, "error": (result.stderr or "").strip()}

    # Confirm it landed, rather than trusting the exit code alone.
    verify = subprocess.run(
        ["docker", "exec", container, "sh", "-c", "test -s /mnt/flash/startup-config"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if verify.returncode != 0:
        return {"device": device, "ok": False, "error": "startup-config is missing or empty in the node"}
    return {"device": device, "ok": True}


def _restart_nodes(devices: list[str]) -> None:
    """Restart nodes so they re-read startup-config."""
    try:
        subprocess.run(
            ["docker", "restart", *[f"{CONTAINER_PREFIX}{d}" for d in devices]],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _action_deploy_bare_lab_k8s() -> dict[str, Any]:
    """Deploy the bare c9s fabric in Kubernetes."""
    cmd = (
        ["sh", "-lc", K8S_DEPLOY_CMD]
        if K8S_DEPLOY_CMD
        else ["containerlab", "--runtime", "c9s", "deploy", "--topo", K8S_TOPO_FILE]
    )
    res = _run(cmd, action="deploy-bare-lab", timeout=REQUEST_TIMEOUT)
    return {
        "ok": res["ok"],
        "action": "deploy-bare-lab",
        "backend": "k8s",
        "devices": list(FABRIC_DEVICES),
        "configs_written": [],
        "output": res.get("output", ""),
        "error": res.get("error", ""),
    }


def action_deploy_bare_lab() -> dict[str, Any]:
    """Write Day-0 configs and bring up the six switches."""
    if LAB_BACKEND == "k8s":
        return _action_deploy_bare_lab_k8s()

    written = write_bare_configs()
    base, env = _compose_cmd()
    res = _run(
        [
            *base,
            "run",
            "--rm",
            "--entrypoint",
            "containerlab",
            "clab",
            "deploy",
            "--reconfigure",
            "--topo",
            f"/clab/{TOPOLOGY_FILE.name}",
        ],
        action="deploy-bare-lab",
        env=env,
        timeout=900,
    )

    if not res["ok"]:
        return {
            "ok": False,
            "action": "deploy-bare-lab",
            "devices": list(FABRIC_DEVICES),
            "configs_written": written,
            "output": res.get("output", ""),
            "error": res.get("error", ""),
        }

    # Put each node's config where it will actually be read, then restart so the
    # switches apply it. Both steps are idempotent.
    copied = [_push_startup_config(device) for device in FABRIC_DEVICES]
    failed = [item["device"] for item in copied if not item["ok"]]
    if not failed:
        _restart_nodes(list(FABRIC_DEVICES))

    network_note = _attach_agent_to_mgmt_network()

    notes = [f"startup-config placed on {len(copied) - len(failed)}/{len(FABRIC_DEVICES)} nodes."]
    if failed:
        notes.append(f"Could not place startup-config on: {', '.join(failed)}.")
    else:
        notes.append("Nodes restarted to apply it; eAPI needs roughly 60s after boot.")
    notes.append(network_note)

    return {
        "ok": not failed,
        "action": "deploy-bare-lab",
        "devices": list(FABRIC_DEVICES),
        "configs_written": written,
        "output": "\n".join(filter(None, [res.get("output", ""), *notes])),
        "error": "",
    }


def _action_run_discovery_k8s() -> dict[str, Any]:
    """Trigger an immediate discovery sweep from the persistent K8s Orb Agent."""
    if K8S_DISCOVERY_CMD:
        res = _run(
            ["sh", "-lc", K8S_DISCOVERY_CMD],
            action="run-discovery",
            timeout=ORB_DISCOVERY_TIMEOUT,
            tail=120,
        )
        return {
            "ok": res["ok"],
            "action": "run-discovery",
            "backend": "k8s",
            "mode": "k8s-custom",
            "output": res.get("output", ""),
            "error": res.get("error", ""),
            "note": (
                "Discovery streams to NetBox Cloud Diode. New objects and deviations appear in "
                "NetBox Assurance once the ingestion finishes."
            ),
        }

    restart = _run(
        [
            "kubectl",
            "rollout",
            "restart",
            f"deployment/{K8S_ORB_DEPLOYMENT}",
            "-n",
            K8S_ORB_NAMESPACE,
        ],
        action="run-discovery",
        timeout=60,
    )
    if not restart["ok"]:
        return {
            "ok": False,
            "action": "run-discovery",
            "backend": "k8s",
            "mode": "k8s-rollout-restart",
            "output": restart.get("output", ""),
            "error": restart.get("error", "failed to restart orb-agent deployment"),
        }

    status = _run(
        [
            "kubectl",
            "rollout",
            "status",
            f"deployment/{K8S_ORB_DEPLOYMENT}",
            "-n",
            K8S_ORB_NAMESPACE,
            f"--timeout={min(ORB_DISCOVERY_TIMEOUT, 180)}s",
        ],
        action="run-discovery",
        timeout=min(ORB_DISCOVERY_TIMEOUT, 180) + 15,
    )
    combined = "\n".join(filter(None, [restart.get("output", ""), status.get("output", "")]))
    return {
        "ok": status["ok"],
        "action": "run-discovery",
        "backend": "k8s",
        "mode": "k8s-rollout-restart",
        "output": combined,
        "error": status.get("error", ""),
        "note": (
            "Persistent Orb Agent restarted to trigger an immediate discovery sweep into NetBox "
            "Cloud Diode. New objects and deviations appear in NetBox Assurance once ingestion completes."
        ),
    }


def action_run_discovery() -> dict[str, Any]:
    """Trigger a one-shot Orb Agent sweep, or explain the Fleet Management path."""
    if ORB_MODE == "fleet":
        return {
            "ok": True,
            "action": "run-discovery",
            "mode": "fleet",
            "output": (
                "Orb Agent runs in NetBox Fleet Management mode, so discovery is started from the "
                "NetBox Labs Console (Assurance -> Discovery jobs). Set ORB_MODE=diode in .env to "
                "trigger a one-shot sweep from this button instead."
            ),
        }

    if LAB_BACKEND == "k8s":
        return _action_run_discovery_k8s()

    base, env = _compose_cmd()
    res = _run(
        [*base, "run", "--rm", "--no-deps", "orb-agent-oneshot"],
        action="run-discovery",
        env=env,
        timeout=ORB_DISCOVERY_TIMEOUT,
        tail=120,
    )
    return {
        "ok": res["ok"],
        "action": "run-discovery",
        "mode": "diode-oneshot",
        "output": res.get("output", ""),
        "error": res.get("error", ""),
        "note": (
            "Discovery streams to NetBox Cloud Diode. New objects and deviations appear in "
            "NetBox Assurance once the ingestion finishes."
        ),
    }


def action_push_configs(payload: dict[str, Any]) -> dict[str, Any]:
    """Push rendered configs from NetBox intent to every targeted switch."""
    configs = payload.get("configs")
    if not isinstance(configs, dict) or not configs:
        raise ValueError("configs must be a non-empty {device: config_text} mapping")

    validated: dict[str, str] = {}
    for dev, text in configs.items():
        validated[validate_device(dev)] = str(text)

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, len(validated))) as pool:
        futures = {pool.submit(_push_one, d, t): d for d, t in validated.items()}
        for fut in as_completed(futures):
            results.append(fut.result())
    results.sort(key=lambda r: FABRIC_DEVICES.index(r["device"]))

    return {
        "ok": all(r["ok"] for r in results),
        "action": "push-configs",
        "results": results,
        "output": "\n".join(
            f"{r['device']}: {'applied ' + str(r['lines']) + ' lines' if r['ok'] else 'FAILED - ' + str(r['error'])}"
            for r in results
        ),
    }


def _validate_one(device: str) -> dict[str, Any]:
    """Read the health evidence that matters for this fabric from one switch.

    Each command is run separately and tolerated individually: a bare switch has no
    BGP or MLAG yet, and reporting "the switch is broken" because a feature it has
    not been configured with is absent would be a lie. `ok` means the switch
    answered, not that every command succeeded.
    """
    base = call_eapi(device, ["enable", "show hostname"], fmt="json", timeout=20)
    if not base["ok"]:
        return {
            "device": device,
            "ok": False,
            "state": base.get("state", "offline"),
            "error": base.get("error", ""),
        }

    hostname = ""
    try:
        hostname = base["result"][1].get("hostname", "")
    except (IndexError, AttributeError):
        pass

    info: dict[str, Any] = {
        "device": device,
        "ok": True,
        "state": "online",
        "hostname": hostname,
    }

    # Optional evidence, each fetched on its own so one failure cannot mask the rest.
    optional = {
        "evpn": "show bgp evpn summary",
        "vlans": "show vlan",
    }
    if device in LEAVES:
        optional["vxlan"] = "show vxlan vtep"
        optional["mlag"] = "show mlag"

    for key, command in optional.items():
        result = call_eapi(device, ["enable", command], fmt="json", timeout=15)
        if result["ok"]:
            try:
                info[f"has_{key}"] = True
                if key == "evpn":
                    peers = result["result"][1].get("vrfs", {}).get("default", {}).get("peers", {})
                    info["evpn_peers"] = len(peers)
                    info["evpn_established"] = sum(
                        1 for p in peers.values() if p.get("peerState") == "Established"
                    )
                elif key == "vlans":
                    info["vlan_count"] = len(result["result"][1].get("vlans", {}))
                elif key == "vxlan":
                    vteps = (
                        result["result"][1].get("interfaces", {}).get("Vxlan1", {}).get("vteps", {})
                    )
                    info["vxlan_vteps"] = len(vteps)
                elif key == "mlag":
                    info["mlag_state"] = result["result"][1].get("state", "unknown")
            except (IndexError, AttributeError, KeyError):
                info[f"has_{key}"] = False
        else:
            # Expected on a bare switch: the feature is simply not configured yet.
            info[f"has_{key}"] = False

    return info


def action_validate_fabric() -> dict[str, Any]:
    """Check all six switches in parallel."""
    devices: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(FABRIC_DEVICES)) as pool:
        futures = [pool.submit(_validate_one, d) for d in FABRIC_DEVICES]
        for fut in as_completed(futures):
            devices.append(fut.result())
    devices.sort(key=lambda d: FABRIC_DEVICES.index(d["device"]))

    lines: list[str] = []
    for d in devices:
        if not d["ok"]:
            lines.append(f"{d['device']}: {d.get('state', 'offline')} - {d.get('error', '')}")
            continue
        parts = [f"online as {d.get('hostname') or '?'}"]
        if d.get("has_evpn"):
            parts.append(f"EVPN {d.get('evpn_established', 0)}/{d.get('evpn_peers', 0)}")
        else:
            parts.append("no BGP yet")
        if d.get("has_vlans"):
            parts.append(f"VLANs {d.get('vlan_count', 0)}")
        if d.get("has_vxlan"):
            parts.append(f"VTEPs {d.get('vxlan_vteps', 0)}")
        if d.get("has_mlag"):
            parts.append(f"MLAG {d.get('mlag_state', '?')}")
        lines.append(f"{d['device']}: " + " | ".join(parts))

    return {
        "ok": all(d["ok"] for d in devices),
        "action": "validate-fabric",
        "devices": devices,
        "output": "\n".join(lines),
    }


def action_eapi(payload: dict[str, Any]) -> dict[str, Any]:
    """Allowlisted show/config proxy so NetBox Cloud can read and change a switch."""
    dev = validate_device(payload.get("device", ""))
    mode = str(payload.get("mode", "show")).strip().lower()
    cmds = validate_eapi_commands(payload.get("commands"), mode)

    if mode == "config":
        full = ["enable", "configure terminal", *cmds, "end", "write memory"]
        fmt = "text"
    else:
        full = ["enable", *cmds]
        fmt = "json" if payload.get("format") != "text" else "text"

    res = call_eapi(dev, full, fmt=fmt, timeout=60)
    res["action"] = "eapi"
    res["mode"] = mode
    res["commands"] = cmds
    return res


def action_rogue_drift(payload: dict[str, Any], *, remediate: bool) -> dict[str, Any]:
    """Inject or remove one reversible drift scenario on the designated leaf."""
    key = str(payload.get("scenario", "multi_drift")).strip()
    scenario = ROGUE_DRIFT_SCENARIOS.get(key)
    if scenario is None:
        raise ValueError(f"Unknown scenario {key!r}; valid: {', '.join(ROGUE_DRIFT_SCENARIOS)}")

    cmds = scenario["remediate"] if remediate else scenario["inject"]
    res = call_eapi(
        ROGUE_DRIFT_DEVICE,
        ["enable", "configure terminal", *cmds, "end", "write memory"],
        fmt="text",
        timeout=60,
    )

    verb = "remediate-drift" if remediate else "inject-rogue-drift"
    return {
        "ok": res["ok"],
        "action": verb,
        "scenario": key,
        "title": scenario["title"],
        "device": ROGUE_DRIFT_DEVICE,
        "commands": cmds,
        "error": res.get("error", ""),
        "output": (
            f"{'Removed' if remediate else 'Injected'} {scenario['title']} on {ROGUE_DRIFT_DEVICE}:\n"
            + "\n".join(f"  {c}" for c in cmds)
            + (
                "\n\nRun Orb Agent discovery, then review NetBox Assurance for the resulting deviations."
                if not remediate
                else "\n\nRun Orb Agent discovery, then expect the deviations to clear."
            )
        )
        if res["ok"]
        else f"Failed on {ROGUE_DRIFT_DEVICE}: {res.get('error', '')}",
    }


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #


class Handler(BaseHTTPRequestHandler):
    server_version = f"NetBoxDemoLabAgent/{AGENT_VERSION}"
    protocol_version = "HTTP/1.1"

    def _json(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self) -> bool:
        if verify_bearer_token(self.headers.get("Authorization"), AGENT_TOKEN):
            return True
        self._json(401, {"ok": False, "error": "Unauthorized: missing or invalid Bearer token"})
        return False

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 5 * 1024 * 1024:
            raise ValueError("Request body exceeds the 5 MB limit")
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object")
        return data

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/ping":
            self._json(200, {"ok": True, "agent_version": AGENT_VERSION, "topology": TOPOLOGY_NAME})
            return
        if not self._authorized():
            return
        if path == "/health":
            fabric, orb = inspect_lab()
            up = sum(1 for c in fabric if c["state"] == "running")
            self._json(
                200,
                {
                    "ok": True,
                    "agent_version": AGENT_VERSION,
                    "backend": LAB_BACKEND,
                    "hostname": socket.gethostname(),
                    "topology": TOPOLOGY_NAME,
                    "ceos_image": CEOS_IMAGE,
                    "mgmt_subnet": MGMT_SUBNET,
                    "expected_devices": len(FABRIC_DEVICES),
                    "running_devices": up,
                    "lab_running": up > 0,
                    "devices": fabric,
                    "orb": orb,
                },
            )
            return
        self._json(404, {"ok": False, "error": f"Unknown route GET {path}"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._authorized():
            return
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            payload = self._body()
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return

        handlers = {
            "/actions/destroy-lab": lambda: action_destroy_lab(),
            "/actions/deploy-bare-lab": lambda: action_deploy_bare_lab(),
            "/actions/run-discovery": lambda: action_run_discovery(),
            "/actions/push-configs": lambda: action_push_configs(payload),
            "/actions/validate-fabric": lambda: action_validate_fabric(),
            "/actions/eapi": lambda: action_eapi(payload),
            "/actions/inject-rogue-drift": lambda: action_rogue_drift(payload, remediate=False),
            "/actions/remediate-drift": lambda: action_rogue_drift(payload, remediate=True),
        }
        handler = handlers.get(path)
        if handler is None:
            self._json(404, {"ok": False, "error": f"Unknown route POST {path}"})
            return

        try:
            result = handler()
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 - operator sees the real cause
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return

        self._json(200 if result.get("ok") else 502, result)

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        print(f"[lab-agent] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    if not AGENT_TOKEN:
        print(
            "[lab-agent] FATAL: LAB_AGENT_TOKEN is empty, so every authenticated call would fail. "
            "Set it in .env before starting.",
            flush=True,
        )
    prefix = FABRIC_DEVICES[0].removesuffix("spine-01") if FABRIC_DEVICES[0].endswith("spine-01") else ""
    print(
        f"[lab-agent] listening on {BIND_HOST}:{BIND_PORT} | backend={LAB_BACKEND} "
        f"| topology={TOPOLOGY_NAME} | mgmt={MGMT_SUBNET} "
        f"| device prefix={prefix or '(none)'} | orb={ORB_MODE}",
        flush=True,
    )
    if BIND_HOST in ("127.0.0.1", "localhost"):
        # Binding to loopback *inside a container* makes the published port
        # unreachable from the host, which looks like a firewall problem.
        print(
            "[lab-agent] WARNING: bound to loopback. In a container this is only reachable "
            "from inside it. Leave LAB_AGENT_BIND_HOST at 0.0.0.0 and restrict access with "
            "LAB_AGENT_BIND_IP (the host-side address) instead.",
            flush=True,
        )
    ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
