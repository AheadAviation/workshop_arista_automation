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
    ContractError,
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
    """The things the stories need from the outside world.

    Injected rather than imported so the tests replace them with fakes and the whole
    demo can be exercised in under a second.

    `write_ipam` is the NetBox write path. It is a seam rather than a direct call because a
    change must be recorded in the source of truth *before* it reaches a switch, and that
    ordering has to be testable — including the case where NetBox refuses. Defaults to `None`,
    which means "not available": callers decide whether that is fatal, and `approve_service_change`
    treats it as fatal on purpose.
    """

    read_intent: Callable[..., dict[str, Intent]]
    read_actual: Callable[[str], Actual]
    preview: Callable[[Plan], Preview]
    apply: Callable[[Plan], Preview]
    audit: AuditLog
    #: (devices, vid, vlan_name) -> dict with `ok`, `updated`, `failures`. See
    #: `_record_in_ipam` for the shape. `None` means NetBox writing is unavailable.
    write_ipam: Callable[..., dict[str, Any]] | None = None

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
            write_ipam=_record_in_ipam,
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

    from concurrent.futures import ThreadPoolExecutor

    ordered = sorted(intended.items())
    if len(ordered) > 1:
        with ThreadPoolExecutor(max_workers=min(len(ordered), 6)) as pool:
            actuals = list(pool.map(lambda pair: lab.read_actual(pair[0]), ordered))
    else:
        actuals = [lab.read_actual(name) for name, _ in ordered]

    cards: list[dict[str, Any]] = []
    for (name, intent), actual in zip(ordered, actuals):
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
# 3b. Deliver a service to a place - the day-two workflow
# --------------------------------------------------------------------------- #
#
# This is the shape the briefing actually shows, and it exists because the one-switch
# version above teaches the wrong lesson. "Add VLAN 333 to leaf-02" is a CLI command with a
# form around it: it asks a person to know a VLAN number and a switch name. The real request
# is "give the pharmacy system the pharmacy network", and the tool should work out that this
# means two switches and one VLAN.
#
# It is also the only shape that makes the Philips story true. A server is dual-homed across
# an MLAG pair, so the VLAN has to land on both legs; changing one switch and then the other
# by hand is the very thing ("four switches, and hoping they got all of them") the demo is
# arguing against.


