"""The demo's stories, end to end (INIT-001/SPEC-004 and SPEC-007).

This is the module the page's buttons call. Each function is one thing a presenter shows,
and each returns plain dicts the page can render without knowing anything about devices,
NetBox or executors.

The five stories, in the order the briefing tells them:

1. `fabric_overview`  — what the network should be, what it is, where they differ.
2. `propose_vlan`     — the Philips change: stage it, show the diff, change nothing.
3. `apply_change`     — a human approves; it lands; the audit records it.
4. `inject_drift`     — break a switch on purpose, so the page can catch it.
5. `lifecycle`        — drift and OS version per device, for the CVE conversation.

Every story is written so that a refusal is a first-class result rather than an
exception the page has to catch: a demo that shows a stack trace has already failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from workbench.contracts import (
    Actual,
    AuditEntry,
    Intent,
    Plan,
    Preview,
    new_id,
)
from workbench.drift import compute_drift, summarise
from workbench.eos_executor import (
    EosApplyError,
    apply_change as eos_apply,
    preview as eos_preview,
    vlan_across_trunks_plan,
)
from workbench.gate import (
    AuditLog,
    GateRefused,
    append_entry,
    approve_and_apply,
    request_change,
)
from workbench.intent_source import (
    NEW_VLAN,
    NEW_VLAN_NAME,
    read_fabric_intent,
)

#: Devices the demo drives. The shipped fabric's six switches.
DEMO_DEVICES: tuple[str, ...] = (
    "spine-01",
    "spine-02",
    "leaf-01",
    "leaf-02",
    "leaf-03",
    "leaf-04",
)


# --------------------------------------------------------------------------- #
# Injectable seams, so every story is testable without a lab
# --------------------------------------------------------------------------- #


@dataclass
class Lab:
    """The four things the stories need from the outside world.

    Injected rather than imported so the tests replace all four with fakes and the whole
    demo can be exercised in under a second.
    """

    read_intent: Callable[..., dict[str, Intent]]
    read_actual: Callable[[str], Actual]
    preview: Callable[[Plan], Preview]
    apply: Callable[[Plan], Preview]
    audit: AuditLog

    @classmethod
    def real(cls, audit: AuditLog | None = None) -> "Lab":
        """The real lab: NetBox, the running containers, and the EOS executor."""
        from workbench.device_reader import read_device

        return cls(
            read_intent=read_fabric_intent,
            read_actual=lambda name: read_device(name, platform="eos"),
            preview=eos_preview,
            apply=eos_apply,
            audit=audit or AuditLog(),
        )


# --------------------------------------------------------------------------- #
# 1. The overview
# --------------------------------------------------------------------------- #


def fabric_overview(lab: Lab, *, new_vlan: int | None = None) -> dict[str, Any]:
    """Every device, its intended state, its actual state, and the difference.

    `new_vlan` proposes an intent that is not yet real, which is how the page can show
    "here is what adding the VLAN *would* mean" without editing the source of truth.
    """
    try:
        intended = lab.read_intent(new_vlan=new_vlan)
    except Exception as exc:  # NetBox unreachable is a fact to show, not a crash
        return {
            "ok": False,
            "headline": "Could not read intended state",
            "detail": str(exc),
            "devices": [],
        }

    cards: list[dict[str, Any]] = []
    for name, intent in sorted(intended.items()):
        actual = lab.read_actual(name)
        findings = compute_drift(intent, actual)
        cards.append(
            {
                "device": name,
                "role": intent.role,
                "intended": _intent_view(intent),
                "actual": _actual_view(actual),
                "verdict": summarise(findings),
                "findings": [f.to_dict() for f in findings],
            }
        )

    drifted = [c for c in cards if c["verdict"]["state"] == "drifted"]
    return {
        "ok": True,
        "headline": _overview_headline(len(cards), len(drifted)),
        "device_count": len(cards),
        "drifted_count": len(drifted),
        "proposed_vlan": new_vlan,
        "devices": cards,
    }


def _overview_headline(total: int, drifted: int) -> str:
    if total == 0:
        return "No devices found"
    if drifted == 0:
        return f"All {total} devices match intended state"
    return f"{drifted} of {total} devices differ from intended state"


def _intent_view(intent: Intent) -> dict[str, Any]:
    return {
        "hostname": intent.hostname,
        "asn": intent.asn,
        "vlans": list(intent.vlans),
        "trunks": list(intent.trunks),
        "source": intent.source,
    }


def _actual_view(actual: Actual) -> dict[str, Any]:
    return {
        "reachable": actual.reachable,
        "hostname": actual.hostname,
        "asn": actual.asn,
        "vlans": list(actual.vlans),
        "trunks": {k: list(v) for k, v in actual.trunk_allowed.items()},
        "os_version": actual.os_version,
        "error": actual.error,
    }


# --------------------------------------------------------------------------- #
# 2. Propose the VLAN change
# --------------------------------------------------------------------------- #


def propose_vlan(
    lab: Lab,
    device: str = "leaf-01",
    *,
    vlan: int = NEW_VLAN,
    name: str = NEW_VLAN_NAME,
) -> dict[str, Any]:
    """Stage the Philips change on one switch and show its diff. Changes nothing.

    Returns the change id the page uses to approve or reject it.
    """
    try:
        intended = lab.read_intent()
    except Exception as exc:
        return {"ok": False, "headline": "Could not read intended state", "detail": str(exc)}

    intent = intended.get(device)
    if intent is None:
        return {
            "ok": False,
            "headline": f"{device} is not in the fabric",
            "detail": f"known devices: {', '.join(sorted(intended))}",
        }

    if not intent.trunks:
        return {
            "ok": False,
            "headline": f"{device} has no server trunks",
            "detail": "the demo's trunk overlay covers the leaves; pick one of those",
        }

    plan = vlan_across_trunks_plan(device, vlan, name, intent.trunks)

    try:
        staged_preview = lab.preview(plan)
    except EosApplyError as exc:
        return {"ok": False, "headline": f"Could not preview on {device}", "detail": str(exc)}

    if staged_preview.is_empty:
        return {
            "ok": True,
            "no_drift": True,
            "headline": f"{device} already carries VLAN {vlan}",
            "detail": "nothing to do — this is what 'already compliant' looks like",
            "device": device,
        }

    change_id = new_id("vlan-across-trunks", device, str(vlan))
    try:
        pending = request_change(change_id, plan, staged_preview)
    except GateRefused as exc:
        return {"ok": False, "headline": "Change refused", "detail": str(exc)}

    # Record that a change was proposed, so the audit reads as a story rather than only
    # as a list of approvals.
    append_entry(
        lab.audit,
        AuditEntry(
            kind="pending",
            device=device,
            summary=(
                f"vlan-across-trunks: staged adding VLAN {vlan} ({name}) to "
                f"{len(intent.trunks)} trunk(s), awaiting approval"
            ),
            autonomy="assist",
            event_id=change_id,
            diff_before=staged_preview.diff,
        ),
    )

    return {
        "ok": True,
        "change_id": change_id,
        "device": device,
        "vlan": vlan,
        "trunks": list(intent.trunks),
        "plan_summary": plan.summary,
        "diff": staged_preview.diff,
        "changed_lines": staged_preview.changed_lines,
        "headline": f"VLAN {vlan} ({name}) would be added to {len(intent.trunks)} trunk(s) on {device}",
        "pending": pending.to_dict(),
    }


# --------------------------------------------------------------------------- #
# 3. Approve and apply
# --------------------------------------------------------------------------- #


def approve(
    lab: Lab,
    device: str,
    *,
    approver: str,
    vlan: int = NEW_VLAN,
    name: str = NEW_VLAN_NAME,
) -> dict[str, Any]:
    """A named human approves. The change lands, and the audit records it.

    Re-derives and re-previews rather than trusting a cached diff, so what is applied is
    what the device needs *now* — and an approval for a stale preview cannot slip a
    different change through.
    """
    if not approver or not approver.strip():
        return {
            "ok": False,
            "headline": "An approver is required",
            "detail": "no change is applied without a named person on the record",
        }

    try:
        intended = lab.read_intent()
    except Exception as exc:
        return {"ok": False, "headline": "Could not read intended state", "detail": str(exc)}

    intent = intended.get(device)
    if intent is None or not intent.trunks:
        return {"ok": False, "headline": f"{device} is not a demo trunk device", "detail": ""}

    plan = vlan_across_trunks_plan(device, vlan, name, intent.trunks)

    try:
        current = lab.preview(plan)
    except EosApplyError as exc:
        return {"ok": False, "headline": f"Could not preview on {device}", "detail": str(exc)}

    if current.is_empty:
        return {
            "ok": True,
            "no_drift": True,
            "headline": f"{device} already carries VLAN {vlan}",
            "detail": "the device matched intent, so nothing was sent",
        }

    try:
        pending = request_change(new_id("vlan-across-trunks", device, str(vlan)), plan, current)
        entry = approve_and_apply(pending, approver, audit=lab.audit, apply=lab.apply)
    except GateRefused as exc:
        return {"ok": False, "headline": "Refused", "detail": str(exc)}
    except EosApplyError as exc:
        return {"ok": False, "headline": f"Apply failed on {device}", "detail": str(exc)}

    return {
        "ok": True,
        "applied": entry.kind == "applied",
        "change_id": entry.event_id,
        "approver": entry.approver,
        "headline": (
            f"VLAN {vlan} applied to {device} and recorded"
            if entry.kind == "applied"
            else "Nothing was sent; the device already matched intent"
        ),
        "diff": entry.diff_after,
        "entry": entry.to_dict(),
    }


# --------------------------------------------------------------------------- #
# 4. Inject drift, then repair it
# --------------------------------------------------------------------------- #


def inject_drift(lab: Lab, device: str = "leaf-01", *, vlan: int | None = None) -> dict[str, Any]:
    """Remove an intended VLAN from one trunk, so the page can catch it.

    This is the deliberate break the presenter performs. It is a real configuration change
    on a real switch, which is the point: the drift the audience then sees is not a mock-up.

    It removes an **intended** VLAN, not an arbitrary one, because only a VLAN that intent
    wants can show up as drift. Breaking something intent does not care about would look
    like nothing happened, which is the opposite of a demo.
    """
    try:
        intended = lab.read_intent()
    except Exception as exc:
        return {"ok": False, "headline": "Could not read intended state", "detail": str(exc)}

    intent = intended.get(device)
    if intent is None or not intent.trunks:
        return {"ok": False, "headline": f"{device} is not a demo trunk device", "detail": ""}

    port = intent.trunks[0]
    target = vlan if vlan is not None else intent.vlans[-1]
    if target not in intent.vlans:
        return {
            "ok": False,
            "headline": f"VLAN {target} is not part of intended state on {device}",
            "detail": f"intent wants {', '.join(str(v) for v in intent.vlans)}",
        }

    plan = Plan(
        device=device,
        platform="eos",
        usecase="injected-drift",
        fmt="text",
        config=f"interface {port}\n   switchport trunk allowed vlan remove {target}",
        mode="merge",
        reason=f"remove VLAN {target} from {port} (demo break)",
    )

    try:
        result = lab.apply(plan)
    except EosApplyError as exc:
        return {"ok": False, "headline": f"Could not inject drift on {device}", "detail": str(exc)}

    if result.is_empty:
        return {
            "ok": True,
            "no_drift": True,
            "headline": f"{port} did not carry VLAN {target} to begin with",
            "detail": "apply the VLAN first, then inject drift",
        }

    append_entry(
        lab.audit,
        AuditEntry(
            kind="detected",
            device=device,
            summary=f"injected drift: removed VLAN {target} from {port}",
            autonomy="observe",
            detail=result.diff,
        ),
    )

    return {
        "ok": True,
        "device": device,
        "port": port,
        "vlan": target,
        "headline": f"Broke it on purpose: VLAN {target} removed from {port} on {device}",
        "detail": "now refresh the overview — the page will catch it",
        "diff": result.diff,
    }


def remediate(lab: Lab, device: str, *, approver: str) -> dict[str, Any]:
    """Close the loop: put back whatever the device is missing relative to intent.

    This is the other half of the demo and the reason the page is worth showing. It reads
    intent, reads the device, plans only the *additions* needed, previews, and applies on
    approval — the same gate as any other change.

    Deliberately additive only. It does not delete anything the switch carries and intent
    does not know about, because in a real estate that would be how automation removes
    something a human added during an incident.
    """
    if not approver or not approver.strip():
        return {
            "ok": False,
            "headline": "An approver is required",
            "detail": "no change is applied without a named person on the record",
        }

    try:
        intended = lab.read_intent()
    except Exception as exc:
        return {"ok": False, "headline": "Could not read intended state", "detail": str(exc)}

    intent = intended.get(device)
    if intent is None:
        return {"ok": False, "headline": f"{device} is not in the fabric", "detail": ""}

    actual = lab.read_actual(device)
    if not actual.reachable:
        return {
            "ok": False,
            "headline": f"{device} did not answer",
            "detail": actual.error or "unreachable",
        }

    # What is missing, worked out from the two states rather than from the drift objects,
    # so this stays correct for any kind of gap the drift engine can report.
    lines: list[str] = []
    missing_vlans = sorted(set(intent.vlans) - set(actual.vlans))
    if missing_vlans:
        lines.append(f"vlan {missing_vlans[0]}")
        lines.append(f"   name RESTORED_{missing_vlans[0]}")

    for port in intent.trunks:
        wanted = set(intent.trunk_allowed.get(port, intent.vlans))
        have = set(actual.trunk_allowed.get(port, ()))
        for vlan in sorted(wanted - have):
            lines.append(f"interface {port}")
            lines.append(f"   switchport trunk allowed vlan add {vlan}")

    if not lines:
        return {
            "ok": True,
            "no_drift": True,
            "headline": f"{device} already matches intended state",
            "detail": "there is nothing to remediate",
        }

    plan = Plan(
        device=device,
        platform="eos",
        usecase="remediate-drift",
        fmt="text",
        config="\n".join(lines),
        mode="merge",
        reason="restore intended VLANs and trunk allow-lists",
    )

    try:
        current = lab.preview(plan)
    except EosApplyError as exc:
        return {"ok": False, "headline": f"Could not preview on {device}", "detail": str(exc)}

    if current.is_empty:
        return {
            "ok": True,
            "no_drift": True,
            "headline": f"{device} already matches intended state",
            "detail": "the preview found nothing to change",
        }

    try:
        pending = request_change(new_id("remediate-drift", device), plan, current)
        entry = approve_and_apply(pending, approver, audit=lab.audit, apply=lab.apply)
    except GateRefused as exc:
        return {"ok": False, "headline": "Refused", "detail": str(exc)}
    except EosApplyError as exc:
        return {"ok": False, "headline": f"Remediation failed on {device}", "detail": str(exc)}

    return {
        "ok": True,
        "applied": entry.kind == "applied",
        "change_id": entry.event_id,
        "approver": entry.approver,
        "headline": (
            f"{device} remediated and recorded"
            if entry.kind == "applied"
            else "Nothing was sent; the device already matched intent"
        ),
        "diff": entry.diff_after,
        "entry": entry.to_dict(),
    }


# --------------------------------------------------------------------------- #
# 5. Lifecycle (SPEC-007)
# --------------------------------------------------------------------------- #


def lifecycle(lab: Lab) -> dict[str, Any]:
    """Drift and OS version per device — the CVE and upgrade conversation, as a picture.

    Deliberately not a full EOL database. It answers the question the CTO actually asks:
    which devices are not doing what we said they should, and what are they running?
    """
    try:
        intended = lab.read_intent()
    except Exception as exc:
        return {"ok": False, "headline": "Could not read intended state", "detail": str(exc)}

    rows: list[dict[str, Any]] = []
    os_counts: dict[str, int] = {}

    for name, intent in sorted(intended.items()):
        actual = lab.read_actual(name)
        findings = compute_drift(intent, actual)
        verdict = summarise(findings)
        version = actual.os_version or "unknown"
        os_counts[version] = os_counts.get(version, 0) + 1
        rows.append(
            {
                "device": name,
                "role": intent.role,
                "state": verdict["state"],
                "headline": verdict["headline"],
                "findings": len(findings),
                "os_version": version,
                "target_os": intent.target_os or "not set",
                "reachable": actual.reachable,
            }
        )

    compliant = sum(1 for r in rows if r["state"] == "compliant")
    return {
        "ok": True,
        "headline": f"{compliant} of {len(rows)} devices match intended state",
        "compliant": compliant,
        "total": len(rows),
        "os_fleet": os_counts,
        "devices": rows,
        "note": (
            "Operating system versions are read from each device, not from a spreadsheet. "
            "Set a target version per device in NetBox to turn this into an upgrade worklist."
        ),
    }


# --------------------------------------------------------------------------- #
# 6. The audit trail, for the page
# --------------------------------------------------------------------------- #


def audit_view(lab: Lab, limit: int = 50) -> dict[str, Any]:
    entries = lab.audit.read(limit=limit)
    return {
        "ok": True,
        "count": len(entries),
        "applied": len([e for e in entries if e.get("kind") == "applied"]),
        "entries": entries,
    }


# --------------------------------------------------------------------------- #
# 7. One intent, two vendors (SPEC-005)
# --------------------------------------------------------------------------- #


def two_vendors(lab: Lab) -> dict[str, Any]:
    """Prove the CTO's claim: the same intent and the same gate drive two vendors.

    Reports, per vendor, whether the device can be read and whether it matches what intent
    says it should be. It reads the FRR track directly rather than through the EOS `Lab`
    seam, because that track has its own home (`labs/l3ls-dual-dc`) and its own executor.

    States its own limits plainly. The FRR side diffs the rendered config; it does not
    pretend to have the same command-level apply path as EOS.
    """
    rows: list[dict[str, Any]] = []

    # Arist'a side, through the normal lab seam.
    try:
        intended = lab.read_intent()
    except Exception as exc:
        return {"ok": False, "headline": "Could not read intended state", "detail": str(exc)}

    leaf = intended.get("leaf-01")
    if leaf is not None:
        actual = lab.read_actual("leaf-01")
        findings = compute_drift(leaf, actual)
        rows.append(
            {
                "device": "leaf-01",
                "vendor": "Arista EOS",
                "platform": "eos",
                "reachable": actual.reachable,
                "state": summarise(findings)["state"],
                "headline": summarise(findings)["headline"],
                "source": leaf.source,
                "capability": "read, diff, approve and apply",
            }
        )

    # The FRR side, from its own track.
    from workbench import frr_executor

    try:
        frr_intent = frr_executor.intent_for_frr()
    except frr_executor.FrrError as exc:
        rows.append(
            {
                "device": "frr-core1",
                "vendor": "FRRouting",
                "platform": "frr",
                "reachable": False,
                "state": "unknown",
                "headline": str(exc),
                "source": "avd:labs/l3ls-dual-dc",
                "capability": "read and diff",
            }
        )
    else:
        actual = frr_executor.read_actual()
        rows.append(
            {
                "device": "frr-core1",
                "vendor": "FRRouting",
                "platform": "frr",
                "reachable": actual.reachable,
                "state": "compliant" if actual.reachable and actual.asn == frr_intent.asn else "drifted",
                "headline": (
                    f"AS {actual.asn} matches intent"
                    if actual.reachable and actual.asn == frr_intent.asn
                    else (actual.error or f"AS {actual.asn} does not match intent AS {frr_intent.asn}")
                ),
                "source": frr_intent.source,
                "capability": "read and diff",
            }
        )

    return {
        "ok": True,
        "headline": "The same intent, the same gate, two different vendors",
        "vendors": rows,
        "note": (
            "Arista is read, diffed, approved and applied through the workbench. FRR is read "
            "and diffed here; its apply path is the dual-DC track's own frr-reload, which the "
            "audit record names. The claim on the screen is the part that is true today, not "
            "a promise about a driver that does not exist."
        ),
    }


def __getattr__(name: str) -> Any:  # pragma: no cover - import-time guard only
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
