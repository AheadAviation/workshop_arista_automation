"""Unit tests for the Kubernetes/c9s backend of lab-agent."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent  # noqa: E402
from contract import DEVICE_MGMT_IPS, FABRIC_DEVICES  # noqa: E402


def test_device_eapi_host_docker_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "LAB_BACKEND", "docker")
    monkeypatch.setattr(agent, "DEVICE_EAPI_HOSTS_RAW", "")
    assert agent.device_eapi_host("demo-spine-01") == DEVICE_MGMT_IPS["demo-spine-01"]


def test_device_eapi_host_k8s_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "LAB_BACKEND", "k8s")
    monkeypatch.setattr(agent, "K8S_NAMESPACE", "netbox-demo-fabric")
    monkeypatch.setattr(
        agent, "K8S_EAPI_HOST_TEMPLATE", "{device}-eapi.{namespace}.svc.cluster.local"
    )
    monkeypatch.setattr(agent, "DEVICE_EAPI_HOSTS_RAW", "")
    assert (
        agent.device_eapi_host("demo-leaf-01")
        == "demo-leaf-01-eapi.netbox-demo-fabric.svc.cluster.local"
    )


def test_device_eapi_host_json_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "LAB_BACKEND", "k8s")
    monkeypatch.setattr(
        agent,
        "DEVICE_EAPI_HOSTS_RAW",
        json.dumps({"demo-spine-01": "10.43.0.11"}),
    )
    assert agent.device_eapi_host("demo-spine-01") == "10.43.0.11"


def test_inspect_lab_k8s_parses_fabric_and_orb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "LAB_BACKEND", "k8s")
    monkeypatch.setattr(agent, "K8S_NAMESPACE", "netbox-demo-fabric")
    monkeypatch.setattr(agent, "K8S_ORB_NAMESPACE", "netbox-demo-fabric")
    monkeypatch.setattr(agent, "K8S_ORB_DEPLOYMENT", "orb-agent")
    monkeypatch.setattr(agent, "DEVICE_EAPI_HOSTS_RAW", "")

    fake_pods = [
        {
            "metadata": {
                "name": f"netbox-demo-fabric-{dev}-abc12",
                "labels": {"clabernetes/topologyNode": dev},
            },
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        }
        for dev in FABRIC_DEVICES
    ]
    fake_pods.append(
        {
            "metadata": {"name": "orb-agent-7d9f8c-xyz", "labels": {"app": "orb-agent"}},
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        }
    )

    monkeypatch.setattr(agent, "_kubectl_pods", lambda ns: fake_pods)

    fabric, orb = agent.inspect_lab()
    assert len(fabric) == 6
    assert [row["device"] for row in fabric] == list(FABRIC_DEVICES)
    assert all(row["state"] == "running" for row in fabric)
    assert fabric[0]["mgmt_ip"].startswith("demo-spine-01-eapi.")
    assert orb["running"] is True
    assert orb["container"] == "orb-agent-7d9f8c-xyz"


def test_k8s_destroy_and_deploy_custom_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "LAB_BACKEND", "k8s")
    monkeypatch.setattr(agent, "K8S_DESTROY_CMD", "/scripts/destroy.sh")
    monkeypatch.setattr(agent, "K8S_DEPLOY_CMD", "/scripts/deploy.sh")

    seen: list[list[str]] = []

    def fake_run(args: list[str], **kwargs):
        seen.append(args)
        return {"ok": True, "action": kwargs.get("action", ""), "returncode": 0, "output": "done"}

    monkeypatch.setattr(agent, "_run", fake_run)

    destroy_res = agent.action_destroy_lab()
    deploy_res = agent.action_deploy_bare_lab()

    assert destroy_res["ok"] is True
    assert destroy_res["backend"] == "k8s"
    assert deploy_res["ok"] is True
    assert deploy_res["backend"] == "k8s"
    assert seen == [
        ["sh", "-lc", "/scripts/destroy.sh"],
        ["sh", "-lc", "/scripts/deploy.sh"],
    ]


def test_k8s_run_discovery_restarts_persistent_orb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "LAB_BACKEND", "k8s")
    monkeypatch.setattr(agent, "ORB_MODE", "diode")
    monkeypatch.setattr(agent, "K8S_DISCOVERY_CMD", "")
    monkeypatch.setattr(agent, "K8S_ORB_DEPLOYMENT", "orb-agent")
    monkeypatch.setattr(agent, "K8S_ORB_NAMESPACE", "netbox-demo-fabric")

    seen: list[list[str]] = []

    def fake_run(args: list[str], **kwargs):
        seen.append(args)
        return {"ok": True, "action": "run-discovery", "returncode": 0, "output": "ok"}

    monkeypatch.setattr(agent, "_run", fake_run)

    res = agent.action_run_discovery()
    assert res["ok"] is True
    assert res["mode"] == "k8s-rollout-restart"
    assert seen[0] == ["kubectl", "rollout", "restart", "deployment/orb-agent", "-n", "netbox-demo-fabric"]
    assert seen[1][:5] == ["kubectl", "rollout", "status", "deployment/orb-agent", "-n"]
