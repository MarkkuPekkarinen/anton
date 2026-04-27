"""Comprehensive export → import roundtrip tests for /share command.

Flow under test:
  1. Build a session with conversation history, memory entries, scratchpad cells.
  2. Export via handle_share_export → .anton file.
  3. Verify .anton file structure and content.
  4. Import via handle_share_import → new session.
  5. Compare state before and after, noting expected differences.

Expected differences after roundtrip:
  - session._session_id  : new ID (import always creates a fresh session)
  - system_prompt_context: new session has provenance suffix; original had none
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from anton.commands.share import handle_share_export, handle_share_import
from anton.core.backends.base import Cell
from anton.core.backends.manager import ScratchpadManager
from anton.core.memory.episodes import EpisodicMemory
from anton.core.session import ChatSession, ChatSessionConfig
from tests.conftest import make_mock_llm


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def console() -> Console:
    return Console(quiet=True)


@pytest.fixture()
def workspace(tmp_path: Path):
    return MagicMock(base=tmp_path)


# Canonical conversation used throughout the tests
HISTORY = [
    {"role": "user",      "content": "What is the revenue breakdown by region?"},
    {"role": "assistant", "content": "Let me query that for you."},
    {"role": "user",      "content": "Can you also show the YoY change?"},
    {"role": "assistant", "content": "Sure, here is the YoY comparison."},
]

SESSION_BORN_MEMORY  = {"content": "Always use CTEs for readability", "kind": "lesson", "topic": "sql"}
PROJECT_MEMORY       = {"content": "Never use SELECT * in production",  "kind": "never",  "topic": ""}
PROFILE_MEMORY       = {"content": "User prefers camel-case",              "kind": "profile", "topic": ""}
SCRATCHPAD_CELL      = Cell(code="df.head()", stdout="   col1\n0  1\n", stderr="", error=None, description="Preview data")


def _build_exporter_session(
    tmp_path: Path,
    workspace,
    *,
    include_profile_memory: bool = False,
) -> tuple[ChatSession, EpisodicMemory, str]:
    """Return (session, episodic, session_id) ready for export."""
    episodes_dir = tmp_path / "episodes"
    episodic = EpisodicMemory(episodes_dir)
    sid = episodic.start_session()

    # log memories that the export should pick up
    episodic.log_turn(0, "memory_write", **SESSION_BORN_MEMORY)
    episodic.log_turn(0, "memory_read",  **PROJECT_MEMORY)
    if include_profile_memory:
        episodic.log_turn(0, "memory_write", **PROFILE_MEMORY)

    mock_llm = make_mock_llm()
    session = ChatSession(ChatSessionConfig(
        llm_client=mock_llm,
        session_id=sid,
        episodic=episodic,
        workspace=workspace,
    ))
    session._history = list(HISTORY)
    session._turn_count = sum(1 for m in HISTORY if m.get("role") == "user")

    # wire a fake scratchpad runtime with one cell
    mock_runtime = MagicMock()
    mock_runtime.cells = [SCRATCHPAD_CELL]
    session._scratchpads._pads = {"main": mock_runtime}

    return session, episodic, sid


# ── export tests ──────────────────────────────────────────────────────────────


class TestShareExport:
    async def test_creates_anton_file_in_output_dir(self, tmp_path, console, workspace):
        session, episodic, _ = _build_exporter_session(tmp_path, workspace)
        mock_llm = make_mock_llm()

        with patch("anton.commands.share._generate_meta",
                   AsyncMock(return_value=("revenue-region-yoy", "Analyzed revenue by region. Found APAC leads YoY."))):
            await handle_share_export(console, session, workspace, mock_llm, episodic)

        output_dir = tmp_path / ".anton" / "output"
        files = list(output_dir.glob("*.anton"))
        assert len(files) == 1
        assert files[0].name.startswith("revenue-region-yoy_")

    async def test_file_version_and_metadata(self, tmp_path, console, workspace):
        session, episodic, _ = _build_exporter_session(tmp_path, workspace)
        mock_llm = make_mock_llm()

        with patch("anton.commands.share._generate_meta",
                   AsyncMock(return_value=("test-session", "Summary text."))):
            await handle_share_export(console, session, workspace, mock_llm, episodic)

        payload = json.loads(next((tmp_path / ".anton" / "output").glob("*.anton")).read_text())

        assert payload["version"] == "0.1"
        assert payload["exported_by"]  # non-empty username
        assert payload["exported_at"]  # ISO timestamp

    async def test_conversation_history_preserved(self, tmp_path, console, workspace):
        session, episodic, _ = _build_exporter_session(tmp_path, workspace)
        mock_llm = make_mock_llm()

        with patch("anton.commands.share._generate_meta",
                   AsyncMock(return_value=("test-session", ""))):
            await handle_share_export(console, session, workspace, mock_llm, episodic)

        payload = json.loads(next((tmp_path / ".anton" / "output").glob("*.anton")).read_text())
        assert payload["session"]["conversation_history"] == HISTORY

    async def test_session_born_memory_included(self, tmp_path, console, workspace):
        session, episodic, _ = _build_exporter_session(tmp_path, workspace)
        mock_llm = make_mock_llm()

        with patch("anton.commands.share._generate_meta",
                   AsyncMock(return_value=("test-session", ""))):
            await handle_share_export(console, session, workspace, mock_llm, episodic)

        payload = json.loads(next((tmp_path / ".anton" / "output").glob("*.anton")).read_text())
        born = payload["memory"]["session_born"]
        assert len(born) == 1
        assert born[0]["content"] == SESSION_BORN_MEMORY["content"]
        assert born[0]["kind"]    == SESSION_BORN_MEMORY["kind"]
        assert born[0]["topic"]   == SESSION_BORN_MEMORY["topic"]

    async def test_project_accessed_memory_included(self, tmp_path, console, workspace):
        session, episodic, _ = _build_exporter_session(tmp_path, workspace)
        mock_llm = make_mock_llm()

        with patch("anton.commands.share._generate_meta",
                   AsyncMock(return_value=("test-session", ""))):
            await handle_share_export(console, session, workspace, mock_llm, episodic)

        payload = json.loads(next((tmp_path / ".anton" / "output").glob("*.anton")).read_text())
        accessed = payload["memory"]["project_accessed"]
        assert len(accessed) == 1
        assert accessed[0]["content"] == PROJECT_MEMORY["content"]
        assert accessed[0]["kind"]    == PROJECT_MEMORY["kind"]

    async def test_scratchpad_cells_included(self, tmp_path, console, workspace):
        session, episodic, _ = _build_exporter_session(tmp_path, workspace)
        mock_llm = make_mock_llm()

        with patch("anton.commands.share._generate_meta",
                   AsyncMock(return_value=("test-session", ""))):
            await handle_share_export(console, session, workspace, mock_llm, episodic)

        payload = json.loads(next((tmp_path / ".anton" / "output").glob("*.anton")).read_text())
        cells = payload["scratchpad"]["cells"]
        assert len(cells) == 1
        assert cells[0]["pad"]         == "main"
        assert cells[0]["code"]        == SCRATCHPAD_CELL.code
        assert cells[0]["stdout"]      == SCRATCHPAD_CELL.stdout
        assert cells[0]["description"] == SCRATCHPAD_CELL.description

    async def test_summary_flag_empties_history(self, tmp_path, console, workspace):
        session, episodic, _ = _build_exporter_session(tmp_path, workspace)
        mock_llm = make_mock_llm()

        with patch("anton.commands.share._generate_meta",
                   AsyncMock(return_value=("test-session", ""))):
            await handle_share_export(console, session, workspace, mock_llm, episodic,
                                      summary_only=True)

        payload = json.loads(next((tmp_path / ".anton" / "output").glob("*.anton")).read_text())
        # --summary: history omitted, memories still present
        assert payload["session"]["conversation_history"] == []
        assert len(payload["memory"]["session_born"]) == 1


# ── import tests ──────────────────────────────────────────────────────────────


def _make_anton_payload(
    history: list[dict] | None = None,
    session_born: list[dict] | None = None,
    project_accessed: list[dict] | None = None,
    cells: list[dict] | None = None,
    version: str = "0.1",
) -> dict:
    return {
        "version": version,
        "exported_by": "alice",
        "exported_at": "2026-04-20T10:00:00+00:00",
        "session": {
            "id": "20260420_100000",
            "title": "Revenue Region Analysis",
            "summary": "Analyzed revenue by region. APAC leads YoY.",
            "conversation_history": history if history is not None else list(HISTORY),
        },
        "memory": {
            "session_born":    session_born    if session_born    is not None else [SESSION_BORN_MEMORY],
            "project_accessed": project_accessed if project_accessed is not None else [PROJECT_MEMORY],
        },
        "scratchpad": {
            "cells": cells if cells is not None else [
                {"pad": "main", "code": "df.head()", "stdout": "...", "stderr": "", "error": None, "description": "preview"}
            ],
        },
    }


def _write_anton_file(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "import_test.anton"
    path.write_text(json.dumps(payload, ensure_ascii=False))
    return path


async def _do_import(
    tmp_path: Path,
    console: Console,
    workspace,
    anton_file: Path,
    *,
    current_history: list[dict] | None = None,
    cortex=None,
) -> tuple[ChatSession, EpisodicMemory]:
    """Run handle_share_import and return (new_session, new_episodic).

    get_or_create is mocked so no real venv subprocess is started.
    Pad runtimes are injected into session._scratchpads._pads so callers
    can inspect restored cells via result._scratchpads._pads[name].cells.
    """
    mock_llm = make_mock_llm()

    # episodic for the new session (recipient side)
    new_episodic = EpisodicMemory(tmp_path / "new_episodes")

    # empty current session (no active history unless specified)
    current_session = ChatSession(ChatSessionConfig(
        llm_client=mock_llm,
        workspace=workspace,
    ))
    if current_history:
        current_session._history = list(current_history)

    # pre-build the session that rebuild_session will return.
    # Uses the session_id already set by handle_share_import's start_session() call.
    def _fake_rebuild(**kwargs):
        return ChatSession(ChatSessionConfig(
            llm_client=mock_llm,
            session_id=kwargs.get("session_id"),
            episodic=new_episodic,
            workspace=workspace,
        ))

    # Mock get_or_create so no real venv is started.
    # The mock sets _pads[name] so callers can inspect cells afterward.
    async def _mock_get_or_create(mgr_self, name):
        if name not in mgr_self._pads:
            rt = MagicMock()
            rt.cells = []
            mgr_self._pads[name] = rt
        return mgr_self._pads[name]

    with patch.object(ScratchpadManager, "get_or_create", _mock_get_or_create):
        with patch("anton.chat_session.rebuild_session", side_effect=_fake_rebuild):
            result = await handle_share_import(
                console,
                current_session,
                workspace,
                MagicMock(),              # settings
                {"llm_client": mock_llm}, # state
                None,                     # self_awareness
                cortex,
                new_episodic,
                None,                     # history_store
                filepath=str(anton_file),
            )

    return result, new_episodic


class TestShareImport:
    async def test_history_restored_in_llm_context(self, tmp_path, console, workspace):
        path = _write_anton_file(tmp_path, _make_anton_payload())
        result, _ = await _do_import(tmp_path, console, workspace, path)

        assert result._history == HISTORY
        assert result._turn_count == 2  # two user messages

    async def test_session_born_logged_as_memory_write(self, tmp_path, console, workspace):
        path = _write_anton_file(tmp_path, _make_anton_payload())
        result, new_episodic = await _do_import(tmp_path, console, workspace, path)

        mem_eps = new_episodic.get_memory_usage(result._session_id)
        writes = [e for e in mem_eps if e.role == "memory_write"]

        assert len(writes) == 1
        assert writes[0].content          == SESSION_BORN_MEMORY["content"]
        assert writes[0].meta["kind"]     == SESSION_BORN_MEMORY["kind"]
        assert writes[0].meta["topic"]    == SESSION_BORN_MEMORY["topic"]

    async def test_project_accessed_logged_as_memory_read(self, tmp_path, console, workspace):
        path = _write_anton_file(tmp_path, _make_anton_payload())
        result, new_episodic = await _do_import(tmp_path, console, workspace, path)

        mem_eps = new_episodic.get_memory_usage(result._session_id)
        reads = [e for e in mem_eps if e.role == "memory_read"]

        assert len(reads) == 1
        assert reads[0].content       == PROJECT_MEMORY["content"]
        assert reads[0].meta["kind"]  == PROJECT_MEMORY["kind"]

    async def test_conversation_replayed_to_episodic(self, tmp_path, console, workspace):
        path = _write_anton_file(tmp_path, _make_anton_payload())
        result, new_episodic = await _do_import(tmp_path, console, workspace, path)

        # The episodic file should contain user and assistant turns from the replayed history
        eps = new_episodic.get_episodes()  # all non-memory episodes
        roles = [e.role for e in eps]
        assert "user"      in roles
        assert "assistant" in roles

    async def test_new_session_id_differs_from_original(self, tmp_path, console, workspace):
        payload = _make_anton_payload()
        original_sid = payload["session"]["id"]  # "20260420_100000"
        path = _write_anton_file(tmp_path, payload)

        result, _ = await _do_import(tmp_path, console, workspace, path)

        assert result._session_id != original_sid

    async def test_provenance_suffix_set(self, tmp_path, console, workspace):
        path = _write_anton_file(tmp_path, _make_anton_payload())
        result, _ = await _do_import(tmp_path, console, workspace, path)

        suffix = result._system_prompt_context.suffix
        assert "alice" in suffix
        assert "2026-04-20" in suffix
        assert "Revenue Region Analysis" in suffix

    async def test_scratchpad_cells_restored_in_runtime(self, tmp_path, console, workspace):
        """Cells from the .anton file are restored into the new session's scratchpad."""
        path = _write_anton_file(tmp_path, _make_anton_payload())
        result, _ = await _do_import(tmp_path, console, workspace, path)

        assert "main" in result._scratchpads._pads
        restored = result._scratchpads._pads["main"].cells
        assert len(restored) == 1
        assert restored[0].code == "df.head()"
        assert restored[0].stdout == "..."

    async def test_session_born_written_to_hippocampus(self, tmp_path, console, workspace):
        """session_born memories with kind=lesson are written to cortex.project_hc."""
        path = _write_anton_file(tmp_path, _make_anton_payload())

        mock_hc = MagicMock()
        mock_cortex = MagicMock()
        mock_cortex.project_hc = mock_hc

        result, _ = await _do_import(tmp_path, console, workspace, path, cortex=mock_cortex)

        # SESSION_BORN_MEMORY has kind="lesson" and topic="sql"
        mock_hc.encode_lesson.assert_called_once_with(
            SESSION_BORN_MEMORY["content"],
            topic=SESSION_BORN_MEMORY["topic"],
            source="import",
        )
        mock_hc.encode_rule.assert_not_called()

    async def test_import_file_not_found(self, tmp_path, console, workspace):
        mock_llm = make_mock_llm()
        current_session = ChatSession(ChatSessionConfig(llm_client=mock_llm, workspace=workspace))

        result = await handle_share_import(
            console, current_session, workspace,
            MagicMock(), {"llm_client": mock_llm}, None, None, None, None,
            filepath=str(tmp_path / "nonexistent.anton"),
        )

        # Returns unchanged session
        assert result is current_session

    async def test_import_wrong_version(self, tmp_path, console, workspace):
        path = _write_anton_file(tmp_path, _make_anton_payload(version="9.9"))
        mock_llm = make_mock_llm()
        current_session = ChatSession(ChatSessionConfig(llm_client=mock_llm, workspace=workspace))

        result = await handle_share_import(
            console, current_session, workspace,
            MagicMock(), {"llm_client": mock_llm}, None, None, None, None,
            filepath=str(path),
        )

        assert result is current_session  # unchanged


