"""What the generator prompts tell the model about the app preview."""

from __future__ import annotations

from pathlib import Path

from anton.core.tools.generate_artifact import prompts


def test_the_generator_prompts_pin_the_versions_the_check_carries():
    """A floating `@4`/`@5` gets whatever jsDelivr serves today, which the
    check's mirror does not hold, so the page could only be unverified."""
    for prompt in (
        prompts.build_subagent_system_prompt(Path("/tmp/a")),
        prompts.build_frontend_system_prompt(Path("/tmp/a")),
    ):
        assert "https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4.3.3" in prompt
        assert "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js" in prompt
        assert "@tailwindcss/browser@4\"" not in prompt and "echarts@5/" not in prompt


def test_the_verifier_contract_names_the_external_resource_warning():
    for prompt in (
        prompts.build_subagent_system_prompt(Path("/tmp/a")),
        prompts.build_frontend_system_prompt(Path("/tmp/a")),
    ):
        assert "the app preview does not load it" in " ".join(prompt.split())


def test_a_caught_view_error_is_logged_where_the_browser_check_sees_it():
    """A per-view `try`/`catch` that only wrote "This view failed to load"
    into the map panel passed every check while the map stayed broken: the
    check counts console errors, and the catch logged none. The rule follows
    the browser-gate bullet in both frontend prompts."""
    for prompt in (
        prompts.build_subagent_system_prompt(Path("/tmp/a")),
        prompts.build_frontend_system_prompt(Path("/tmp/a")),
    ):
        gate = prompt.index("Where a headless browser is available")
        rule = prompt.index("catch (e) { console.error(e); el.textContent")
        assert gate < rule < prompt.index("Not checked by the verifier")
