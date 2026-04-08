from typing import TYPE_CHECKING

from anton.core.utils.scratchpad import prepare_scratchpad_exec, format_cell_result

if TYPE_CHECKING:
    from anton.chat_session import ChatSession


async def handle_recall(session: ChatSession, tc_input: dict) -> str:
    """Process a recall tool call — search episodic memory."""
    if session._episodic is None or not session._episodic.enabled:
        return "Episodic memory is not available."

    query = tc_input.get("query", "")
    if not query:
        return "No query provided."

    kwargs: dict = {}
    if "max_results" in tc_input:
        kwargs["max_results"] = int(tc_input["max_results"])
    if "days_back" in tc_input:
        kwargs["days_back"] = int(tc_input["days_back"])

    return session._episodic.recall_formatted(query, **kwargs)


async def handle_memorize(session: ChatSession, tc_input: dict) -> str:
    """Process a memorize tool call and return a result string.

    Encoding is fire-and-forget so it never blocks scratchpad execution.
    """
    import asyncio

    if session._cortex is None:
        return "Memory system not available."

    if session._cortex.mode == "off":
        return "Memory encoding is disabled. Change memory mode via /setup to enable."

    from anton.memory.hippocampus import Engram

    raw_entries = tc_input.get("entries", [])
    if not raw_entries:
        return "No entries provided."

    engrams: list[Engram] = []
    for entry in raw_entries:
        if not isinstance(entry, dict) or "text" not in entry:
            continue

        kind = entry.get("kind", "lesson")
        if kind not in ("always", "never", "when", "lesson", "profile"):
            kind = "lesson"

        scope = entry.get("scope", "project")
        if scope not in ("global", "project"):
            scope = "project"

        # User-sourced memories (via explicit tool call) get high confidence
        engrams.append(Engram(
            text=entry["text"],
            kind=kind,
            scope=scope,
            confidence="high",
            topic=entry.get("topic", ""),
            source="user",
        ))

    if not engrams:
        return "No valid entries provided."

    # Always encode immediately via fire-and-forget — the LLM explicitly
    # chose to memorize these, so we never interrupt the user mid-turn
    # with confirmation prompts.  Confirmations are reserved for the
    # post-turn consolidator (lessons extracted from scratchpad sessions).
    async def _encode_bg(cortex, entries):
        try:
            await cortex.encode(entries)
        except Exception:
            pass  # Best-effort; don't disrupt the conversation

    asyncio.create_task(_encode_bg(session._cortex, engrams))

    descriptions = [f"Encoded {e.kind}: {e.text}" for e in engrams]
    return "Memory updated: " + "; ".join(descriptions)


async def handle_scratchpad(session: ChatSession, tc_input: dict) -> str:
    """Dispatch a scratchpad tool call by action."""
    action = tc_input.get("action", "")
    name = tc_input.get("name", "")

    if not name:
        return "Scratchpad name is required."

    if action == "exec":
        result = await prepare_scratchpad_exec(session, tc_input)
        if isinstance(result, str):
            return result
        pad, code, description, estimated_time, estimated_seconds = result

        cell = await pad.execute(
            code,
            description=description,
            estimated_time=estimated_time,
            estimated_seconds=estimated_seconds,
        )
        return format_cell_result(cell)

    elif action == "view":
        pad = session._scratchpads._pads.get(name)
        if pad is None:
            return f"No scratchpad named '{name}'."
        return pad.view()

    elif action == "reset":
        pad = session._scratchpads._pads.get(name)
        if pad is None:
            return f"No scratchpad named '{name}'."
        await pad.reset()
        return f"Scratchpad '{name}' reset. All state cleared."

    elif action == "remove":
        return await session._scratchpads.remove(name)

    elif action == "dump":
        pad = session._scratchpads._pads.get(name)
        if pad is None:
            return f"No scratchpad named '{name}'."
        return pad.render_notebook()

    elif action == "install":
        packages = tc_input.get("packages", [])
        if not packages:
            return "No packages specified."
        pad = await session._scratchpads.get_or_create(name)
        return await pad.install_packages(packages)

    else:
        return f"Unknown scratchpad action: {action}"
