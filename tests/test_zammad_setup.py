"""The Zammad setup path (INIT-002/SPEC-006).

Nothing here talks to Docker. These pin the four things that went wrong while building the
setup, each of which failed *silently* or far from its cause:

1. **`AUTOWIZARD_JSON` must be base64.** Plain JSON is skipped without an error, so the stack
   comes up healthy and the wizard still shows. Anyone reaching for that variable needs to know.
2. **The setup must not race the migration.** It was a one-shot compose service and could start
   before the schema existed.
3. **Group access, not just roles.** An admin with `Admin` + `Agent` roles and no group access
   gets a 403 on ticket creation while every read works — which reads like a bad token.
4. **`fqdn` is a host, not a URL.** Passing `http://localhost:8090` becomes
   `http://http://localhost:8090` internally and breaks login.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BOOTSTRAP = REPO_ROOT / "scripts" / "zammad_bootstrap.rb"
RUNNER = REPO_ROOT / "scripts" / "zammad_setup.py"
ITSM_COMPOSE = REPO_ROOT / "docker-compose.itsm.yml"
RUN_PS1 = REPO_ROOT / "run.ps1"
GUIDE = REPO_ROOT / "workbench" / "static" / "guide.html"


def _code_lines(path: Path) -> str:
    """The file without comment lines, so an assertion cannot match its own warning."""
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith(("#", "//"))
    )


# --------------------------------------------------------------------------- #
# the race
# --------------------------------------------------------------------------- #


def test_the_setup_is_not_a_compose_service():
    """A one-shot service races the migration and fails on a cold database."""
    text = ITSM_COMPOSE.read_text(encoding="utf-8")
    services = re.findall(r"^  (zammad-[a-z]+):", text, flags=re.MULTILINE)
    assert "zammad-setup" not in services, (
        "setup must not be a compose service: it races zammad-init on a cold database"
    )


def test_the_runner_waits_for_the_schema_rather_than_sleeping_once():
    """It must poll. A fixed sleep is the same race with a longer fuse."""
    code = _code_lines(RUNNER)
    assert "def wait_for_railsserver" in code
    assert "table_exists" in code, "readiness must ask the database, not just the container"
    assert "attempts" in code, "the wait must be bounded and retried"


def test_the_readiness_check_requires_seed_data_not_just_tables():
    """This was the second race, and it is subtler than the first.

    `db:migrate` creates the `users` table well before `db:seed` creates Zammad's system user
    (id 1). A check that only asks "do the tables exist" passes, the setup starts, and it aborts
    on a missing system user — which looks like a Ruby bug rather than a timing problem.
    """
    code = _code_lines(RUNNER)
    assert "User.exists?(1)" in code, "readiness must require the seeded system user"
    assert "table_exists" in code

    # And the bootstrap must defend itself too, since it is documented as runnable by hand.
    bootstrap = _code_lines(BOOTSTRAP)
    assert "User.exists?(1)" in bootstrap


# --------------------------------------------------------------------------- #
# the four silent failures
# --------------------------------------------------------------------------- #


def test_the_compose_file_does_not_pass_plain_json_to_autowizard():
    """`AUTOWIZARD_JSON` is base64-decoded; plain JSON is silently discarded."""
    code = _code_lines(ITSM_COMPOSE)
    assert "AUTOWIZARD_JSON:" not in code or "base64" in code, (
        "AUTOWIZARD_JSON must be base64-encoded or omitted entirely"
    )


def test_the_bootstrap_sets_system_init_done():
    """Without it the browser keeps showing 'Set up a new system' forever."""
    assert "Setting.set('system_init_done', true)" in BOOTSTRAP.read_text(encoding="utf-8")


def test_the_bootstrap_grants_group_access_not_just_roles():
    """Roles alone authorize nothing for tickets; this is the 403 that looks like a bad token."""
    code = _code_lines(BOOTSTRAP)
    assert "group_names_access_map" in code, (
        "the admin needs group access or ticket creation returns 403 while reads succeed"
    )


def test_the_bootstrap_gives_the_token_its_permissions():
    """`preferences: {}` authorizes nothing, so writes 403 with a valid token."""
    code = _code_lines(BOOTSTRAP)
    assert "token.preferences[:permission]" in code


def test_the_bootstrap_does_not_attach_the_token_to_the_system_user():
    """User id 1 is Zammad's internal '-' account: tickets opened by it show no customer."""
    code = _code_lines(BOOTSTRAP)
    assert "User.find(1)" in code, "the system user is used as the *actor* during bootstrap"
    assert re.search(r"Token\.(create!|new)\([^)]*user:\s*admin", code), (
        "the token must be owned by the real admin, not User.first"
    )


def test_the_fqdn_setting_is_a_host_not_a_url():
    """`fqdn` and `http_type` are separate settings; conflating them breaks login."""
    code = _code_lines(BOOTSTRAP)
    assert "Setting.set('fqdn'" in code
    assert "partition('://')" in code, "the scheme must be split off before fqdn is set"

    # The old bug, spelled out: assigning the whole URL.
    assert "Setting.set('fqdn', ENV.fetch(" not in code, (
        "fqdn must not be assigned the raw ITSM_PUBLIC_URL; it is a host"
    )


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #


def test_run_ps1_itsm_runs_the_setup_after_starting_the_stack():
    text = RUN_PS1.read_text(encoding="utf-8")
    block = text.split('"itsm" {', 1)[1].split('"itsm-down"', 1)[0]
    code = "\n".join(
        line for line in block.splitlines() if not line.strip().startswith("#")
    )
    assert "zammad_setup.py" in code, "`run.ps1 itsm` must finish the setup, not just start it"
    assert code.index("docker-compose.itsm.yml") < code.index("zammad_setup.py"), (
        "the stack has to be started before its setup can run"
    )


def test_the_runner_writes_the_token_back_into_the_plugin_config():
    """A token that lives only in a container is lost on the next wipe."""
    code = _code_lines(RUNNER)
    assert "def write_token" in code
    assert "plugins.py" in code


def test_the_runner_verifies_from_inside_netbox():
    """Proving it from the host proves the wrong thing: only NetBox must resolve Zammad."""
    code = _code_lines(RUNNER)
    assert "netbox-1" in code
    assert "users/me" in code


def test_a_failure_reports_the_reason_not_the_traceback():
    """The first version printed the last line of the Ruby backtrace — always
    `from bin/rails:4:in '<main>'` — and never the sentence saying what went wrong, which sent
    a timing problem looking like a code bug."""
    code = _code_lines(RUNNER)
    assert "def run_bootstrap" in code
    # It must filter stack frames rather than take the tail of the output.
    assert "startswith((\"from \", " in code or "re.match" in code
    assert "reason" in code


def test_the_guide_documents_the_setup_step():
    page = GUIDE.read_text(encoding="utf-8")
    assert "run.ps1 itsm" in page
    assert "Set up a new system" in page, "the symptom a presenter would actually see"
    assert "403" in page, "the group-access failure deserves a line in the triage table"
