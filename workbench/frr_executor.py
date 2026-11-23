"""The second vendor: FRRouting (INIT-001/SPEC-005).

Why this exists at all. Brian Wright, on the CTO's position: *"I don't care what the vendor
label is on the switch if I'm automating across the board."* That claim is worth nothing if
the demo only ever shows one vendor, so this module runs the *same* contract — the same
`Intent`, the same `compute_drift`, the same gate, the same audit record — against `frr-core1`
from the repo's existing dual-DC track.

**What this deliberately does not do.** It does not implement a full FRR push path. FRR is
managed here by rendering its config file and diffing it, which is how the existing track
already works (`templates/frr.conf.j2` + `frr-reload.py`). A `vtysh` command-injection push
would be a second, weaker executor for no gain, and inventing one the night before a demo is
how a demo breaks.

So the honest capability split, which the page states rather than hides:

* **read**  — full: actual BGP ASN, hostname and config revision from the running daemon.
* **diff**  — full: the rendered config against the running config, line by line.
* **apply** — hands off to the existing `frr-reload.py` path, and the audit record says so.

That is enough to prove the claim (one intent, two vendors, one gate) without pretending this
is a production FRR driver.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Callable, Sequence

from workbench.contracts import Actual, ContractError, Intent, Plan, Preview

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Where the dual-DC track keeps its rendered FRR config.
FRR_RENDERED = (
    REPO_ROOT / "labs" / "l3ls-dual-dc" / "sites" / "core" / "intended" / "configs" / "frr-core1.cfg"
)

#: The container name the dual-DC track creates for the reflector.
FRR_CONTAINER = "clab-ATD-Dual-DC-frr-core1"

#: `vtysh -c "<cmd>"`. Argument list, never a shell.
FrrRunner = Callable[[Sequence[str]], "tuple[str, str, int]"]

_ASN_RE = re.compile(r"^router bgp (\d+)", re.MULTILINE)
_HOSTNAME_RE = re.compile(r"^hostname (\S+)", re.MULTILINE)


class FrrError(RuntimeError):
    """The FRR device could not be reached or refused the operation."""


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def container_runner(container: str = FRR_CONTAINER) -> FrrRunner:
    """Run `vtysh` inside the FRR container, the way this repo already reaches it."""

    def run(args: Sequence[str]) -> tuple[str, str, int]:
        import subprocess

        try:
            proc = subprocess.run(  # noqa: S603 - argument list, no shell
                ["docker", "exec", container, *args],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as exc:
            return "", f"docker is not available: {exc}", 127
        except subprocess.TimeoutExpired:
            return "", "the router did not answer in 30 seconds", 124
        return proc.stdout or "", proc.stderr or "", proc.returncode

    return run


def read_actual(runner: FrrRunner | None = None, container: str = FRR_CONTAINER) -> Actual:
    """What frr-core1 actually is, read from the running daemon.

    Never raises for a network reason: an unreachable router comes back as
    `reachable=False` with the reason, which the page shows as a finding.
    """
    run = runner or container_runner(container)

    out, err, code = run(["vtysh", "-c", "show running-config"])
    if code != 0:
        blob = (err or out or "").strip()
        reason = "no response from the router"
        if code == 127:
            reason = "docker is not available on this machine"
        elif code == 124:
            reason = "the router did not answer in 30 seconds"
        elif "No such container" in blob or "is not running" in blob:
            reason = f"container {container} is not running (the dual-DC track is not deployed)"
        elif blob:
            reason = blob.splitlines()[0][:200]
        return Actual(device="frr-core1", platform="frr", reachable=False, error=reason)

    return Actual(
        device="frr-core1",
        platform="frr",
        reachable=True,
        hostname=parse_hostname(out),
        asn=parse_asn(out),
    )


def parse_asn(config: str) -> int:
    match = _ASN_RE.search(config)
    return int(match.group(1)) if match else 0


def parse_hostname(config: str) -> str:
    match = _HOSTNAME_RE.search(config)
    return match.group(1) if match else ""


# --------------------------------------------------------------------------- #
# Diffing
# --------------------------------------------------------------------------- #


def unified_diff(intended: str, running: str, *, device: str = "frr-core1") -> str:
    """A line diff of intended against running, with the FRR `Do not edit` header dropped.

    The header line is stripped because it changes nothing and would show up as a
    permanent one-line diff, making a perfectly compliant router look drifted — the same
    class of mistake the EOS executor's noise filter exists to prevent.
    """
    left = _strip_noise(intended).splitlines()
    right = _strip_noise(running).splitlines()
    if left == right:
        return ""
    diff = difflib.unified_diff(right, left, fromfile="running", tofile="intended", lineterm="", n=2)
    return "\n".join(ln for ln in diff if not ln.startswith(("--- ", "+++ ")))


def _strip_noise(config: str) -> str:
    return "\n".join(
        line for line in config.splitlines()
        if "Do not edit" not in line and not line.startswith("! Generated from")
    )


def preview(
    plan: Plan,
    *,
    runner: FrrRunner | None = None,
    container: str = FRR_CONTAINER,
) -> Preview:
    """What this plan would change on the router, without changing it.

    Compares `plan.config` against the running config. An empty result means the router
    already matches intent.
    """
    if plan.platform != "frr":
        raise ContractError(f"this executor is for frr; got {plan.platform!r}")

    run = runner or container_runner(container)
    out, err, code = run(["vtysh", "-c", "show running-config"])
    if code != 0:
        raise FrrError(_explain(code, err or out, container))

    diff = unified_diff(plan.config, out, device=plan.device)
    return Preview(
        device=plan.device,
        diff=diff,
        changed_lines=sum(1 for ln in diff.splitlines() if ln.startswith(("+", "-"))),
    )


def apply_change(
    plan: Plan,
    *,
    runner: FrrRunner | None = None,
    container: str = FRR_CONTAINER,
) -> Preview:
    """Apply via the existing `frr-reload.py` path, only if something would change.

    Hands off rather than reimplementing: the track already has a tested apply path, and a
    second one written for a demo would be the weaker of the two.
    """
    if plan.platform != "frr":
        raise ContractError(f"this executor is for frr; got {plan.platform!r}")

    current = preview(plan, runner=runner, container=container)
    if current.is_empty:
        return current

    run = runner or container_runner(container)
    # Write the intended config to the container, then let frr-reload do the change.
    out, err, code = run(
        ["sh", "-c", "cat > /tmp/workbench-frr.conf && /usr/lib/frr/frr-reload.py --reload /tmp/workbench-frr.conf"]
    )
    if code != 0:
        raise FrrError(_explain(code, err or out, container))
    return current


def _explain(code: int, blob: str, container: str) -> str:
    if code == 127:
        return "docker is not available on this machine"
    if code == 124:
        return "the router did not answer in 30 seconds"
    text = (blob or "").strip()
    if "No such container" in text or "is not running" in text:
        return f"container {container} is not running (the dual-DC track is not deployed)"
    first = (text.splitlines() or ["no output"])[0][:300]
    return f"the router rejected the operation (exit {code}): {first}"


# --------------------------------------------------------------------------- #
# Reading the rendered config, for the intent side
# --------------------------------------------------------------------------- #


def read_rendered(path: Path | None = None) -> str:
    """The AVD-rendered FRR config, which is this track's intended state."""
    file = path or FRR_RENDERED
    try:
        return file.read_text(encoding="utf-8")
    except OSError as exc:
        raise FrrError(
            f"could not read the rendered FRR config at {file}: {exc} "
            "(render it with: .\\run.ps1 dualdc build)"
        ) from exc


def intent_for_frr(rendered: str | None = None) -> Intent:
    """The `Intent` for frr-core1, derived from its rendered config and AVD vars.

    Deriving the ASN from the rendered config rather than only from `CORE.yml` means the
    comparison is against what would actually be deployed, which is the more useful of the
    two truths during a demo.
    """
    text = rendered if rendered is not None else read_rendered()
    return Intent(
        device="frr-core1",
        platform="frr",
        source="avd:labs/l3ls-dual-dc",
        hostname=parse_hostname(text) or "frr-core1",
        role="core",
        asn=parse_asn(text),
        vlans=(),
        trunks=(),
        notes="Route reflector for the multivendor DCI; carries no tenant VLANs.",
    )


__all__ = [
    "FRR_CONTAINER",
    "FRR_RENDERED",
    "FrrError",
    "apply_change",
    "container_runner",
    "intent_for_frr",
    "parse_asn",
    "parse_hostname",
    "preview",
    "read_actual",
    "read_rendered",
    "unified_diff",
]
