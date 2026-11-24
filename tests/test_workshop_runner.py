"""Unit tests for the scoped workshop endpoints in `docker/lab-runner/runner.py` (INIT-004/SPEC-001, SPEC-004)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER_PATH = REPO_ROOT / "docker" / "lab-runner" / "runner.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("lab_runner_under_test", RUNNER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_post_routes_cover_legacy_and_workshop_verbs():
    runner = _load_runner()
    expected = {
        "/deploy",
        "/destroy",
        "/workshop/clean",
        "/workshop/import",
        "/workshop/topology",
        "/workshop/generate-configs",
        "/workshop/deploy",
        "/workshop/destroy",
        "/workshop/push-configs",
        "/workshop/backup",
        "/workshop/rollback",
        "/workshop/validate",
        "/workshop/validate-quick",
    }
    assert expected.issubset(set(runner.POST_ROUTES.keys()))


def test_workshop_ansible_actions_use_fixed_commands(monkeypatch):
    runner = _load_runner()
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(runner, "restore_host_project_dir", lambda: "/host/repo")
    monkeypatch.setattr(runner, "restore_compose_project_name", lambda: "workshop_arista_automation")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    res_imp = runner.workshop_import_netbox()
    res_topo = runner.workshop_generate_topology()
    res_cfg = runner.workshop_generate_configs()
    res_val = runner.workshop_validate(quick=False)
    res_quick = runner.workshop_validate(quick=True)

    assert all(r["ok"] for r in (res_imp, res_topo, res_cfg, res_val, res_quick))
    assert calls[0][-2:] == ["python", "scripts/full_import.py"]
    assert calls[1][-2:] == ["python", "scripts/generate_clab_topology_docker.py"]
    assert calls[2][-2:] == ["ansible-playbook", "playbooks/generate_configs.yml"]
    assert calls[3][-4:] == ["ansible-playbook", "-i", "inventory/hosts.yml", "playbooks/test_all.yml"]
    assert calls[4][-6:] == [
        "ansible-playbook",
        "-i",
        "inventory/hosts.yml",
        "playbooks/test_all.yml",
        "--tags",
        "quick",
    ]
    for cmd in calls:
        assert "-f" in cmd
        assert "/compose/docker-compose.lab-from-container.yml" in cmd


def test_workshop_push_configs_reports_per_device_and_catches_cli_errors(monkeypatch, tmp_path):
    runner = _load_runner()
    cfg_dir = tmp_path / "output" / "configs"
    cfg_dir.mkdir(parents=True)
    for dev in runner.FABRIC_DEVICES:
        (cfg_dir / f"{dev}.cfg").write_text(f"hostname {dev}\n", encoding="utf-8")

    monkeypatch.setattr(runner, "COMPOSE_DIR", str(tmp_path))

    def fake_run(args, **kwargs):
        # Simulate a CLI error on leaf-02 only
        container = args[3]
        if container.endswith("leaf-02"):
            return SimpleNamespace(returncode=0, stdout="% Invalid input at line 3", stderr="")
        return SimpleNamespace(returncode=0, stdout="Copy completed successfully.", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    res = runner.workshop_push_configs()
    assert res["ok"] is False
    assert res["succeeded"] == 5
    assert res["failed"] == 1
    by_dev = {d["device"]: d for d in res["devices"]}
    assert by_dev["leaf-02"]["ok"] is False
    assert by_dev["spine-01"]["ok"] is True


def test_workshop_backup_and_rollback_run_on_all_six_devices(monkeypatch):
    runner = _load_runner()
    seen: list[list[str]] = []

    def fake_run(args, **kwargs):
        seen.append(args)
        return SimpleNamespace(returncode=0, stdout="Copy completed successfully.", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    b_res = runner.workshop_backup()
    r_res = runner.workshop_rollback()

    assert b_res["ok"] is True and b_res["succeeded"] == 6
    assert r_res["ok"] is True and r_res["succeeded"] == 6
    assert len(seen) == 12
    assert seen[0][-1] == "copy running-config flash:backup.cfg"
    assert seen[6][-1] == "configure replace flash:backup.cfg"


def test_run_clab_refuses_unknown_verb():
    runner = _load_runner()
    res = runner.run_clab("exec")
    assert res["ok"] is False
    assert "unknown verb" in res["error"]
