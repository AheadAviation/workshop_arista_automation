"""Read and act on NetBox Assurance deviation state via Diode gRPC (INIT-035/SPEC-002).

On NetBox Enterprise 2.5.x (NetBox 4.7.x), `netbox_assurance_plugin.models.Deviation`
is a minimal stub model (`fields=['id']`) whose ORM table is always empty; real
deviations live in the Diode Reconciler and are accessed through
`netbox_assurance_plugin.diode_api`.

This module:
1. Queries `netbox_assurance_plugin.diode_api` first (gRPC to Diode Reconciler).
2. Never reports fake zeroes when Diode gRPC is unreachable or when an ORM model
   is a stub without a state/status field — it marks `available=False` with an
   explicit reason instead.
3. Exposes demo-scoped list / apply / rediff / ignore helpers so operators can
   close the discovery -> assurance -> reconciliation loop without leaving the
   demo workflow, while never touching deviations outside the demo fabric.
"""

from __future__ import annotations

import importlib
from typing import Any

from . import conf, design

_MAX_PAGES = 10
_DEFAULT_PAGE_SIZE = 200
_OPEN_KEYS = ("open", "new", "pending", "active", "unreviewed")


def _plugin_installed() -> bool:
    from django.apps import apps

    return apps.is_installed("netbox_assurance_plugin")


def _load_diode_api():
    """Import `netbox_assurance_plugin.diode_api` when available."""
    try:
        return importlib.import_module("netbox_assurance_plugin.diode_api")
    except ImportError:
        return None


def _state_label(diode_api, raw_state: Any) -> str:
    if raw_state is None:
        return "unknown"
    if isinstance(raw_state, str):
        return raw_state.strip().lower() or "unknown"
    converter = getattr(diode_api, "get_state_string", None)
    if callable(converter):
        try:
            return str(converter(raw_state)).strip().lower() or "unknown"
        except Exception:  # noqa: BLE001
            pass
    return str(raw_state).strip().lower() or "unknown"


def _is_demo_deviation(item: dict[str, Any]) -> bool:
    """True when a deviation belongs to the demo fabric or demo Orb Agent."""
    source = str(item.get("source") or "").lower()
    name = str(item.get("name") or "").lower()
    if "netbox-demo" in source or "demo_fabric" in source:
        return True
    if design.DEVICE_PREFIX.lower() in name:
        return True
    if design.SITE_SLUG.lower() in name or design.SITE_NAME.lower() in name:
        return True
    return False


def _serialize_deviation(diode_api, dev: Any) -> dict[str, Any]:
    state = _state_label(diode_api, getattr(dev, "state", None))
    raw_type = getattr(dev, "deviation_type", "")
    type_name = str(raw_type or "")
    resolver = getattr(diode_api, "get_deviation_type_name_from_id", None)
    if callable(resolver) and raw_type:
        try:
            type_name = str(resolver(raw_type) or type_name)
        except Exception:  # noqa: BLE001
            pass
    return {
        "id": str(getattr(dev, "id", "")),
        "name": str(getattr(dev, "name", "")),
        "object_type": str(getattr(dev, "object_type", "")),
        "deviation_type": type_name,
        "source": str(getattr(dev, "source", "") or getattr(dev, "source_name", "")),
        "state": state,
    }


def _fetch_all_diode_deviations(
    diode_api,
    *,
    filters: dict[str, Any] | None = None,
    max_items: int = 1000,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page_token: str | None = None
    page_size = min(_DEFAULT_PAGE_SIZE, max(1, max_items))
    for _ in range(_MAX_PAGES):
        resp = diode_api.get_deviation_list(
            page_size=page_size,
            page_token=page_token,
            filters=dict(filters or {}),
        )
        batch = list(getattr(resp, "deviations", None) or [])
        for dev in batch:
            rows.append(_serialize_deviation(diode_api, dev))
            if len(rows) >= max_items:
                return rows
        next_token = getattr(resp, "next_page_token", None) or ""
        if not next_token or not batch:
            break
        page_token = next_token
    return rows


def _counts_from_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("state") or "unknown").lower()
        counts[key] = counts.get(key, 0) + 1
    counts["total"] = len(rows)
    return counts


def _deviation_models():
    """Yield candidate deviation models for legacy ORM-backed Assurance releases."""
    from django.apps import apps

    for model_name in ("Deviation", "DeviationResult", "AssuranceDeviation"):
        try:
            yield apps.get_model("netbox_assurance_plugin", model_name)
        except LookupError:
            continue