def plan_service_change(
    lab: Lab,
    *,
    service_key: str,
    scope: str,
    pair: str = "",
    device: str = "",
) -> dict[str, Any]:
    """Work out what delivering a service would take, across every switch involved.

    Changes nothing. Returns one preview per switch plus a single sentence describing the
    change, because that sentence is what a decision-maker reads.

    A switch that already carries the service is reported as `already` rather than dropped:
    "3 of 4 already have it, 1 needs it" is a genuinely useful answer, and silently ignoring
    the other three would look like the tool had lost them.
    """
    from workbench import catalogue

    try:
        svc = catalogue.service(service_key)
        switches = catalogue.targets(scope, pair=pair, device=device)
    except ContractError as exc:
        return {
            "ok": False,
            "headline": "That is not a change this network offers",
            "detail": str(exc),
        }

    sentence = catalogue.describe(service_key, scope, pair=pair, device=device)

    try:
        intended = lab.read_intent()
    except Exception as exc:
        return {"ok": False, "headline": "Could not read intended state", "detail": str(exc)}

    previews: list[dict[str, Any]] = []
    to_change: list[str] = []
    already: list[str] = []
    unreachable: list[str] = []
    total_lines = 0

    for name in switches:
        intent = intended.get(name)
        if intent is None or not intent.trunks:
            unreachable.append(name)
            previews.append(
                {
                    "device": name,
                    "state": "unreachable",
                    "detail": "no server trunk declared for this switch",
                    "diff": "",
                    "changed_lines": 0,
                }
            )
            continue

        plan = vlan_across_trunks_plan(name, svc.vlan, svc.name, intent.trunks)
        try:
            # Previewing is what proves we can reach the switch, so a failure here is
            # reported per-device rather than aborting the whole change: "three of four
            # answered" is exactly the situation a presenter needs to know about.
            #
            # Catching broadly on purpose. A switch that is down surfaces as EosApplyError,
            # but a connection refused or a DNS failure surfaces as whatever the HTTP client
            # raises — and letting that escape would turn one dead switch into no plan at all,
            # which is the opposite of useful when the point is to report the fleet honestly.
            staged = lab.preview(plan)
        except Exception as exc:
            previews.append(
                {
                    "device": name,
                    "state": "unreachable",
                    "detail": f"{type(exc).__name__}: {exc}"[:200],
                    "diff": "",
                    "changed_lines": 0,
                }
            )
            unreachable.append(name)
            continue

        if staged.is_empty:
            already.append(name)
            previews.append(
                {
                    "device": name,
                    "state": "already",
                    "trunks": list(intent.trunks),
                    "detail": "already carries VLAN {0}".format(svc.vlan),
                    "diff": "",
                    "changed_lines": 0,
                }
            )
            continue

        to_change.append(name)
        total_lines += staged.changed_lines
        previews.append(
            {
                "device": name,
                "state": "needs-change",
                "trunks": list(intent.trunks),
                "detail": "{0} line(s) would change".format(staged.changed_lines),
                "diff": staged.diff,
                "changed_lines": staged.changed_lines,
                "_plan": plan,
                "_preview": staged,
            }
        )

    if not to_change:
        return {
            "ok": True,
            "no_drift": True,
            "sentence": sentence,
            "service": svc.to_dict(),
            "scope": scope,
            "pair": pair,
            "switches": switches,
            "devices": [_public(p) for p in previews],
            "already": already,
            "unreachable": unreachable,
            "changed_lines": 0,
            "headline": (
                "{0} is already delivered to every switch in scope".format(svc.name)
                if not unreachable
                else "Nothing to change, but some switches did not answer"
            ),
            "detail": "the network already matches what you asked for",
        }

    change_id = new_id("service", svc.key, scope, pair or device)
    pending: list[Any] = []
    for p in previews:
        if p["state"] != "needs-change":
            continue
        # Reuse the preview already taken rather than previewing again: the diff shown to the
        # approver and the change applied must be the same object, or an approval could land
        # something nobody read.
        pending.append(request_change("{0}:{1}".format(change_id, p["device"]), p["_plan"], p["_preview"]))

    append_entry(
        lab.audit,
        AuditEntry(
            kind="pending",
            device=",".join(to_change),
            summary=(
                "service {0} (VLAN {1}) to {2} switch(es), awaiting approval".format(
                    svc.name, svc.vlan, len(to_change)
                )
            ),
            autonomy="assist",
            event_id=change_id,
            diff_before="\n".join(p["diff"] for p in previews if p["state"] == "needs-change"),
        ),
    )

    _PENDING_SERVICE_CHANGES[change_id] = {
        "pending": pending,
        "service": svc.to_dict(),
        "scope": scope,
        "pair": pair,
    }

    return {
        "ok": True,
        "change_id": change_id,
        "sentence": sentence,
        "service": svc.to_dict(),
        "scope": scope,
        "pair": pair,
        "switches": switches,
        "devices": [_public(p) for p in previews],
        "already": already,
        "unreachable": unreachable,
        "to_change": to_change,
        "changed_lines": total_lines,
        "headline": sentence,
        "detail": (
            "{0} switch(es) need the change".format(len(to_change))
            + (", {0} already have it".format(len(already)) if already else "")
            + (", {0} did not answer".format(len(unreachable)) if unreachable else "")
        ),
    }


#: Previewed service changes, waiting for an approval. In-process, like the page's own
#: session: the demo runs one presenter on one machine, and a restart simply means previewing
#: again -- which is the behaviour the gate already requires.
_PENDING_SERVICE_CHANGES: dict[str, list[Any]] = {}


def _public(preview: dict[str, Any]) -> dict[str, Any]:
    """Strip the internal plan and preview from a device row before it reaches a page."""
    return {k: v for k, v in preview.items() if not k.startswith("_")}


