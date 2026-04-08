from anton.core.tools.tool_defs import MEMORIZE_TOOL, RECALL_TOOL, SCRATCHPAD_TOOL, ToolDef


class ToolRegistry:
    """
    Registry of tools available to the LLM.
    """
    def __init__(self) -> None:
        # Register core tools.
        self._tools = [SCRATCHPAD_TOOL, MEMORIZE_TOOL, RECALL_TOOL]

    def register_tool(self, tool_def: ToolDef) -> None:
        """
        Register a new (extra to core) tool.
        """
        self._tools.append(tool_def)

    def dispatch_tool(self, tool_name: str, tc_input: dict) -> str:
        """
        Dispatch a tool call by name. Returns result text.
        """
        tool_def = next((tool for tool in self._tools if tool.name == tool_name), None)
        if tool_def is None:
            raise ValueError(f"Tool {tool_name} not found")
        return tool_def.handler(tc_input)
