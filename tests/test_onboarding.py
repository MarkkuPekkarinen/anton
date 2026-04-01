from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from anton.onboarding import (
    PromptSuggestion,
    build_suggestions,
    display_suggestions,
    prompt_for_selection,
)


class TestPromptSuggestion:
    def test_fields_populated(self):
        s = PromptSuggestion(
            display_text="Short display",
            prompt_text="Long detailed prompt",
            category="showcase",
        )
        assert s.display_text == "Short display"
        assert s.prompt_text == "Long detailed prompt"
        assert s.category == "showcase"

    def test_display_and_prompt_can_differ(self):
        s = PromptSuggestion(
            display_text="Short",
            prompt_text="Very long and detailed version of Short",
            category="analysis",
        )
        assert s.display_text != s.prompt_text


class TestBuildSuggestions:
    def test_returns_four_suggestions(self):
        assert len(build_suggestions()) == 4

    def test_first_is_showcase(self):
        assert build_suggestions()[0].category == "showcase"

    def test_last_is_dashboard(self):
        assert build_suggestions()[-1].category == "dashboard"

    def test_has_datasource_suggestion(self):
        suggestions = build_suggestions()
        ds = [s for s in suggestions if s.category == "datasource"]
        assert len(ds) == 1
        assert "datasource" in ds[0].display_text.lower()

    def test_has_analysis_suggestion(self):
        suggestions = build_suggestions()
        analysis = [s for s in suggestions if s.category == "analysis"]
        assert len(analysis) == 1
        assert "Analyze" in analysis[0].display_text

    def test_all_have_non_empty_texts(self):
        for s in build_suggestions():
            assert s.display_text.strip()
            assert s.prompt_text.strip()


class TestDisplaySuggestions:
    def test_calls_console_print(self):
        console = MagicMock()
        display_suggestions(console, build_suggestions())
        assert console.print.called

    def test_prints_at_least_header_plus_suggestions(self):
        console = MagicMock()
        suggestions = build_suggestions()
        display_suggestions(console, suggestions)
        assert console.print.call_count >= len(suggestions) + 1


class TestPromptForSelection:
    @pytest.fixture()
    def suggestions(self):
        return build_suggestions()

    @pytest.fixture()
    def mock_session(self):
        session = MagicMock()
        session.prompt_async = AsyncMock()
        return session

    @pytest.mark.asyncio
    async def test_number_returns_suggestion_prompt(self, suggestions, mock_session):
        mock_session.prompt_async.return_value = "1"
        result = await prompt_for_selection(MagicMock(), suggestions, mock_session)
        assert result == suggestions[0].prompt_text

    @pytest.mark.asyncio
    async def test_number_2_returns_second(self, suggestions, mock_session):
        mock_session.prompt_async.return_value = "2"
        result = await prompt_for_selection(MagicMock(), suggestions, mock_session)
        assert result == suggestions[1].prompt_text

    @pytest.mark.asyncio
    async def test_empty_input_returns_none(self, suggestions, mock_session):
        mock_session.prompt_async.return_value = ""
        result = await prompt_for_selection(MagicMock(), suggestions, mock_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_none(self, suggestions, mock_session):
        mock_session.prompt_async.return_value = "   "
        result = await prompt_for_selection(MagicMock(), suggestions, mock_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_custom_text_returns_as_is(self, suggestions, mock_session):
        mock_session.prompt_async.return_value = "What can you do?"
        result = await prompt_for_selection(MagicMock(), suggestions, mock_session)
        assert result == "What can you do?"

    @pytest.mark.asyncio
    async def test_out_of_range_number_returns_as_text(self, suggestions, mock_session):
        mock_session.prompt_async.return_value = "99"
        result = await prompt_for_selection(MagicMock(), suggestions, mock_session)
        assert result == "99"

    @pytest.mark.asyncio
    async def test_eof_returns_none(self, suggestions, mock_session):
        mock_session.prompt_async.side_effect = EOFError
        result = await prompt_for_selection(MagicMock(), suggestions, mock_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_returns_none(self, suggestions, mock_session):
        mock_session.prompt_async.side_effect = KeyboardInterrupt
        result = await prompt_for_selection(MagicMock(), suggestions, mock_session)
        assert result is None