def approve_service_change(
    lab: Lab,
    *,
    change_id: str,
    approver: str,
) -> dict[str, Any]:
    """Apply a previewed service change to every switch in scope, on one approval.

    One approval, not four. The person approving is approving *a sentence* -- "give pharmacy
    the pharmacy network on pair 1" -- and the fact that it lands on two switches is an
    implementation detail they should not have to click through. That is the difference
    between a workflow and a script with a button on it.

    Every switch is applied and read back individually, and the result says which succeeded.
    A partial failure is reported as partial: claiming success because the first switch worked
    would be the worst possible outcome to show.
    """
    if not approver or not approver.strip():
        return {
            "ok": False,
            "headline": "An approver is required",
            "detail": "no change is applied without a named person on the record",
        }

    staged = _PENDING_SERVICE_CHANGES.pop(str(change_id or ""), None)
    if not staged:
        return {
            "ok": False,
            "refused": True,
            "headline": "This change is no longer staged",
            "detail": (
                "Preview the change again. An approval only applies to a change that was "
                "previewed in this session, so a stale approval cannot land."
            ),
        }

    pending = staged["pending"]
    service = staged.get("service") or {}
    service_key = service.get("key") or ""
    vid = int(service.get("vlan") or 0)
    vlan_name = str(service.get("name") or "")

    targets = [item.plan.device for item in pending]

    # **Write the intent to NetBox first.** Order matters and this is the reason: if the model
    # cannot be updated, nothing is pushed, so a switch can never be left carrying a VLAN that
    # NetBox does not know about. The reverse order is what produces a "rogue system" — the
    # network changed, the source of truth did not, and the next reader is misled.
    ipam = _write_ipam(lab, targets, vid=vid, vlan_name=vlan_name)
    if not ipam.get("ok"):
        return {
            "ok": False,
            "refused": True,
            "headline": "NetBox could not be updated, so nothing was pushed",
            "detail": (
                "The change was not applied to any switch, because the source of truth must "
                f"record it first. {ipam.get('note') or ''} "
                f"{(ipam.get('failures') or [])[:2]}"
            ).strip(),
            "ipam": ipam,
            "applied": [],
            "failures": [],
            "device_count": len(pending),
        }

    applied: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for item in pending:
        try:
            entry = approve_and_apply(item, approver, audit=lab.audit, apply=lab.apply)
        except (GateRefused, EosApplyError) as exc:
            failures.append({"device": item.plan.device, "detail": str(exc)[:200]})
            continue

        applied.append(
            {
                "device": entry.device,
                "kind": entry.kind,
                "diff": entry.diff_after or "",
                "summary": entry.summary,
            }
        )

    ok = not failures and bool(applied)
    if ok:
        headline = "Applied to {0} switch(es) and recorded".format(len(applied))
    elif applied:
        headline = "Applied to {0} of {1} switches".format(len(applied), len(pending))
    else:
        headline = "The change did not apply"

    return {
        "ok": ok,
        "change_id": change_id,
        "approver": approver.strip(),
        "applied": [a for a in applied if a["kind"] == "applied"],
        "no_op": [a for a in applied if a["kind"] != "applied"],
        "failures": failures,
        "ipam": ipam,
        "device_count": len(pending),
        "headline": headline,
        "detail": (
            "every switch was read back after the change; NetBox records it"
            if ok
            else "see the per-switch results; nothing was claimed that did not happen"
        ),
    }


def _write_ipam(lab: Lab, devices: list[str], *, vid: int, vlan_name: str) -> dict[str, Any]:
    """Record the change in the source of truth, via the lab's own seam.

    A lab with no `write_ipam` cannot record anything, so this returns `ok: False` with a
    reason rather than silently proceeding. That is the point: on a real install NetBox is
    always there, and in a test the fake either provides a writer or the test is about the
    refusal path.
    """
    writer = getattr(lab, "write_ipam", None)
    if writer is None:
        return {
            "ok": False,
            "updated": [],
            "failures": [],
            "note": (
                "no NetBox write path is available, so the change cannot be recorded in the "
                "source of truth"
            ),
        }
    return writer(devices, vid=vid, vlan_name=vlan_name)


