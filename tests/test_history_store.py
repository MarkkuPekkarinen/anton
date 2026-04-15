"""Tests for anton.memory.history_store.HistoryStore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anton.memory.history_store import HistoryStore


@pytest.fixture
def store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(tmp_path)


@pytest.fixture
def episodes_dir(tmp_path: Path) -> Path:
    return tmp_path


def test_save_load_roundtrip(store: HistoryStore, episodes_dir: Path) -> None:
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    store.save("20260301_120000", history)
    loaded = store.load("20260301_120000")
    assert loaded == history


def test_save_overwrites(store: HistoryStore) -> None:
    store.save("20260301_120000", [{"role": "user", "content": "v1"}])
    store.save("20260301_120000", [{"role": "user", "content": "v2"}])
    loaded = store.load("20260301_120000")
    assert loaded == [{"role": "user", "content": "v2"}]


def test_load_missing_returns_none(store: HistoryStore) -> None:
    assert store.load("nonexistent") is None


def test_load_corrupt_json_returns_none(store: HistoryStore, episodes_dir: Path) -> None:
    path = episodes_dir / "20260301_120000_history.json"
    path.write_text("not valid json {{{", encoding="utf-8")
    assert store.load("20260301_120000") is None


def test_load_non_list_returns_none(store: HistoryStore, episodes_dir: Path) -> None:
    path = episodes_dir / "20260301_120000_history.json"
    path.write_text('{"not": "a list"}', encoding="utf-8")
    assert store.load("20260301_120000") is None


def test_list_sessions_newest_first(store: HistoryStore) -> None:
    store.save("20260301_100000", [
        {"role": "user", "content": "first session"},
        {"role": "assistant", "content": "reply"},
    ])
    store.save("20260302_100000", [
        {"role": "user", "content": "second session"},
        {"role": "assistant", "content": "reply"},
    ])

    sessions = store.list_sessions()
    assert len(sessions) == 2
    assert sessions[0]["session_id"] == "20260302_100000"
    assert sessions[1]["session_id"] == "20260301_100000"


def test_list_sessions_preview_extraction(store: HistoryStore) -> None:
    store.save("20260301_100000", [
        {"role": "user", "content": "What is the meaning of life?"},
        {"role": "assistant", "content": "42"},
    ])

    sessions = store.list_sessions()
    assert sessions[0]["preview"] == "What is the meaning of life?"


def test_list_sessions_preview_truncated(store: HistoryStore) -> None:
    long_msg = "x" * 100
    store.save("20260301_100000", [
        {"role": "user", "content": long_msg},
        {"role": "assistant", "content": "ok"},
    ])

    sessions = store.list_sessions()
    assert len(sessions[0]["preview"]) == 60  # 57 + "..."
    assert sessions[0]["preview"].endswith("...")


def test_list_sessions_skips_empty(store: HistoryStore, episodes_dir: Path) -> None:
    # Empty list
    store.save("20260301_100000", [])
    # No user turns
    store.save("20260301_110000", [{"role": "assistant", "content": "monologue"}])

    sessions = store.list_sessions()
    assert len(sessions) == 0


def test_list_sessions_skips_corrupt(store: HistoryStore, episodes_dir: Path) -> None:
    # Valid session
    store.save("20260301_100000", [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ])
    # Corrupt file
    corrupt = episodes_dir / "20260301_110000_history.json"
    corrupt.write_text("broken!", encoding="utf-8")

    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "20260301_100000"


def test_list_sessions_limit(store: HistoryStore) -> None:
    for i in range(5):
        store.save(f"20260301_10000{i}", [
            {"role": "user", "content": f"msg {i}"},
            {"role": "assistant", "content": "ok"},
        ])

    sessions = store.list_sessions(limit=3)
    assert len(sessions) == 3


def test_list_sessions_turn_count(store: HistoryStore) -> None:
    store.save("20260301_100000", [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ])

    sessions = store.list_sessions()
    assert sessions[0]["turns"] == 2


def test_list_sessions_date_format(store: HistoryStore) -> None:
    store.save("20260301_143052", [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ])

    sessions = store.list_sessions()
    assert sessions[0]["date"] == "2026-03-01 14:30"


def test_list_sessions_multimodal_preview(store: HistoryStore) -> None:
    store.save("20260301_100000", [
        {"role": "user", "content": [
            {"type": "text", "text": "describe this image"},
            {"type": "image", "source": {"data": "..."}},
        ]},
        {"role": "assistant", "content": "I see a cat"},
    ])

    sessions = store.list_sessions()
    assert sessions[0]["preview"] == "describe this image"


def test_list_sessions_no_dir(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "nonexistent")
    assert store.list_sessions() == []


def test_save_never_raises(tmp_path: Path) -> None:
    # Read-only directory — save should not raise
    store = HistoryStore(tmp_path / "no" / "such" / "deep" / "path")
    # This would fail if save tried to create dirs and something went wrong,
    # but it should silently succeed or fail without raising
    store.save("20260301_100000", [{"role": "user", "content": "test"}])


class TestWorkspaceIsolation:
    def test_sessions_isolated_across_workspaces(self, tmp_path: Path) -> None:
        """Sessions saved in workspace A must not appear in workspace B's history."""
        store_a = HistoryStore(tmp_path / "project_a" / ".anton" / "episodes")
        store_b = HistoryStore(tmp_path / "project_b" / ".anton" / "episodes")

        store_a.save("20260330_120000", [{"role": "user", "content": "project A session"}])

        assert store_b.list_sessions() == []
        sessions_a = store_a.list_sessions()
        assert len(sessions_a) == 1
        assert sessions_a[0]["preview"] == "project A session"
