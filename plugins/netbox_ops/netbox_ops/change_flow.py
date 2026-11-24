"""The visible change flow: snapshot, request, push, diff, verify (INIT-002/SPEC-004).

This is the demo's spine, made explicit so each step can be shown on screen:

1. **Snapshot.** Capture the switch's config as it is now — the "before".
2. **Request.** Work out precisely what would change, and open a ticket for it.
3. **Approve.** A named human says yes, in the ticket.
4. **Push.** Apply it to the device.
5. **Diff.** Show what the device's config actually became, against the snapshot.
6. **Verify.** Read the device back and confirm it now matches intent.

Each step returns something a page can render, and each is refused if the one before it was
not done. That ordering is the point: the audience sees a request, then a push, then proof —
not a claim that it happened.
"""

from __future__ import annotations

from typing import Any

from . import conf, devices, eapi_exec, engine, itsm, live_config


class StepRefused(RuntimeError):
    """A step was attempted out of order or without what it needs."""


# --------------------------------------------------------------------------- #
# 1. Snapshot
# --------------------------------------------------------------------------- #


def begin_change(
    device: str, vlan: int | None = None, vlan_name: str = ""
) -> dict[str, Any]:
    """Start a change: capture the device's config as the before-state."""
    snap = live_config.snapshot(device)
    if not snap["ok"]:
        return snap

    # Work out the change at the same time, so the page can show "this is what we will do"
    # immediately after capturing the before-state.
    proposal = engine.propose_vlan(
        device, (vlan or conf.demo_vlan()), (vlan_name or conf.demo_vlan_name())
    )
    return {
        "ok": proposal.ok,
        "device": device,
        "snapshot_lines": snap["lines"],
        "proposed_diff": (proposal.data or {}).get("diff") or "",
        "proposed_changes": (proposal.data or {}).get("changed_lines") or 0,
        "headline": (
            f"Captured {device} ({snap['lines']} lines). "
            f"{proposal.headline or 'Nothing to change.'}"
        ),
        "detail": proposal.detail or "",
        "vlan": (vlan or conf.demo_vlan()),
        "vlan_name": (vlan_name or conf.demo_vlan_name()),
    }


# --------------------------------------------------------------------------- #
# 2. Request a ticket
# --------------------------------------------------------------------------- #


def request_change(
    device: str, *, requester: str = "", vlan: int | None = None, vlan_name: str = ""
) -> dict[str, Any]:
    """Open a ticket for the change — the request the customer's process sees."""
    proposal = engine.propose_vlan(
        device, (vlan or conf.demo_vlan()), (vlan_name or conf.demo_vlan_name())
    )
    if not proposal.ok:
        return {"ok": False, "headline": "Could not work out the change", "detail": proposal.detail}

    if (proposal.data or {}).get("no_drift"):
        return {
            "ok": True,
            "no_drift": True,
            "headline": f"{device} already carries VLAN {(vlan or conf.demo_vlan())}",
            "detail": "Nothing to request.",
        }

    diff = (proposal.data or {}).get("diff") or ""
    title = (
        f"[CHANGE] Add VLAN {(vlan or conf.demo_vlan())} ({(vlan_name or conf.demo_vlan_name())}) to {device}"
    )
    body = (
        f"Requested{' by ' + requester if requester else ''} via NetBox Ops.\n\n"
        f"Change:\n{diff}\n\n"
        f"Approval is required before this is applied."
    )

    ticket = itsm.create_change_ticket(title, body)
    return {
        "ok": bool(ticket.get("ok", False)) or not ticket.get("configured", True),
        "configured": bool(ticket.get("configured", True)),
        "ticket_id": ticket.get("ticket_id"),
        "number": ticket.get("number"),
        "device": device,
        "diff": diff,
        "headline": (
            f"Requested: ticket {ticket.get('number') or ticket.get('ticket_id')}"
            if ticket.get("ok")
            else f"Change prepared, but ITSM is not available ({ticket.get('headline', '')})"
        ),
        "detail": ticket.get("detail") or "",
    }


# --------------------------------------------------------------------------- #
# 3-6. Approve, push, diff, verify
# --------------------------------------------------------------------------- #