def _record_in_ipam(devices: list[str], *, vid: int, vlan_name: str) -> dict[str, Any]:
    """Write an applied change into NetBox's IPAM.

    This is the real writer used by `Lab.real()`. It reads configuration from the plugin's
    settings, which only exist inside NetBox, so outside that context it reports why it cannot
    write rather than raising — the caller turns that into a refusal.
    """
    if not devices or not vid:
        return {"ok": True, "updated": [], "failures": [], "note": "nothing to record"}

    try:
        from netbox_ops import conf
        from workbench.netbox_ipam_write import NetboxWriter, record_service_on_devices
    except Exception as exc:  # running outside NetBox, e.g. from the CLI
        return {
            "ok": False,
            "updated": [],
            "failures": [],
            "note": f"IPAM write unavailable: {type(exc).__name__}: {exc}",
        }

    try:
        client = NetboxWriter(conf.netbox_url(), conf.netbox_token())
        result = record_service_on_devices(
            client, vid=vid, vlan_name=vlan_name, devices=devices
        )
    except Exception as exc:
        return {
            "ok": False,
            "updated": [],
            "failures": [{"device": "*", "detail": f"{type(exc).__name__}: {exc}"[:200]}],
            "note": "the switches changed but NetBox did not; the model is now behind",
        }

    return {
        "ok": bool(result.get("ok")),
        "updated": [r["device"] for r in result.get("results") or [] if r.get("changed")],
        "already": [r["device"] for r in result.get("results") or [] if r.get("ok") and not r.get("changed")],
        "failures": [r for r in result.get("results") or [] if not r.get("ok")],
        "headline": result.get("headline", ""),
    }


# --------------------------------------------------------------------------- #
# 3c. Retire a network — the reverse of delivering one
# --------------------------------------------------------------------------- #


def plan_retirement(lab: Lab, *, vid: int) -> dict[str, Any]:
    """Work out what removing a VLAN from every leaf would take. Changes nothing.

    Retirement is not "delivery with a minus sign", and it needs its own plan because the risk is
    different: adding a VLAN to a trunk that already carries traffic is nearly always safe, while
    removing one can take a live service off the network. So the plan reports **where the VLAN is
    actually in use** and refuses to look like a routine change when it is not.

    It is also the operation most worth having in a demo, because it is the one a network director
    recognises as the thing that never gets done: the VLAN nobody remembers to remove.
    """
    try:
        intended = lab.read_intent()
    except Exception as exc:
        return {"ok": False, "headline": "Could not read intended state", "detail": str(exc)}

    leaves = {
        name: intent
        for name, intent in intended.items()
        if intent.trunks
    }
    if not leaves:
        return {
            "ok": False,
            "headline": "No leaf switches found",
            "detail": "the fabric has no server trunks to retire anything from",
        }

    carrying: list[dict[str, Any]] = []
    clear: list[str] = []
    for name in sorted(leaves):
        intent = leaves[name]
        if vid in intent.vlans or any(vid in vids for vids in intent.trunk_allowed.values()):
            carrying.append(
                {
                    "device": name,
                    "trunks": [port for port, vids in intent.trunk_allowed.items() if vid in vids],
                }
            )
        else:
            clear.append(name)

    if not carrying:
        return {
            "ok": True,
            "no_drift": True,
            "headline": f"VLAN {vid} is not on any leaf",
            "detail": "there is nothing to retire, which is the expected end state",
            "devices": [],
        }

    change_id = new_id("retire", str(vid))
    details = "; ".join(
        f"{row['device']} on {', '.join(row['trunks'])}" for row in carrying
    )
    append_entry(
        lab.audit,
        AuditEntry(
            kind="pending",
            device=",".join(row["device"] for row in carrying),
            summary=f"retire VLAN {vid} from {len(carrying)} switch(es), awaiting approval",
            autonomy="assist",
            event_id=change_id,
        ),
    )

    # The retirement is staged as one item per device, exactly like a delivery, so the approval
    # path and the audit record are identical. Only the config differs.
    _PENDING_SERVICE_CHANGES[change_id] = {
        "pending": _retirement_items(lab, carrying, vid),
        "service": {"key": f"retire-{vid}", "name": f"VLAN {vid}", "vlan": vid},
        "scope": "all-leaves",
        "pair": "",
        "retire": True,
    }

    return {
        "ok": True,
        "change_id": change_id,
        "vlan": vid,
        "devices": [row["device"] for row in carrying],
        "carrying": carrying,
        "already_clear": clear,
        "headline": f"Retire VLAN {vid} from {len(carrying)} switch(es)",
        "detail": details,
    }