def _status_field(model) -> str | None:
    field_names = {f.name for f in model._meta.get_fields()}
    for candidate in ("status", "state", "review_status"):
        if candidate in field_names:
            return candidate
    return None


def _counts_from_orm() -> dict[str, int] | None:
    """Read counts from legacy ORM-backed Assurance models.

    Deliberately skips stub models that expose no state/status field (such as
    NBE 2.5.x's empty `Deviation(id)` placeholder), so a stub table can never
    masquerade as zero drift.
    """
    for model in _deviation_models():
        status_field = _status_field(model)
        if status_field is None:
            continue
        try:
            counts: dict[str, int] = {}
            for row in model.objects.values(status_field):
                key = str(row[status_field] or "unknown").lower()
                counts[key] = counts.get(key, 0) + 1
            counts["total"] = sum(counts.values())
            return counts
        except Exception:  # noqa: BLE001
            continue
    return None


def _open_from(counts: dict[str, int]) -> int:
    """Deviations still awaiting a decision, from an already-fetched count map."""
    for key in _OPEN_KEYS:
        if key in counts:
            return int(counts[key])
    # If states were returned (e.g. only `applied` or `ignored`) with no open key,
    # open count is genuinely 0 rather than total.
    non_total_keys = [k for k in counts if k != "total"]
    if non_total_keys:
        return 0
    return int(counts.get("total", 0))


def summary(*, demo_only: bool = False) -> dict[str, Any]:
    """Honest Assurance deviation summary for the UI and REST API.

    `available` is False whenever Assurance is not installed or Diode gRPC
    cannot be reached — never misreporting an outage or stub table as zero drift.
    """
    url = conf.assurance_deviations_url()
    if not _plugin_installed():
        return {
            "available": False,
            "source": "none",
            "reason": "NetBox Assurance is not installed on this instance.",
            "counts": {},
            "open": 0,
            "url": url,
        }

    diode_api = _load_diode_api()
    if diode_api is not None:
        try:
            rows = _fetch_all_diode_deviations(diode_api)
            if demo_only:
                rows = [r for r in rows if _is_demo_deviation(r)]
            counts = _counts_from_rows(rows)
            return {
                "available": True,
                "source": "diode_grpc",
                "reason": "",
                "counts": counts,
                "open": _open_from(counts),
                "url": url,
            }
        except Exception as exc:  # noqa: BLE001 - ReconcilerClientError or gRPC fault
            return {
                "available": False,
                "source": "diode_grpc",
                "reason": f"Assurance Diode gRPC unavailable: {exc}",
                "counts": {},
                "open": 0,
                "url": url,
            }

    counts = _counts_from_orm()
    if counts is None:
        return {
            "available": False,
            "source": "orm",
            "reason": "NetBox Assurance models do not expose deviation states on this release.",
            "counts": {},
            "open": 0,
            "url": url,
        }

    return {
        "available": True,
        "source": "orm",
        "reason": "",
        "counts": counts,
        "open": _open_from(counts),
        "url": url,
    }


def list_deviations(
    *,
    states: list[str] | None = None,
    limit: int = 100,
    demo_only: bool = True,
) -> dict[str, Any]:
    """Return structured deviations from Diode gRPC, optionally demo-scoped."""
    if not _plugin_installed():
        return {"available": False, "reason": "NetBox Assurance is not installed.", "deviations": []}
    diode_api = _load_diode_api()
    if diode_api is None:
        return {"available": False, "reason": "Assurance Diode API is unavailable.", "deviations": []}

    wanted_states = {s.strip().lower() for s in (states or []) if s and s.strip()}
    try:
        rows = _fetch_all_diode_deviations(diode_api, max_items=max(limit * 2, 400))
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "reason": f"Assurance Diode gRPC unavailable: {exc}",
            "deviations": [],
        }

    if demo_only:
        rows = [r for r in rows if _is_demo_deviation(r)]
    if wanted_states:
        rows = [r for r in rows if r["state"] in wanted_states]

    return {
        "available": True,
        "reason": "",
        "count": len(rows[:limit]),
        "total_matching": len(rows),
        "deviations": rows[:limit],
    }


def demo_open_deviation_ids(*, limit: int = 200) -> list[str]:
    """Return IDs of open demo-scoped deviations awaiting approval."""
    listed = list_deviations(states=["open"], limit=limit, demo_only=True)
    if not listed.get("available"):
        return []
    return [row["id"] for row in listed["deviations"] if row.get("id")]


