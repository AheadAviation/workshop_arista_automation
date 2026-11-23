"""Device reader and EOS parser tests (INIT-001/SPEC-002).

Both halves matter here:
  * the parsers are pinned to captured-shape fixtures, so a format change is caught;
  * the reader's failure path is pinned to returning an unreachable `Actual` rather
    than raising, because that is what stops the page reporting green on a switch it
    could not read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.contracts import Actual
from workbench.device_reader import (
    DeviceReadError,
    parse_asn,
    parse_hostname,
    parse_os_version,
    parse_trunk_ports,
    parse_trunks,
    parse_vlan_list,
    parse_vlans,
    read_device,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "eos"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# ac-5 — pinned to real EOS output shapes
# --------------------------------------------------------------------------- #


def test_parse_vlans_from_real_output():
    vlans = parse_vlans(fixture("show_vlan.txt"))
    assert 100 in vlans
    assert 4094 in vlans
    assert len(vlans) == len(set(vlans)), "no duplicates"


def test_parse_vlans_ignores_header_and_dashes():
    vlans = parse_vlans(fixture("show_vlan.txt"))
    assert 1 in vlans            # the default VLAN really is present
    assert all(1 <= v <= 4094 for v in vlans)


def test_parse_vlans_on_empty_output_is_empty():
    assert parse_vlans("") == ()


def test_parse_trunks_reads_only_the_allowed_list():
    """The 'allowed on trunk' block is the one that matters; the others are narrower."""
    trunks = parse_trunks(fixture("show_interfaces_trunk.txt"))
    assert trunks["Et5"] == (100, 110, 120, 130, 140)
    assert trunks["Po2000"] == (4094,)


def test_parse_trunks_handles_the_ceos_header_wording():
    """Real cEOS prints `Vlans allowed`, not `Vlans allowed on trunk`.

    Captured from the live lab. The earlier parser only matched the longer wording, so a
    correctly configured trunk read back as carrying nothing -- the exact symptom that made
    the live check fail after the change had in fact succeeded.
    """
    trunks = parse_trunks(fixture("show_interfaces_trunk_ceos.txt"))
    assert trunks["Et5"] == (100, 110, 120, 130, 140)


def test_parse_trunks_ignores_the_inactive_and_forwarding_blocks_ceos_style():
    output = """
Port            Vlans allowed
Et5             100,110,150

Port            Vlans allowed and active in management domain
Et5             None

Port            Vlans in spanning tree forwarding state
Et5             None
"""
    assert parse_trunks(output)["Et5"] == (100, 110, 150)


def test_parse_trunks_ignores_the_narrower_later_blocks():
    """A VLAN that is allowed but not yet active must still count as on the trunk."""
    output = """
Port        Vlans allowed on trunk
Et5         100,110,150

Port        Vlans allowed and active in management domain
Et5         100,110

Port        Vlans in spanning tree forwarding state and not pruned
Et5         100
"""
    assert parse_trunks(output)["Et5"] == (100, 110, 150)


def test_parse_trunks_handles_explicit_port_channel_names():
    output = """