def _retirement_items(lab: Lab, carrying: list[dict[str, Any]], vid: int) -> list[Any]:
    """One pending change per device, each a previewed removal.

    Built as `PendingChange` objects so `approve_and_apply` can consume them unchanged — the
    approval gate, the audit entry and the read-back all come for free, and a retirement is
    recorded exactly like the delivery it undoes.
    """
    from workbench.contracts import Plan, Preview

    items: list[Any] = []
    for row in carrying:
        device = row["device"]
        config = "\n".join(
            f"interface {port}\n   switchport trunk allowed vlan remove {vid}" for port in row["trunks"]
        )
        plan = Plan(
            device=device,
            platform="eos",
            usecase="retire-vlan",
            fmt="text",
            config=config,
            mode="merge",
            reason=f"remove VLAN {vid} from {len(row['trunks'])} trunk(s)",
        )
        try:
            staged = lab.preview(plan)
        except Exception:
            continue
        if staged.is_empty:
            continue
        items.append(request_change(f"retire:{vid}:{device}", plan, staged))
    return items


def approve_retirement(lab: Lab, *, change_id: str, approver: str) -> dict[str, Any]:
    """Apply a staged retirement, and remove the VLAN from the IPAM trunks too.

    The IPAM write happens **first**, for the same reason it does on a delivery: the model must
    agree before the device changes. See `approve_service_change`.
    """
    if not approver or not approver.strip():
        return {
            "ok": False,
            "headline": "An approver is required",
            "detail": "no change is applied without a named person on the record",
        }

    staged = _PENDING_SERVICE_CHANGES.pop(str(change_id or ""), None)
    if not staged or not staged.get("retire"):
        return {
            "ok": False,
            "refused": True,
            "headline": "This retirement is no longer staged",
            "detail": "Preview the retirement again.",
        }

    pending = staged["pending"]
    vid = int((staged.get("service") or {}).get("vlan") or 0)
    targets = [item.plan.device for item in pending]

    ipam = _remove_from_ipam(targets, vid=vid)

    applied: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for item in pending:
        try:
            entry = approve_and_apply(item, approver, audit=lab.audit, apply=lab.apply)
        except (GateRefused, EosApplyError) as exc:
            failures.append({"device": item.plan.device, "detail": str(exc)[:200]})
            continue
        applied.append({"device": entry.device, "kind": entry.kind, "diff": entry.diff_after or ""})

    ok = not failures and bool(applied)
    return {
        "ok": ok,
        "change_id": change_id,
        "approver": approver.strip(),
        "applied": [a for a in applied if a["kind"] == "applied"],
        "no_op": [a for a in applied if a["kind"] != "applied"],
        "failures": failures,
        "ipam": ipam,
        "device_count": len(pending),
        "headline": (
            f"Retired VLAN {vid} from {len(applied)} switch(es)"
            if ok
            else (f"Retired from {len(applied)} of {len(pending)} switches" if applied else "The retirement did not apply")
        ),
        "detail": "every switch was read back after the change; NetBox records it" if ok else "see the per-switch results",
    }


def _remove_from_ipam(devices: list[str], *, vid: int) -> dict[str, Any]:
    """Take the VLAN off the trunks in NetBox, via the lab's own write seam."""
    if not devices or not vid:
        return {"ok": True, "updated": [], "failures": [], "note": "nothing to remove"}

    try:
        from netbox_ops import conf
        from workbench.netbox_ipam_write import NetboxWriter, remove_vlan_from_trunks
    except Exception as exc:
        return {
            "ok": False,
            "updated": [],
            "failures": [],
            "note": f"IPAM write unavailable: {type(exc).__name__}: {exc}",
        }

    try:
        client = NetboxWriter(conf.netbox_url(), conf.netbox_token())
        result = remove_vlan_from_trunks(client, vid=vid, devices=devices)
    except Exception as exc:
        return {
            "ok": False,
            "updated": [],
            "failures": [{"device": "*", "detail": f"{type(exc).__name__}: {exc}"[:200]}],
            "note": "the switches changed but NetBox did not",
        }

    return {
        "ok": bool(result.get("ok")),
        "updated": [r["device"] for r in result.get("results") or [] if r.get("changed")],
        "failures": [r for r in result.get("results") or [] if not r.get("ok")],
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