# ── roundtrip ─────────────────────────────────────────────────────────────────


class TestShareRoundtrip:
    async def test_full_roundtrip(self, tmp_path, console, workspace):
        """Export a live session, import it, compare state point by point."""

        # ── 1. build exporter session ──────────────────────────────────────
        session, episodic, original_sid = _build_exporter_session(tmp_path, workspace)
        mock_llm = make_mock_llm()

        with patch("anton.commands.share._generate_meta",
                   AsyncMock(return_value=("revenue-region-yoy", "Analyzed revenue. APAC leads YoY."))):
            await handle_share_export(console, session, workspace, mock_llm, episodic)

        output_dir = tmp_path / ".anton" / "output"
        anton_file = next(output_dir.glob("*.anton"))
        payload = json.loads(anton_file.read_text())

        # ── 2. import into fresh session ───────────────────────────────────
        mock_hc = MagicMock()
        mock_cortex = MagicMock()
        mock_cortex.project_hc = mock_hc

        result, new_episodic = await _do_import(
            tmp_path / "recipient", console, workspace, anton_file,
            cortex=mock_cortex,
        )

        # ── 3. compare ─────────────────────────────────────────────────────

        # conversation history: EQUAL
        assert result._history == HISTORY, "Conversation history must be fully preserved"
        assert result._turn_count == 2

        # session_id: comes from a fresh start_session(), not copied from the .anton file
        # (the .anton file's session.id is the original exporter's ID)
        # We verify the new session got its ID from the new episodic, not the payload
        assert result._session_id == new_episodic._session_id

        # memory in new episodic: session_born → memory_write
        mem_eps = new_episodic.get_memory_usage(result._session_id)
        writes = [e for e in mem_eps if e.role == "memory_write"]
        reads  = [e for e in mem_eps if e.role == "memory_read"]

        assert len(writes) == 1
        assert writes[0].content == SESSION_BORN_MEMORY["content"]

        assert len(reads) == 1
        assert reads[0].content == PROJECT_MEMORY["content"]

        # scratchpad: one cell in .anton file, and it is restored into the runtime
        assert len(payload["scratchpad"]["cells"]) == 1
        assert "main" in result._scratchpads._pads
        assert len(result._scratchpads._pads["main"].cells) == 1

        # hippocampus: session_born (kind=lesson) written to project_hc
        mock_hc.encode_lesson.assert_called_once_with(
            SESSION_BORN_MEMORY["content"],
            topic=SESSION_BORN_MEMORY["topic"],
            source="import",
        )
        mock_hc.encode_rule.assert_not_called()

        # suffix: provenance present, original session had none
        exported_by = payload["exported_by"]
        assert exported_by not in (session._system_prompt_context.suffix or "")
        assert exported_by in result._system_prompt_context.suffix
        assert payload["session"]["title"] in result._system_prompt_context.suffix
