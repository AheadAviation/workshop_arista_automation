# SPEC-003: Guided Workshop Tab & Day-2 Operations UI

**Initiative:** `INIT-004`   **Domain:** `frontend`
**Complexity:** `medium` — new tab route, views with permission/CSRF enforcement, and Bootstrap/NetBox templates.
**Autonomy:** `auto_tests`   **Reversibility:** `branch`   **Security Review:** `required` (new POST views mutating lab/devices)

## Goal

Add the **Workshop** tab (`plugins:netbox_ops:workshop`) to `navigation.py`, `urls.py`, `views.py`, and templates (`home.html`, `_workshop.html`, `_workshop_result.html`) so a user can execute Quick Start Steps 1–9 and Day 2 Operations end-to-end from NetBox with zero CLI commands.

## Acceptance Criteria

- **AC1 (`REQ-003`):** `Network Ops` left nav and tab strip include **Workshop** (`plugins:netbox_ops:workshop`) while keeping all 9 existing tabs intact.
- **AC2 (`REQ-001`, `REQ-003`):** The Workshop tab renders Quick Start Steps 1–9 in order with live status badges, one-click POST action buttons (Clean Slate, Import into NetBox, Generate Topology, Generate Configs, Deploy Lab, Push Full Configs, Verify Fabric, Run Validation Suite), and collapsible inline viewers for `topology-docker.clab.yml` and rendered `.cfg` files.
- **AC3 (`REQ-002`, `REQ-003`):** The Workshop tab includes a **Day 2 Operations** section with direct links to NetBox Devices & Config Contexts, plus one-click Regenerate Configs, Diff vs. Running Config, Backup Switches, Push Full Configs, Quick/Full Validation, and Rollback to Backup.
- **AC4 (`REQ-004`):** Every mutating POST view inherits `ChangeOperatorMixin` (`dcim.change_device`) and `@method_decorator(csrf_protect, name="post")`; read-only GET/verify/diff views inherit `ReadOnlyOperatorMixin` (`dcim.view_device`).
- **AC5 (`REQ-003`):** Action responses render cleanly inside NetBox's layout (with a clear Back to Workshop link and structured pass/fail output) and never leak tracebacks.

## Security

- All POST endpoints require `dcim.change_device` and CSRF protection.
- Device parameter on config viewer is allowlisted against the 6 fabric device names.

## Verification Strategy

Django test-client / view unit tests assert permissions (anonymous rejected, view-only blocked from POST, change user allowed), CSRF enforcement, template rendering, and all URL reversals.
