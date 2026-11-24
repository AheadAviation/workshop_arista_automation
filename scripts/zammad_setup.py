"""Set up Zammad and hand its credentials to the plugin (INIT-002/SPEC-006).

Run from the host:

    python scripts/zammad_setup.py           # set up, then report
    python scripts/zammad_setup.py --verify  # only prove it works, change nothing

`.\\run.ps1 itsm` calls this after the stack is up, so the whole ITSM story is one command.

**Why this is a host script and not a compose service.** An earlier version ran the setup in a
one-shot container. It raced: on a fresh database it could start while `zammad-init` was still
migrating, find no schema, and give up — or, worse, succeed against a half-built database. The
container reported success or hung, depending on timing. Running against the long-lived
railsserver with retries removes the race entirely, and the retry loop is honest about waiting
for a service that genuinely takes minutes to boot on a cold database.

**Why it extracts the token rather than assuming one.** Zammad's `Token` model has
`before_create :generate_token`, which overwrites any value you set. So the token is generated
and then *read back*, and the value the plugin needs is what comes out — not what went in. The
bootstrap script pins it afterwards so the value is stable across wipes, but this runner does
not depend on that succeeding: if a future Zammad changes the callback, it simply reports the
real value.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "zammad_bootstrap.rb"

#: Both compose files, in this order. The ITSM services are defined in the second one, and the
#: first supplies the named `netbox-arista-lab` network the plugin reaches Zammad over. Using
#: the ITSM file alone starts a Zammad the plugin cannot resolve.
COMPOSE = [
    "docker", "compose",
    "-f", "docker-compose.yml",
    "-f", "docker-compose.itsm.yml",
    "--profile", "itsm",
]

RAILSSERVER = "workshop_arista_automation-zammad-railsserver-1"
NGINX = "workshop_arista_automation-zammad-nginx-1"

#: Where the plugin's config lives. Rewritten in place, so a token change cannot leave the
#: repository holding a stale value that only fails at ticket time.
PLUGINS_CONFIG = REPO_ROOT / "docker" / "netbox" / "configuration" / "plugins.py"


def _run(args: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - argument list, no shell
        args, capture_output=True, text=True, timeout=timeout, check=False, cwd=REPO_ROOT
    )


def _container_running(name: str) -> bool:
    result = _run(["docker", "inspect", "-f", "{{.State.Running}}", name], timeout=30)
    return result.returncode == 0 and result.stdout.strip() == "true"


def wait_for_railsserver(*, attempts: int = 60, delay: int = 5) -> bool:
    """Wait until Zammad is genuinely ready for setup. First boot is slow, and readiness has
    **two** stages that are easy to confuse:

    1. the schema exists (`db:migrate` has run), and
    2. the seed data exists (`db:seed` has run) — which creates Zammad's system user, id 1.

    Checking only the first is what made this race the second time. On a cold database the
    tables appear before seeding finishes, the bootstrap starts, finds no system user, and
    aborts — and because the traceback is long, the error looked like a Ruby bug rather than a
    timing problem. Requiring the system user too means the bootstrap never starts against a
    database that is only half built.
    """
    probe = (
        "print(ActiveRecord::Base.connection.table_exists?('users') && User.exists?(1))"
    )
    for attempt in range(1, attempts + 1):
        if not _container_running(RAILSSERVER):
            print(f"  waiting for {RAILSSERVER} to start ({attempt}/{attempts})")
            time.sleep(delay)
            continue
        result = _run(
            [
                "docker", "exec", RAILSSERVER,
                "/opt/zammad/contrib/docker/docker-entrypoint.sh",
                "bundle", "exec", "rails", "runner", probe,
            ],
            timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip().endswith("true"):
            return True
        print(f"  waiting for Zammad's schema and seed data ({attempt}/{attempts})")
        time.sleep(delay)
    return False


def run_bootstrap() -> dict[str, str]:
    """Run the setup script inside the railsserver and return the credentials it printed."""
    if not BOOTSTRAP.exists():
        raise SystemExit(f"missing {BOOTSTRAP}")

    copied = _run(["docker", "cp", str(BOOTSTRAP), f"{RAILSSERVER}:/tmp/zammad_bootstrap.rb"])
    if copied.returncode != 0:
        raise SystemExit(f"could not copy the bootstrap into the container: {copied.stderr[:300]}")

    result = _run(
        [
            "docker", "exec", RAILSSERVER,
            "/opt/zammad/contrib/docker/docker-entrypoint.sh",
            "bundle", "exec", "rails", "runner", "/tmp/zammad_bootstrap.rb",
        ],
        timeout=300,
    )
    output = f"{result.stdout}\n{result.stderr}"

    match = re.search(r"^ITSM_JSON=(\{.*\})$", output, flags=re.MULTILINE)
    if not match:
        # Report the *error message*, not 30 lines of Ruby backtrace.
        #
        # The first version printed the tail of the output, which is always the last frame of
        # the traceback -- `from bin/rails:4:in '<main>'` -- and never the sentence that says
        # what went wrong. The actual message is the first line that is not logging or a stack
        # frame, so look for that instead.
        noise = ("INFO --", "ActionCable", "file store", "Redis back", "WARN --")
        candidates = [
            line.strip() for line in output.splitlines()
            if line.strip()
            and not any(n in line for n in noise)
            and not line.strip().startswith(("from ", "\tfrom ", "/usr/", "/opt/"))
            and not re.match(r"^\s+from ", line)
        ]
        reason = ""
        for line in candidates:
            if any(marker in line for marker in ("Error", "error", "raise", "violates", "does not exist")):
                reason = line
                break
        if not reason and candidates:
            reason = candidates[0]

        raise SystemExit(
            "Zammad setup failed"
            + (f": {reason}" if reason else "")
            + "\n\nFull output is in the container log. If this is a fresh database it is most"
            "\nlikely still seeding -- run the setup again, it is idempotent."
        )

    for line in output.splitlines():
        if line.strip() and "INFO --" not in line and "ActionCable" not in line:
            print("  " + line.strip())
    return json.loads(match.group(1))


def write_token(token: str) -> bool:
    """Put the token into the plugin's config, so the repository is the source of truth."""
    if not PLUGINS_CONFIG.exists():
        return False
    text = PLUGINS_CONFIG.read_text(encoding="utf-8")
    updated = re.sub(r"('itsm_token':\s*')[^']*(')", rf"\g<1>{token}\g<2>", text)
    if updated == text:
        return False
    PLUGINS_CONFIG.write_text(updated, encoding="utf-8")
    return True


