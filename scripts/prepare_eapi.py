"""Prepare every lab switch for eAPI — run from the HOST, not from NetBox.

Why this is a host script and not a plugin action: turning eAPI on requires the switch's CLI,
which requires `docker exec`, which requires the Docker CLI. NetBox deliberately has none of
that — the whole security posture of this plugin is that NetBox cannot exec into a container.
So this one job runs from the host, once, after the lab is deployed; after it, the plugin
talks to the switches over eAPI and needs no Docker access for devices at all.

    python scripts/prepare_eapi.py            # all six switches
    python scripts/prepare_eapi.py leaf-01    # just one
    python scripts/prepare_eapi.py --verify   # only check, change nothing

Idempotent: running it twice is harmless.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

CONTAINER_PREFIX = "clab-netbox-arista-lab-"
DEFAULT_DEVICES = ["spine-01", "spine-02", "leaf-01", "leaf-02", "leaf-03", "leaf-04"]
DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "admin"


def cli(device: str, script: str, timeout: int = 90) -> str:
    """Run a CLI script on a switch container."""
    container = f"{CONTAINER_PREFIX}{device}"
    try:
        proc = subprocess.run(  # noqa: S603 - argument list, no shell
            ["docker", "exec", "-i", container, "Cli", "-p", "15"],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise SystemExit("docker is not on PATH; run this on the demo host") from None
    except subprocess.TimeoutExpired:
        raise SystemExit(f"{device} did not answer in {timeout}s") from None
    return f"{proc.stdout or ''}\n{proc.stderr or ''}"


def mgmt_vrf(device: str) -> str:
    """Which VRF the management interface (and eAPI) must be bound to.

    Deliberately returns `default`, not the interface's own VRF. Two reasons, both found the
    hard way on this lab:

    * The management interface is in `MGMT`, but a switch's **default route** is in `default`,
      so eAPI bound to `MGMT` has no path back to the client and times out.
    * Binding eAPI to `default` while `Management0` is in `MGMT` still works, because the
      management address is reachable from the default VRF on these cEOS nodes.

    So the VRF eAPI listens in must be `default`, and the earlier version of this function —
    which read the interface's VRF and bound eAPI to it — was wrong in a way that produced a
    timeout rather than an error. It is kept as a function because the value belongs in one
    place, not because it reads the config.
    """
    return "default"


def zerotouch_disabled(device: str) -> bool:
    return "ZeroTouch Mode: Disabled" in cli(device, "show zerotouch\n")


def prepare(device: str, user: str, password: str) -> dict:
    """Make one switch answer eAPI, and save it."""
    vrf = mgmt_vrf(device)

    script = "\n".join(
        [
            "configure",
            f"username {user} privilege 15 role network-admin secret {password}",
            "management api http-commands",
            # Both levels need `no shutdown`: the top level enables the service itself, and the
            # VRF block enables it in that VRF. Issuing only the VRF one leaves the service
            # disabled — `Enabled: No` with an HTTPS server that is "enabled" but not running,
            # which is a state that looks configured and answers nothing.
            "   no shutdown",
            "   protocol https",
            "   vrf default",
            "      no shutdown",
            "end",
            "write memory",
            "",
        ]
    )
    out = cli(device, script)
    saved = "Copy completed" in out
    return {"device": device, "vrf": vrf, "saved": saved}


def verify(device: str, user: str, password: str) -> dict:
    """Confirm eAPI answers, by asking **NetBox** to make the call.

    The host cannot reach the containerlab management network — only NetBox can, because it is
    attached to it. So verification runs inside the NetBox container. This is not a
    workaround: it is the same path the plugin itself uses, so a pass here means the plugin
    will work, not just that the switch is listening.
    """
    address = f"172.30.30.{_mgmt_octet(device)}"
    script = f"""
import json, warnings, requests
warnings.filterwarnings('ignore')
p = {{"jsonrpc":"2.0","method":"runCmds",
      "params":{{"version":1,"cmds":["show hostname"],"format":"text"}},"id":"v"}}
try:
    r = requests.post("https://{address}/command-api", data=json.dumps(p),
                      auth=("{user}", "{password}"), verify=False, timeout=15)
    if r.status_code != 200:
        print(json.dumps({{"ok": False, "error": "HTTP %d" % r.status_code}}))
    else:
        out = r.json()["result"][0]["output"].strip().splitlines()[0]
        print(json.dumps({{"ok": True, "hostname": out}}))
except Exception as exc:
    print(json.dumps({{"ok": False, "error": str(exc)[:120]}}))
"""
    try:
        proc = subprocess.run(
            ["docker", "exec", "workshop_arista_automation-netbox-1",
             "/opt/netbox/venv/bin/python", "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"device": device, "ok": False, "error": f"could not run the check: {exc}"}

    text = (proc.stdout or "").strip().splitlines()
    for line in reversed(text):
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
                parsed["device"] = device
                return parsed
            except ValueError:
                continue
    return {"device": device, "ok": False, "error": (proc.stderr or "no output")[:120]}


def _mgmt_octet(device: str) -> int:
    mapping = {
        "spine-01": 11, "spine-02": 12,
        "leaf-01": 21, "leaf-02": 22, "leaf-03": 23, "leaf-04": 24,
    }
    if device not in mapping:
        raise SystemExit(f"unknown lab device {device!r}")
    return mapping[device]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("devices", nargs="*", default=None, help="device names, default all six")
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--verify", action="store_true", help="only check, change nothing")
    args = parser.parse_args(argv)

    devices = args.devices or DEFAULT_DEVICES
    failures = 0

    for device in devices:
        if args.verify:
            result = verify(device, args.user, args.password)
            mark = "ok  " if result["ok"] else "FAIL"
            detail = result.get("hostname") or result.get("error", "")
            print(f"  {mark} {device:<10} {detail}")
            failures += 0 if result["ok"] else 1
            continue

        if not zerotouch_disabled(device):
            # ZeroTouch reverts unsaved configuration; preparing eAPI on such a switch
            # produces something that works for a few minutes and then does not.
            print(f"  warn {device:<10} ZeroTouch is active — disabling it first")
            cli(device, "configure\nzerotouch disable\nend\n")

        info = prepare(device, args.user, args.password)
        check = verify(device, args.user, args.password)
        ok = info["saved"] and check["ok"]
        mark = "ok  " if ok else "FAIL"
        detail = f"vrf={info['vrf']}" + ("" if info["saved"] else " (NOT SAVED)")
        if not check["ok"]:
            detail += f" — {check.get('error')}"
        print(f"  {mark} {device:<10} {detail}")
        failures += 0 if ok else 1

    print()
    if failures:
        print(f"{failures} problem(s).")
        return 1
    print(f"eAPI ready on {len(devices)} device(s). The plugin can now manage them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