def _validate_demo_ids(diode_api, ids: list[str]) -> tuple[list[str], list[str]]:
    """Verify every requested deviation ID belongs to the demo scope.

    Returns (allowed_ids, rejected_ids).
    """
    clean = [str(i).strip() for i in ids if str(i).strip()]
    if not clean:
        return [], []
    rows = _fetch_all_diode_deviations(diode_api, filters={"id": clean}, max_items=len(clean))
    by_id = {row["id"]: row for row in rows}
    allowed: list[str] = []
    rejected: list[str] = []
    for dev_id in clean:
        row = by_id.get(dev_id)
        if row is not None and _is_demo_deviation(row):
            allowed.append(dev_id)
        else:
            rejected.append(dev_id)
    return allowed, rejected


def _run_diode_action(
    action_name: str,
    ids: list[str] | None,
    *,
    branch_id: str | None = None,
    demo_only: bool = True,
) -> dict[str, Any]:
    if not _plugin_installed():
        return {"ok": False, "error": "NetBox Assurance is not installed."}
    diode_api = _load_diode_api()
    if diode_api is None:
        return {"ok": False, "error": "Assurance Diode API is unavailable."}

    try:
        if ids is None:
            target_ids = demo_open_deviation_ids(limit=200)
            rejected: list[str] = []
        elif demo_only:
            target_ids, rejected = _validate_demo_ids(diode_api, ids)
        else:
            target_ids = [str(i).strip() for i in ids if str(i).strip()]
            rejected = []

        if rejected:
            return {
                "ok": False,
                "error": (
                    f"Refused to {action_name} {len(rejected)} deviation(s) outside the "
                    f"demo fabric scope: {', '.join(rejected[:5])}"
                ),
                "rejected": rejected,
            }

        if not target_ids:
            return {"ok": True, "action": action_name, "count": 0, "ids": []}

        if action_name == "apply":
            if len(target_ids) == 1:
                diode_api.deviation_apply(target_ids[0], branch_id)
            else:
                diode_api.bulk_deviation_apply(target_ids, branch_id)
        elif action_name == "rediff":
            if len(target_ids) == 1:
                diode_api.deviation_rediff(target_ids[0], branch_id)
            else:
                diode_api.bulk_deviation_rediff(target_ids, branch_id)
        elif action_name == "ignore":
            if len(target_ids) == 1:
                diode_api.deviation_ignore(target_ids[0])
            else:
                diode_api.bulk_deviation_ignore(target_ids, branch_id)
        else:
            return {"ok": False, "error": f"Unsupported Assurance action {action_name!r}"}

        return {
            "ok": True,
            "action": action_name,
            "count": len(target_ids),
            "ids": target_ids,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": action_name, "error": f"Assurance {action_name} failed: {exc}"}


def apply_deviations(
    ids: list[str] | None = None, *, branch_id: str | None = None, demo_only: bool = True
) -> dict[str, Any]:
    """Apply demo-scoped open deviations in NetBox Assurance (via Diode gRPC)."""
    return _run_diode_action("apply", ids, branch_id=branch_id, demo_only=demo_only)


def rediff_deviations(
    ids: list[str] | None = None, *, branch_id: str | None = None, demo_only: bool = True
) -> dict[str, Any]:
    """Re-diff demo-scoped open deviations against current NetBox state."""
    return _run_diode_action("rediff", ids, branch_id=branch_id, demo_only=demo_only)


def ignore_deviations(
    ids: list[str] | None = None, *, branch_id: str | None = None, demo_only: bool = True
) -> dict[str, Any]:
    """Ignore demo-scoped deviations in NetBox Assurance."""
    return _run_diode_action("ignore", ids, branch_id=branch_id, demo_only=demo_only)


def open_deviation_count() -> int | None:
    """Deviations still awaiting a decision, or None when Assurance is unavailable."""
    data = summary()
    if not data.get("available"):
        return None
    return int(data.get("open", 0))


def zero_drift_claim() -> tuple[bool | None, str]:
    """Whether the fabric can be called drift-free, and the sentence to show.

    Returns None when that cannot be known, which is not the same as "no drift".
    """
    data = summary()
    if not data["available"]:
        reason = data.get("reason") or "Assurance is unavailable on this instance."
        return None, (
            f"{reason} Drift cannot be confirmed, and the plugin only shows drift it can actually see."
        )
    open_count = data.get("open", 0)
    if open_count == 0:
        return True, "Assurance reports no open deviations against the documented fabric."
    return False, f"Assurance reports {open_count} open deviation(s) to review."
