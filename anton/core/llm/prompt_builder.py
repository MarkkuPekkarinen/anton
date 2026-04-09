from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .prompts import (
    BASE_VISUALIZATIONS_PROMPT, 
    CHAT_SYSTEM_PROMPT, 
    VISUALIZATIONS_MARKDOWN_OUTPUT_FORMAT_PROMPT,
    VISUALIZATIONS_HTML_OUTPUT_FORMAT_PROMPT,
)

if TYPE_CHECKING:
    from anton.core.tools.tool_defs import ToolDef


class ChatSystemPromptBuilder:
    """
    Build Anton's chat system prompt from core components.
    """

    def _build_tool_prompts_section(self, tool_defs: list["ToolDef"] | None) -> str:
        """Build an optional system-prompt section from `ToolDef.prompt`."""
        if not tool_defs:
            return ""

        chunks: list[str] = []
        for tool in tool_defs:
            prompt = getattr(tool, "prompt", None)

            if not prompt:
                continue

            prompt_text = str(prompt).strip()
            if not prompt_text:
                continue

            chunks.append(prompt_text)

        if not chunks:
            return ""

        return "\n\n".join(chunks)

    def _build_visualizations_section(
        self,
        *,
        proactive_dashboards: bool,
        output_path: str,
    ) -> str:
        visualizations_output_format_prompt = (
            VISUALIZATIONS_HTML_OUTPUT_FORMAT_PROMPT
            if proactive_dashboards
            else VISUALIZATIONS_MARKDOWN_OUTPUT_FORMAT_PROMPT
        )
        # The output-format prompt can reference `{output_path}`.
        output_format = visualizations_output_format_prompt.format(output_path=output_path)
        return BASE_VISUALIZATIONS_PROMPT.format(output_format=output_format)

    def build(
        self,
        *,
        settings,
        current_datetime: str,
        runtime_context: str,
        proactive_dashboards: bool,
        tool_defs: list["ToolDef"] | None = None,
        memory_context: str = "",
        project_context: str = "",
        self_awareness_context: str = "",
        datasource_context: str = "",
    ) -> str:
        output_path = f"{Path(str(settings.output_dir)).as_posix().rstrip('/')}/"

        visualizations_section = self._build_visualizations_section(
            proactive_dashboards=proactive_dashboards,
            output_path=output_path,
        )

        prompt = CHAT_SYSTEM_PROMPT.format(
            runtime_context=runtime_context,
            visualizations_section=visualizations_section,
            current_datetime=current_datetime,
        )

        tool_prompts = self._build_tool_prompts_section(tool_defs)
        if tool_prompts:
            prompt += tool_prompts

        if memory_context:
            prompt += memory_context
        if project_context:
            prompt += project_context
        if self_awareness_context:
            prompt += self_awareness_context
        if datasource_context:
            prompt += datasource_context

        return prompt


__all__ = ["ChatSystemPromptBuilder"]

