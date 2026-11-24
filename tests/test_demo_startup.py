"""The demo's start-up surface: `run.ps1` and the compose definitions (INIT-002).

These are not unit tests of logic. They pin three things that failed in practice, each of
which is silent — the demo looks fine until you are in front of a customer:

1. **The ITSM stack must be started with both compose files.** Zammad is defined in
   `docker-compose.itsm.yml`, but starting that file *alone* puts it on a different Docker
   network from NetBox, so the plugin's `http://zammad-nginx:8080` cannot resolve. The
   Tickets tab then says "ITSM is not configured" while Zammad is plainly running. The only
   thing that makes this work is passing `docker-compose.yml` first.
2. **`down -v` must never be offered for the ITSM stack.** On this project it removes
   NetBox's volumes too, which wipes the source of truth.
3. **Every command the presenter guide tells you to run must exist in `run.ps1`.** A guide
   that says `.\run.ps1 eapi` when no such command exists is worse than no guide: it fails
   in the presenter's hands, minutes before a briefing.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

RUN_PS1 = REPO_ROOT / "run.ps1"
ITSM_COMPOSE = REPO_ROOT / "docker-compose.itsm.yml"
MAIN_COMPOSE = REPO_ROOT / "docker-compose.yml"
GUIDE = REPO_ROOT / "workbench" / "static" / "guide.html"


def _run_commands() -> set[str]:
    """The command names `run.ps1` dispatches on, read from its own switch statement."""
    text = RUN_PS1.read_text(encoding="utf-8")
    return set(re.findall(r'^\s{4}"([a-z0-9-]+)"\s*\{', text, flags=re.MULTILINE))


def _command_block(name: str, *, until: str | None = None) -> str:
    """The executable lines of one `run.ps1` command, with comments removed.

    Comments are stripped deliberately. Several of them *quote* the wrong command in order
    to warn against it, so a naive substring search over the block finds the warning and
    reports it as the violation it is warning about.
    """
    text = RUN_PS1.read_text(encoding="utf-8")
    block = text.split(f'"{name}" {{', 1)[1]
    if until:
        block = block.split(f'"{until}"', 1)[0]
    else:
        block = block.split("\n    }", 1)[0]
    return "\n".join(
        line for line in block.splitlines() if not line.strip().startswith("#")
    )


# --------------------------------------------------------------------------- #
# run.ps1
# --------------------------------------------------------------------------- #


def test_the_itsm_command_passes_both_compose_files():
    """The network matters more than the ordering reads here: see the module docstring."""
    block = _command_block("itsm", until="itsm-down")

    assert "docker-compose.yml" in block, "the main compose file must come first"
    assert "docker-compose.itsm.yml" in block
    # The main file first, so its named network wins.
    assert block.index("docker-compose.yml") < block.index("docker-compose.itsm.yml")
    assert "--profile itsm" in block


def test_the_itsm_down_command_never_removes_volumes():
    """`down -v` here would delete NetBox's data as well, not just Zammad's."""
    block = _command_block("itsm-down")

    assert "down" not in block, "itsm-down must stop, not down: down would take NetBox too"
    assert " -v" not in block, "no volume removal in itsm-down"


def test_eapi_command_exists_and_calls_the_host_script():
    """eAPI cannot be enabled from inside NetBox; it needs the switch CLI via docker exec."""
    assert "eapi" in _run_commands()
    block = _command_block("eapi", until="itsm")
    assert "prepare_eapi.py" in block


# --------------------------------------------------------------------------- #
# compose
# --------------------------------------------------------------------------- #


def test_the_itsm_compose_file_does_not_declare_its_own_network():
    """It must inherit the main file's `netbox-arista-lab`, which is where NetBox lives.

    Declaring a network here would put Zammad somewhere the plugin cannot reach, which is
    the failure this whole file exists to prevent.
    """
    text = ITSM_COMPOSE.read_text(encoding="utf-8")
    top_level = text.split("\nvolumes:", 1)[0]
    assert not re.search(r"^networks:", top_level, flags=re.MULTILINE), (
        "docker-compose.itsm.yml must not declare networks; it inherits the main file's"
    )


def test_the_main_compose_file_names_its_network():
    """The name is what makes the two files line up, so it is load-bearing."""
    text = MAIN_COMPOSE.read_text(encoding="utf-8")
    assert "networks:" in text
    assert re.search(r"^\s+name:\s*netbox-arista-lab", text, flags=re.MULTILINE), (
        "the default network must be explicitly named netbox-arista-lab"
    )


def test_the_itsm_compose_header_documents_both_files():
    """The comment is the only thing someone reads before running the command by hand."""
    text = ITSM_COMPOSE.read_text(encoding="utf-8")
    header = "\n".join(text.splitlines()[:22])
    assert "docker-compose.itsm.yml" in header
    assert "docker-compose.yml" in header
    assert "profile itsm" in header


# --------------------------------------------------------------------------- #
# the guide, against run.ps1
# --------------------------------------------------------------------------- #


def test_every_run_command_the_guide_tells_you_to_run_exists():
    """The guide is followed under time pressure. A typo'd command is a wasted minute of
    the presenter's confidence, and possibly the only rehearsal they get."""
    guide = GUIDE.read_text(encoding="utf-8")
    referenced = set(re.findall(r"run\.ps1 ([a-z0-9-]+)", guide))
    assert referenced, "the guide should reference run.ps1 commands"

    available = _run_commands()
    missing = sorted(referenced - available)
    assert missing == [], f"the guide references commands run.ps1 does not define: {missing}"


def test_the_guide_does_not_document_a_bare_profile_itsm_invocation():
    """That command starts nothing, because the services are in the other file."""
    guide = GUIDE.read_text(encoding="utf-8")
    bare = re.findall(r"docker compose --profile itsm up", guide)
    assert bare == [], (
        "the guide must not show `docker compose --profile itsm up -d` on its own; "
        "it starts no ITSM services at all"
    )