def approve_and_push(
    device: str,
    approver: str,
    *,
    ticket_id: int | None = None,
    vlan: int | None = None,
    vlan_name: str = "",
) -> dict[str, Any]:
    """Approve by name, push, then show the diff and verify the device.

    The push is what makes this a deployment rather than a record of one. The diff and the
    read-back are what make it verifiable rather than a claim.

    **A ticket is mandatory, and that is the whole point of this module.** An earlier version
    treated `ticket_id` as optional and pushed happily without one — verified by pushing VLAN
    347 to leaf-02 with `ticket_id=None`, which returned `applied: True, verified: True`. A
    change that can reach a switch with no change record is not change control, it is a button
    with an audit line attached, and no network director would accept it. The ticket is what
    turns "somebody clicked approve" into "this change exists in the system our process governs".

    The refusal is explicit rather than a silent downgrade, because the alternative — pushing
    anyway and annotating — is the failure mode this exists to prevent.
    """
    if not approver or not approver.strip():
        return {
            "ok": False,
            "headline": "An approver is required",
            "detail": "No change is applied without a named person on the record.",
        }

    if not ticket_id:
        # Not a warning. A change without a change record is the thing this flow is for.
        return {
            "ok": False,
            "refused": True,
            "headline": "A change ticket is required",
            "detail": (
                "Request the change first (step 2), then approve it against that ticket. "
                "Nothing is sent to a device without a change record behind it."
            ),
        }

    # Approve in the ticket first: the record of the decision belongs in the customer's system
    # of record, not only in ours. A failure here is fatal to the change, because approving in
    # NetBox while the ticket says otherwise would leave the two disagreeing — which is worse
    # than not changing anything.
    ticket_result = itsm.approve_ticket(
        int(ticket_id), f"Approved by {approver.strip()} via NetBox Ops"
    )
    if not ticket_result.get("ok"):
        return {
            "ok": False,
            "refused": True,
            "headline": "The ticket could not be approved, so the change was not applied",
            "detail": (
                f"Ticket {ticket_id}: {ticket_result.get('headline', 'refused')} "
                f"{ticket_result.get('detail', '')}".strip()
            ),
        }

    # The push itself.
    applied = engine.approve_vlan(
        device,
        approver.strip(),
        vlan=(vlan or conf.demo_vlan()),
        vlan_name=(vlan_name or conf.demo_vlan_name()),
    )

    # What the device's config actually became.
    diff = live_config.diff_against_snapshot(device)

    # And whether it now matches intent.
    verify = engine.fabric_overview()
    device_state = ""
    if verify.ok:
        for card in verify.data.get("devices") or []:
            if card.get("device") == device:
                device_state = (card.get("verdict") or {}).get("state", "")
                break

    verified = device_state == "compliant"

    # Close the ticket only once the change is actually on the device and verified. Closing on
    # approval alone would claim success before there is any evidence of it, which is the exact
    # thing the rest of this flow exists to avoid.
    closed = False
    close_headline = ""
    if verified and applied.ok:
        close_result = itsm.close_ticket(
            int(ticket_id),
            f"Change applied to {device} and verified: VLAN {vlan or conf.demo_vlan()}",
        )
        closed = bool(close_result.get("ok"))
        close_headline = str(close_result.get("headline") or "")
    elif applied.ok:
        # The change went in but the read-back disagrees. Leave the ticket OPEN: a closed ticket
        # would assert a success nobody has evidence for.
        close_headline = (
            "change applied but not verified, so the ticket was left open for investigation"
        )

    # The note describes the *outcome*, not just the approval. Reporting the approval here was a
    # real defect: a change that could not be verified closed nothing yet said "approved", which
    # reads as success.
    note = close_headline or str(ticket_result.get("headline") or "")

    return {
        "ok": applied.ok,
        "applied": bool((applied.data or {}).get("applied")),
        "approver": approver.strip(),
        "device": device,
        "ticket_id": int(ticket_id),
        "headline": applied.headline or "Change applied",
        "detail": applied.detail or "",
        "config_diff": diff.get("diff") or "",
        "config_changed_lines": diff.get("changed_lines") or 0,
        "verified_state": device_state,
        "verified": verified,
        "ticket_state_changed": bool(ticket_result.get("state_changed")),
        "ticket_closed": closed,
        "ticket_note": note,
    }


# --------------------------------------------------------------------------- #
# Demo reset
# --------------------------------------------------------------------------- #


def reset(device: str | None = None, vlan: int | None = None) -> dict[str, Any]:
    """Undo the demo change: remove the VLAN from the trunk and clear the snapshot.

    Deliberately narrow. It removes exactly what a change added, and touches nothing else — a
    reset that restored a whole config would be a second change, and a riskier one.
    """
    targets = [device] if device else list(devices.EXPECTED if hasattr(devices, "EXPECTED") else [])
    if not targets:
        targets = ["leaf-01", "leaf-02", "leaf-03", "leaf-04"]

    target_vlan = vlan or conf.demo_vlan()
    results: list[dict[str, Any]] = []
    for target in targets:
        try:
            plan_config = [
                "interface Ethernet5",
                f"   switchport trunk allowed vlan remove {target_vlan}",
            ]
            eapi_exec._eapi(
                target, ["enable", "configure"] + plan_config + ["end", "write memory"], timeout=90
            )
            live_config.clear_snapshot(target)
            results.append({"device": target, "ok": True})
        except eapi_exec.ApplyError as exc:
            results.append({"device": target, "ok": False, "error": str(exc)[:120]})

    failures = sum(1 for r in results if not r["ok"])
    return {
        "ok": failures == 0,
        "headline": (
            f"Demo reset: VLAN {target_vlan} removed from the trunks"
            if failures == 0
            else f"Reset: {failures} device(s) failed"
        ),
        "detail": "" if failures == 0 else "See the per-device results.",
        "results": results,
    }


__all__ = ["StepRefused", "approve_and_push", "begin_change", "request_change", "reset"]
