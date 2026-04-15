from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from anton.utils.datasources import (
    _DS_KNOWN_VARS,
    _DS_SECRET_VARS,
    scrub_credentials,
)


@pytest.fixture(autouse=True)
def clean_ds_state():
    """Clear _DS_SECRET_VARS, _DS_KNOWN_VARS, and all DS_* env vars around each test."""
    def _clean():
        _DS_SECRET_VARS.clear()
        _DS_KNOWN_VARS.clear()
        for k in list(os.environ):
            if k.startswith("DS_"):
                del os.environ[k]

    _clean()
    yield
    _clean()


class TestScrubCredentials:
    """Focused regression tests for _scrub_credentials short-secret handling."""

    def test_registered_6char_secret_scrubbed(self, monkeypatch):
        """A 6-character registered secret is scrubbed regardless of length."""
        _DS_SECRET_VARS.add("DS_PASSWORD")
        monkeypatch.setenv("DS_PASSWORD", "abc123")
        result = scrub_credentials("auth failed: abc123")
        assert "abc123" not in result
        assert "[DS_PASSWORD]" in result

    def test_registered_8char_secret_scrubbed(self, monkeypatch):
        """An 8-character registered secret is scrubbed (was at the old threshold)."""
        _DS_SECRET_VARS.add("DS_API_KEY")
        monkeypatch.setenv("DS_API_KEY", "tok12345")
        result = scrub_credentials("token=tok12345 rejected")
        assert "tok12345" not in result
        assert "[DS_API_KEY]" in result

    def test_registered_1char_secret_scrubbed(self, monkeypatch):
        """A 1-character registered secret is scrubbed."""
        _DS_SECRET_VARS.add("DS_SECRET")
        monkeypatch.setenv("DS_SECRET", "x")
        result = scrub_credentials("value=x here")
        assert "=x " not in result
        assert "[DS_SECRET]" in result

    def test_non_secret_var_not_scrubbed(self, monkeypatch):
        """A known but non-secret DS_* var (e.g. DS_HOST) stays readable."""
        _DS_KNOWN_VARS.add("DS_HOST")
        monkeypatch.setenv("DS_HOST", "mydbhostname")
        result = scrub_credentials("host=mydbhostname")
        assert "mydbhostname" in result

    def test_unknown_short_ds_var_not_scrubbed(self, monkeypatch):
        """Unknown DS_* vars with short values are NOT scrubbed (heuristic threshold)."""
        monkeypatch.setenv("DS_ENABLE_FEATURE", "on")
        result = scrub_credentials("flag=on active")
        assert "on" in result
