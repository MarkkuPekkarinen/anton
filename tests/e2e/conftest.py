"""
Pytest configuration for Anton E2E tests.

Adds:
  --live        run against real LLM provider instead of stub server
  cfg           session-scoped E2EConfig fixture
  stub          function-scoped provider fixture (StubServer or LiveProvider)
"""

from __future__ import annotations

from typing import Generator

import pytest

from tests.e2e.harness import E2EConfig, LiveProvider
from tests.e2e.stub_server import StubServer


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run E2E tests against real LLM provider instead of the stub server.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "stub_only: test requires stub server (automatically skipped when --live is passed)",
    )


@pytest.fixture(scope="session")
def cfg(request: pytest.FixtureRequest) -> E2EConfig:
    """Session-wide E2E configuration (stub vs. live mode)."""
    return E2EConfig(live=request.config.getoption("--live"))


@pytest.fixture
def stub(cfg: E2EConfig) -> Generator[StubServer | LiveProvider, None, None]:
    """Fresh provider for each test — StubServer in stub mode, LiveProvider in live mode."""
    with cfg.make_provider() as p:
        yield p


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip stub_only tests when running in live mode."""
    if not config.getoption("--live"):
        return
    skip = pytest.mark.skip(
        reason="stub-only: requires request inspection or scripted LLM behaviour"
    )
    for item in items:
        if item.get_closest_marker("stub_only"):
            item.add_marker(skip)