def wait_for_nginx(*, attempts: int = 60, delay: int = 3) -> bool:
    """Wait until Zammad's web front end answers.

    The railsserver being ready does not mean nginx is: nginx starts last and its entrypoint
    probes the railsserver first. Verifying before that produced a `Connection refused` to
    `zammad-nginx:8080` — which looks exactly like the two-stacks-on-different-networks failure,
    so the message sent me the wrong way on a setup that had actually worked.
    """
    for attempt in range(1, attempts + 1):
        result = _run(
            ["docker", "exec", "workshop_arista_automation-netbox-1",
             "curl", "-sf", "-o", "/dev/null", "http://zammad-nginx:8080/"],
            timeout=30,
        )
        if result.returncode == 0:
            return True
        if attempt % 5 == 0:
            print(f"  waiting for Zammad's web front end ({attempt}/{attempts})")
        time.sleep(delay)
    return False


def verify(token: str, public_url: str) -> int:
    """Prove it works the way the plugin uses it: from inside the NetBox container."""
    if not wait_for_nginx():
        print("\nZammad's web front end never answered on zammad-nginx:8080.")
        print("It is still starting, or the stacks are on different networks. Check:")
        print("  docker compose -f docker-compose.yml -f docker-compose.itsm.yml --profile itsm ps")
        return 1

    script = (
        "import requests\n"
        f"h={{'Authorization':'Token token={token}','Accept':'application/json'}}\n"
        "me=requests.get('http://zammad-nginx:8080/api/v1/users/me',headers=h,timeout=15)\n"
        "print('me', me.status_code, me.json().get('login') if me.status_code==200 else me.text[:80])\n"
        "ts=requests.get('http://zammad-nginx:8080/api/v1/ticket_states',headers=h,timeout=15)\n"
        "print('states', ts.status_code)\n"
        "gs=requests.get('http://zammad-nginx:8080/api/v1/getting_started',timeout=15)\n"
        "print('setup_done', gs.json().get('setup_done') if gs.status_code==200 else gs.status_code)\n"
    )
    result = _run(
        ["docker", "exec", "workshop_arista_automation-netbox-1",
         "/opt/netbox/venv/bin/python", "-c", script],
        timeout=60,
    )
    output = f"{result.stdout}\n{result.stderr}"
    print(output.strip())

    if "me 200" not in output:
        print("\nThe token did not work from inside NetBox. Zammad's front end answers, so the")
        print("likely cause is a stale token rather than a network problem: check that")
        print("docker/netbox/configuration/plugins.py carries the value above.")
        return 1
    if "setup_done True" not in output:
        print("\nZammad's setup wizard is still active; the UI will not be usable.")
        return 1

    print(f"\nITSM ready. Log in at {public_url} as admin@example.com / admin")
    print("Tickets tab: NetBox -> Network Ops -> ITSM tickets")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--verify", action="store_true", help="only check, change nothing")
    parser.add_argument("--no-restart", action="store_true",
                        help="do not restart NetBox after writing the token")
    args = parser.parse_args(argv)

    if args.verify:
        # Read the token the way the plugin reads it -- through Django. A plain `python -c
        # 'from netbox_ops import conf'` returns empty strings, because `conf` resolves settings
        # lazily and there are no settings without `manage.py`. That silently made `--verify`
        # report "could not read token" on a perfectly configured install.
        from_here = _run(
            ["docker", "exec", "workshop_arista_automation-netbox-1",
             "/opt/netbox/netbox/manage.py", "shell", "-c",
             "from netbox_ops import conf; print('TOKEN=' + conf.itsm_token())"],
            timeout=120,
        )
        lines = [ln for ln in from_here.stdout.splitlines() if ln.startswith("TOKEN=")]
        token = lines[-1].split("=", 1)[1].strip() if lines else ""
        if not token:
            print("Could not read itsm_token from the plugin's config.")
            print(f"  exit {from_here.returncode}")
            print((from_here.stderr or from_here.stdout)[-400:])
            return 1
        return verify(token, "http://localhost:8090")

    print("Waiting for Zammad's railsserver...")
    if not wait_for_railsserver():
        print("\nZammad did not become ready. Check:")
        print("  docker compose -f docker-compose.yml -f docker-compose.itsm.yml --profile itsm ps")
        print(f"  docker logs {RAILSSERVER} --tail 50")
        return 1

    print("\nConfiguring Zammad...")
    credentials = run_bootstrap()

    token = credentials.get("token", "")
    public_url = credentials.get("url", "http://localhost:8090")

    if write_token(token):
        print("\nWrote the token into docker/netbox/configuration/plugins.py")
        if not args.no_restart:
            print("Restarting NetBox so it picks the token up...")
            _run(["docker", "compose", "restart", "netbox"], timeout=300)
            # NetBox's start_period is long; give the app time to answer before verifying.
            for _ in range(30):
                probe = _run(
                    ["docker", "exec", "workshop_arista_automation-netbox-1",
                     "curl", "-sf", "-o", "/dev/null", "http://localhost:8080/login/"],
                    timeout=30,
                )
                if probe.returncode == 0:
                    break
                time.sleep(3)
    else:
        print("\nThe plugin's config already carries this token.")

    return verify(token, public_url)


if __name__ == "__main__":
    sys.exit(main())
