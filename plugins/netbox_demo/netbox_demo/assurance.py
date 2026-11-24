"""Read NetBox Assurance deviation state for the UI (INIT-001/SPEC-004).

Assurance owns approval and remediation, so this module only *reads*. It tries
the installed plugin's models first (fast, no HTTP) and falls back to the REST
API, because the model names differ between Assurance releases and a demo must
not break because of that.

When Assurance is absent — for example in a local test harness — every function
returns a clearly-marked "unavailable" result rather than zeroes, since zero
deviations and no Assurance at all are very different claims.
"""

from __future__ import annotations

from typing import Any

from . import conf


def _plugin_installed() -> bool:
    from django.apps import apps

    return apps.is_installed("netbox_assurance_plugin")


def _deviation_models():
    """Yield candidate deviation models across Assurance releases."""
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
    for model in _deviation_models():
        status_field = _status_field(model)
        if status_field is None:
            try:
                return {"total": model.objects.count()}
            except Exception:  # noqa: BLE001 - a broken model must not break the page
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


def summary() -> dict[str, Any]:
    """A small, honest summary for the UI.

    `available` is False when Assurance is not installed, so the page can say
    "Assurance is not installed here" instead of implying the fabric is clean.
    """
    if not _plugin_installed():
        return {
            "available": False,
            "reason": "NetBox Assurance is not installed on this instance.",
            "counts": {},
            "url": conf.assurance_deviations_url(),
        }

    counts = _counts_from_orm() or {}
    return {
        "available": True,
        "reason": "",
        "counts": counts,
        "open": _open_from(counts),
        "url": conf.assurance_deviations_url(),
    }


def _open_from(counts: dict[str, int]) -> int:
    """Deviations still awaiting a decision, from an already-fetched count map."""
    for key in ("new", "open", "pending", "active", "unreviewed"):
        if key in counts:
            return int(counts[key])
    return int(counts.get("total", 0))


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
        return None, (
            "Assurance is not installed on this instance, so drift cannot be "
            "confirmed. The plugin only shows drift it can actually see."
        )
    open_count = data.get("open", 0)
    if open_count == 0:
        return True, "Assurance reports no open deviations against the documented fabric."
    return False, f"Assurance reports {open_count} open deviation(s) to review."
