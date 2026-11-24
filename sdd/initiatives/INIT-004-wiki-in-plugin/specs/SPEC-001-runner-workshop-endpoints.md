# SPEC-001: Scoped Workshop Runner Endpoints

**Initiative:** `INIT-004`   **Domain:** `infra`
**Complexity:** `medium` — extends existing `lab-runner` HTTP service and compose override with fixed allowlisted actions.
**Autonomy:** `auto_tests`   **Reversibility:** `branch`   **Security Review:** `required` (touches Docker-privileged sidecar boundary)

## Goal

Extend `docker/lab-runner/runner.py`, `docker-compose.yml`, and `docker-compose.lab-from-container.yml` with fixed, parameter-free HTTP endpoints so `netbox_ops` can drive every host-side step of `wiki/Quick-Start.md` and `wiki/Day-2-Operations.md` without NetBox ever holding Docker access.

## Acceptance Criteria

- **AC1 (`REQ-001`, `REQ-004`):** `lab-runner` exposes fixed `POST` endpoints: `/workshop/clean`, `/workshop/topology`, `/workshop/generate-configs`, `/workshop/deploy`, `/workshop/destroy`, `/workshop/push-configs`, `/workshop/backup`, `/workshop/rollback`, `/workshop/validate`, and `/workshop/validate-quick` — with zero user-supplied shell or path arguments.
- **AC2 (`REQ-001`):** `docker-compose.lab-from-container.yml` rewrites `ansible` service bind mounts to `${HOST_REPO}/...` (matching `clab`), so nested `docker compose run --rm ansible ...` from `lab-runner` resolves host paths cleanly.
- **AC3 (`REQ-001`, `REQ-002`):** `/workshop/push-configs` applies each generated `output/configs/<dev>.cfg` to `clab-netbox-arista-lab-<dev>` via `docker exec` (with `write memory`), reporting per-device `ok`/`error`. `/workshop/backup` saves `flash:backup.cfg` and `/workshop/rollback` runs `configure replace flash:backup.cfg` per device.
- **AC4 (`REQ-004`):** Existing `/health`, `/deploy`, and `/destroy` endpoints remain untouched and working; unknown routes return HTTP 404 JSON.
- **AC5 (`REQ-001`):** `netbox` service mounts `./clab:/opt/netbox/clab:ro` (alongside existing `./output/configs:/opt/netbox/configs:ro`) so the plugin can inspect `topology-docker.clab.yml` read-only.

## Security

- No shell interpolation (`subprocess.run` with fixed argument lists only).
- Fixed allowlist of the 6 fabric device names (`spine-01`, `spine-02`, `leaf-01`..`leaf-04`).
- `lab-runner` remains internal to the compose network (no host port published).

## Verification Strategy

Unit tests mocking `subprocess.run` assert exact argument lists, per-device results, refusal of unknown verbs/routes (404), and clean failure JSON on non-zero return codes.
