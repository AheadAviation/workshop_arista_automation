"""Shared fixtures. The add-on's scripts are not an installed package, so add them to the path."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from plants_demo import meraki_mock  # noqa: E402


@pytest.fixture(scope="session")
def plan() -> dict:
    with open(ROOT / "netbox_data" / "plants_demo.yml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="session")
def world(plan: dict) -> dict:
    return meraki_mock.build_world(plan)


@pytest.fixture
def server(world: dict) -> Iterator[meraki_mock.MockMeraki]:
    """A running mock that serves 10 items per page, so every collection spans pages."""
    running = meraki_mock.start(world, page_cap=10)
    yield running
    meraki_mock.stop(running)