Port        Vlans allowed on trunk
Ethernet5   100,110,150
Port-Channel2000   4094
"""
    trunks = parse_trunks(output)
    assert trunks["Ethernet5"] == (100, 110, 150)
    assert trunks["Port-Channel2000"] == (4094,)


def test_parse_trunks_with_no_allowed_block_is_empty():
    assert parse_trunks("Port  Mode  Status\n") == {}


def test_parse_trunk_ports():
    ports = parse_trunk_ports(fixture("show_interfaces_trunk.txt"))
    assert "Et5" in ports and "Et6" in ports and "Po2000" in ports


def test_parse_vlan_list_expands_ranges():
    assert parse_vlan_list("100,110,120-122") == (100, 110, 120, 121, 122)


def test_parse_vlan_list_ignores_junk():
    assert parse_vlan_list("100, foo, 9999, 110") == (100, 110)


def test_parse_vlan_list_handles_reversed_range():
    assert parse_vlan_list("140-120") == ()


def test_parse_hostname():
    assert parse_hostname("leaf-01\n") == "leaf-01"


def test_parse_hostname_with_labelled_form():
    assert parse_hostname("Hostname: spine-01\n") == "spine-01"


def test_parse_hostname_on_empty_output():
    assert parse_hostname("") == ""


def test_parse_asn_from_running_config():
    assert parse_asn(fixture("show_running_config_bgp.txt")) == 65001


def test_parse_asn_when_no_bgp_is_configured():
    assert parse_asn("! no bgp here\n") == 0


def test_parse_os_version_from_real_output():
    assert parse_os_version(fixture("show_version.txt")) == "4.32.0F"


def test_parse_os_version_absent():
    assert parse_os_version("nothing useful\n") == ""


# --------------------------------------------------------------------------- #
# the reader: success path
# --------------------------------------------------------------------------- #


def fake_runner(outputs: dict[str, str], code: int = 0, stderr: str = ""):
    """A runner that answers by command substring, the way the CLI would.

    The fallback returns the given `code`/`stderr` rather than a generic failure, so a
    caller can simulate "the container is gone" or "docker is missing" and have the
    reader's real error path exercised.
    """

    def run(args):
        command = args[-1]
        for key, out in outputs.items():
            if key in command:
                return out, stderr, code
        return "", stderr, code

    return run


def test_read_device_assembles_actual_state():
    run = fake_runner(
        {
            "show hostname": "leaf-01\n",
            "show vlan": fixture("show_vlan.txt"),
            "show interfaces trunk": fixture("show_interfaces_trunk.txt"),
            "show version": fixture("show_version.txt"),
            "router bgp": fixture("show_running_config_bgp.txt"),
        }
    )
    actual = read_device("leaf-01", runner=run)
    assert isinstance(actual, Actual)
    assert actual.reachable is True
    assert actual.hostname == "leaf-01"
    assert actual.asn == 65001
    assert 100 in actual.vlans
    assert actual.trunk_allowed["Et5"] == (100, 110, 120, 130, 140)
    assert actual.os_version == "4.32.0F"


# --------------------------------------------------------------------------- #
# ac-2 — an unreachable device is a value, not an exception
# --------------------------------------------------------------------------- #


def test_unreachable_device_returns_rather_than_raises():
    run = fake_runner({}, code=1, stderr="Error: No such container: clab-x-leaf-01")
    actual = read_device("leaf-01", runner=run)
    assert actual.reachable is False
    assert "not running" in actual.error or "No such container" in actual.error


def test_missing_docker_is_reported_plainly():
    run = fake_runner({}, code=127, stderr="")
    actual = read_device("leaf-01", runner=run)
    assert actual.reachable is False
    assert "docker is not available" in actual.error


def test_timeout_is_reported_plainly():
    run = fake_runner({}, code=124, stderr="")
    actual = read_device("leaf-01", runner=run)
    assert actual.reachable is False
    assert "did not answer" in actual.error


def test_eos_percent_error_on_stdout_counts_as_unreachable():
    """EOS prints its complaints to stdout with a zero exit code."""

    def run(args):
        return "% Invalid input (at token 0: 'show')\n", "", 0

    actual = read_device("leaf-01", runner=run)
    assert actual.reachable is False


# --------------------------------------------------------------------------- #
# ac-7 / input validation — nothing unvalidated reaches a command
# --------------------------------------------------------------------------- #


def test_invalid_device_name_is_refused_before_any_command():
    called = []

    def run(args):
        called.append(args)
        return "", "", 0

    with pytest.raises(DeviceReadError, match="invalid device name"):
        read_device("leaf-01; rm -rf /", runner=run)
    assert called == [], "no command may run for an invalid device name"


def test_device_name_with_path_separator_is_refused():
    with pytest.raises(DeviceReadError):
        read_device("../../etc/passwd", runner=lambda a: ("", "", 0))


def test_reader_passes_commands_as_an_argument_list_never_a_shell():
    seen: list[list[str]] = []

    def run(args):
        seen.append(list(args))
        return "leaf-01\n", "", 0

    read_device("leaf-01", runner=run)
    assert all(isinstance(a, list) for a in seen)
    assert seen[0][0] == "docker", "invoked via docker exec, not a shell string"


# --------------------------------------------------------------------------- #
# ac-6 — reading twice is stable
# --------------------------------------------------------------------------- #


def test_reading_the_same_device_twice_gives_the_same_value():
    outputs = {
        "show hostname": "leaf-01\n",
        "show vlan": fixture("show_vlan.txt"),
        "show interfaces trunk": fixture("show_interfaces_trunk.txt"),
        "show version": fixture("show_version.txt"),
        "router bgp": fixture("show_running_config_bgp.txt"),
    }
    a = read_device("leaf-01", runner=fake_runner(outputs))
    b = read_device("leaf-01", runner=fake_runner(outputs))
    assert a.to_dict() == b.to_dict()
