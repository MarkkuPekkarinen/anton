"""Extra tools for the open source terminal agent."""

from __future__ import annotations
from typing import TYPE_CHECKING

from anton.core.tools.tool_defs import ToolDef

if TYPE_CHECKING:
    from anton.core.session import ChatSession


async def handle_connect_datasource(session: ChatSession, tc_input: dict) -> str:
    """Handle connect_new_datasource tool call — interactive connection flow."""
    engine = tc_input.get("engine", "")
    if not engine:
        return "Engine name is required."

    console = session._console
    if console is None:
        return "Cannot connect datasource — no console available."

    console.print()
    console.print(
        f"[anton.prompt]anton>[/] I can help with that \u2014 let's connect [bold]{engine}[/] to Anton."
    )

    from anton.commands.datasource import handle_connect_datasource
    from anton.data_vault import DataVault

    # Check which connections exist before
    vault = DataVault()
    before = {f"{c['engine']}-{c['name']}" for c in vault.list_connections()}

    await handle_connect_datasource(
        console,
        session._scratchpads,
        session,
        prefill=engine,
    )

    # Check if a new connection was actually added
    after = {f"{c['engine']}-{c['name']}" for c in vault.list_connections()}
    new_connections = after - before

    if new_connections:
        slug = next(iter(new_connections))
        return (
            f"Successfully connected '{slug}'. The datasource is now available. "
            f"Continue helping the user with their original request using this data source."
        )
    else:
        # User cancelled or connection failed — show briefly with spinner
        # so user knows the agent is picking back up
        from rich.live import Live
        from rich.spinner import Spinner
        from rich.text import Text
        import asyncio

        console.print()
        console.print("[anton.muted]  No worries, let's continue where we left off.[/]")
        with Live(
            Spinner("dots", text=Text("", style="anton.muted"), style="anton.cyan"),
            console=console,
            refresh_per_second=10,
            transient=True,
        ):
            await asyncio.sleep(1.5)
        console.print()
        return (
            f"CANCELLED: The user pressed Escape and cancelled the '{engine}' connection. "
            f"STOP — do NOT call connect_new_datasource again. Do NOT retry. "
            f"Acknowledge the cancellation briefly and ask the user what they'd like to do instead. "
            f"Respond with TEXT ONLY — no tool calls."
        )


CONNECT_DATASOURCE_TOOL = ToolDef(
    name = "connect_new_datasource",
    description = (
        "Connect a new data source to Anton's Local Vault. Call this when the user "
        "asks a question that requires data from a source that isn't connected yet "
        "(e.g. email, database, CRM, API). This starts an interactive connection flow "
        "where the user enters their credentials.\n\n"
        "Pass the datasource type/name (e.g. 'gmail', 'postgres', 'salesforce', 'hubspot'). "
        "Anton will match it to the right connector and guide the user through setup.\n\n"
        "Do NOT print any message before calling this tool — it handles the user-facing output."
    ),
    input_schema = {
        "type": "object",
        "properties": {
            "engine": {
                "type": "string",
                "description": "The datasource type or name (e.g. 'gmail', 'postgres', 'snowflake', 'hubspot')",
            },
            "reason": {
                "type": "string",
                "description": "Brief explanation of why this datasource is needed",
            },
        },
        "required": ["engine"],
    },
    handler = handle_connect_datasource,
)


async def handle_publish_or_preview(session: ChatSession, tc_input: dict) -> str:
    """Interactive preview/publish flow after dashboard creation."""
    import os
    import webbrowser
    from pathlib import Path

    console = session._console

    raw_path = tc_input.get("file_path", "")
    title = tc_input.get("title", "Dashboard")
    action = tc_input.get("action", "ask")
    file_path = Path(raw_path)
    if not file_path.is_absolute() and session._workspace:
        file_path = Path(session._workspace.base) / raw_path

    if not file_path.exists():
        return f"File not found: {file_path}"

    # Direct preview — just open and return, no prompts
    if action in ("preview", "ask"):
        abs_path = os.path.abspath(str(file_path))
        webbrowser.open(f"file://{abs_path}")
        return f"Opened {title} in browser. The user can ask for changes or say /publish to publish it to the web."

    # Publish flow
    from anton.config.settings import AntonSettings
    from anton.publisher import publish
    from anton.utils.prompt import prompt_or_cancel

    settings = AntonSettings()

    if not settings.minds_api_key:
        console.print("  [anton.muted]To publish you need a free Minds account.[/]")
        console.print()
        has_key = await prompt_or_cancel(
            "  (anton) Do you have an mdb.ai API key?",
            choices=["y", "n"],
            choices_display="y/n",
            default="y",
        )
        if has_key is None:
            console.print()
            return "User cancelled publish."
        if has_key == "n":
            webbrowser.open(
                "https://mdb.ai/auth/realms/mindsdb/protocol/openid-connect/registrations"
                "?client_id=public-client&response_type=code&scope=openid"
                "&redirect_uri=https%3A%2F%2Fmdb.ai"
            )
            console.print()

        api_key = await prompt_or_cancel("  (anton) API key", password=True)
        if api_key is None or not api_key.strip():
            console.print()
            return "User cancelled publish."
        api_key = api_key.strip()
        settings.minds_api_key = api_key
        if session._workspace:
            session._workspace.set_secret("ANTON_MINDS_API_KEY", api_key)
        console.print()

    from rich.live import Live
    from rich.spinner import Spinner

    with Live(Spinner("dots", text="  Publishing...", style="anton.cyan"), console=console, transient=True):
        try:
            result = publish(
                file_path,
                api_key=settings.minds_api_key,
                publish_url=settings.publish_url,
                ssl_verify=settings.minds_ssl_verify,
            )
        except Exception as e:
            console.print(f"  [anton.error]Publish failed: {e}[/]")
            console.print()
            return f"PUBLISH FAILED: {e}"

    view_url = result.get("view_url", "")
    console.print(f"  [anton.success]Published![/]")
    console.print(f"  [link={view_url}]{view_url}[/link]")
    console.print()

    if view_url:
        webbrowser.open(view_url)

    return f"Published successfully!\nView URL: {view_url}"


PUBLISH_TOOL = ToolDef(
    name = "publish_or_preview",
    description = (
        "Call this after generating an HTML dashboard or report in .anton/output/. "
        "Actions: 'ask' (default) prompts the user to preview/publish/skip interactively. "
        "'preview' opens the file in the browser immediately. "
        "'publish' publishes to the web immediately. "
        "Use 'preview' or 'publish' when the user has already stated their intent. "
        "Use 'ask' after generating a new dashboard to let the user choose."
    ),
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the HTML file (e.g. .anton/output/dashboard.html)",
            },
            "title": {
                "type": "string",
                "description": "Short title describing the dashboard (e.g. 'BTC & Macro Dashboard')",
            },
            "action": {
                "type": "string",
                "enum": ["ask", "preview", "publish"],
                "description": "What to do: 'ask' prompts user, 'preview' opens locally, 'publish' publishes to web",
            },
        },
        "required": ["file_path"],
    },
    handler = handle_publish_or_preview,
)
