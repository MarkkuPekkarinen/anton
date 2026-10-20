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
