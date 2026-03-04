# AGENTS.md — Anton

## What is Anton?

Anton is an AI "doing" agent built by MindsDB. Unlike coding assistants that edit source files, Anton solves problems end-to-end: a user describes what they need in plain language, and Anton figures out the toolchain — scrapes data, writes and runs code in sandboxed scratchpads, crunches numbers, builds dashboards — and delivers the result. The code is a means to an end, not the deliverable.

Anton is currently a CLI tool (`anton` command). It has no HTTP API or server.

## Core Capabilities

| Capability | How It Works |
|---|---|
| **Natural language tasks** | User describes a problem; Anton plans and executes |
| **Scratchpad execution** | Isolated Python environments (venvs) where Anton writes and runs code. Variables persist across cells like a notebook. Auto-installs packages. |
| **Memory (semantic)** | Rules, lessons, identity, and domain expertise stored as human-readable markdown. Two scopes: global and per-project. |
| **Memory (episodic)** | Complete JSONL archive of every conversation, searchable via the `recall` tool. |
| **Multimodal input** | Images from clipboard, file references, drag-and-drop files |
| **HTML visualizations** | Anton prefers building interactive HTML dashboards/charts and opening them in the browser |
| **Learning** | Automatically extracts lessons from errors and long scratchpad sessions (consolidation). Proposes rules and lessons for future sessions. |
| **Session resume** | Can continue previous conversations with full history |

## Architecture Overview

```
User input
  → ChatSession.turn_stream(message)
    → System prompt (base + memory context + anton.md)
    → LLM call (streaming)
    → Tool loop (up to 25 rounds):
        → scratchpad: exec/view/reset/dump/install
        → memorize: write rules/lessons/profile to memory
        → recall: search episodic memory
    → Stream events back to caller
```

## Key Modules

| Module | Path | Purpose |
|---|---|---|
| `cli.py` | `anton/cli.py` | Typer CLI app, workspace boot, API key setup |
| `chat.py` | `anton/chat.py` | `ChatSession` class, `run_chat()`, streaming tool loop, slash commands |
| `chat_ui.py` | `anton/chat_ui.py` | Rich terminal UI for streaming (progress bars, tool status, markdown) |
| `tools.py` | `anton/tools.py` | Tool definitions (scratchpad, memorize, recall) + handlers + dispatch |
| `scratchpad.py` | `anton/scratchpad.py` | `Scratchpad` (venv, subprocess, cells) and `ScratchpadManager` |
| `scratchpad_boot.py` | `anton/scratchpad_boot.py` | Subprocess bootstrap: `get_llm()`, `progress()`, `sample()` |
| `workspace.py` | `anton/workspace.py` | `.anton/` directory, `anton.md`, `.env` secret vault |
| `config/settings.py` | `anton/config/settings.py` | `AntonSettings` via Pydantic (env-based config) |
| `llm/client.py` | `anton/llm/client.py` | `LLMClient` with `plan()` and `code()` (dual-model) |
| `llm/provider.py` | `anton/llm/provider.py` | `LLMProvider` ABC, all streaming event types, `ToolCall` |
| `llm/anthropic.py` | `anton/llm/anthropic.py` | Anthropic API provider |
| `llm/openai.py` | `anton/llm/openai.py` | OpenAI / OpenAI-compatible provider |
| `llm/prompts.py` | `anton/llm/prompts.py` | `CHAT_SYSTEM_PROMPT`, consolidation prompts |
| `memory/cortex.py` | `anton/memory/cortex.py` | Executive memory coordinator (builds context, encoding gate) |
| `memory/hippocampus.py` | `anton/memory/hippocampus.py` | `Engram` dataclass, per-scope memory read/write |
| `memory/episodes.py` | `anton/memory/episodes.py` | Episodic JSONL logging and `recall` search |
| `memory/consolidator.py` | `anton/memory/consolidator.py` | Post-scratchpad lesson extraction |
| `memory/history_store.py` | `anton/memory/history_store.py` | Save/load chat history for resume |

## Streaming Events

The LLM provider yields these event types (defined in `anton/llm/provider.py`). Any API layer must forward these to the frontend in real time:

| Event | Fields | Meaning |
|---|---|---|
| `StreamTextDelta` | `text` | Incremental text from the LLM |
| `StreamToolUseStart` | `id`, `name` | A tool call is beginning |
| `StreamToolUseDelta` | `id`, `json_delta` | Partial JSON for the tool call input |
| `StreamToolUseEnd` | `id` | Tool call input is complete |
| `StreamComplete` | `response` (LLMResponse) | LLM turn finished (contains usage, stop_reason) |
| `StreamTaskProgress` | `phase`, `message`, `eta_seconds` | Progress update from scratchpad execution |
| `StreamToolResult` | `content` | Tool output to display to user |
| `StreamContextCompacted` | `message` | Context was summarized to free space |

## Tools Available to Anton

**scratchpad** — Execute Python in isolated venvs. Actions: `exec`, `view`, `reset`, `remove`, `dump`, `install`. Supports `get_llm()` for LLM calls inside scratchpad, `progress()` for keepalive, `sample()` for variable inspection.

**memorize** — Write to long-term memory. Entry kinds: `always`, `never`, `when` (rules), `lesson` (facts), `profile` (user identity). Scopes: `global`, `project`.

**recall** — Search episodic memory (past conversations). Parameters: `query`, `max_results`, `days_back`.

## ChatSession Public Interface

```python
class ChatSession:
    def __init__(self, llm_client, *, cortex, episodic, workspace, ...): ...

    async def turn_stream(self, user_input: str | list[dict]) -> AsyncIterator[StreamEvent]:
        """Send a message, get streaming events back. Handles the full tool loop internally."""

    @property
    def history(self) -> list[dict]:
        """Full conversation history."""
```

`turn_stream()` takes a user message and yields streaming events. All tool execution (scratchpad, memorize, recall) happens internally within the tool loop — the caller just consumes events.

## Configuration

Settings are loaded from environment variables with `ANTON_` prefix (Pydantic Settings):

| Setting | Default | Description |
|---|---|---|
| `ANTON_PLANNING_PROVIDER` | anthropic | LLM provider for planning |
| `ANTON_PLANNING_MODEL` | claude-sonnet-4-6 | Planning model |
| `ANTON_CODING_PROVIDER` | anthropic | LLM provider for coding |
| `ANTON_CODING_MODEL` | claude-haiku-4-5-20251001 | Fast model for scratchpad, consolidation |
| `ANTON_MAX_TOKENS` | 8192 | Max output tokens |
| `ANTON_ANTHROPIC_API_KEY` | — | Anthropic API key |
| `ANTON_OPENAI_API_KEY` | — | OpenAI API key |
| `ANTON_MEMORY_MODE` | autopilot | autopilot / copilot / off |
| `ANTON_EPISODIC_MEMORY` | true | Enable episodic memory |

## File Layout on Disk

```
~/.anton/                          Global scope
└── memory/
    ├── profile.md                 User identity
    ├── rules.md                   Always/never/when rules
    ├── lessons.md                 Semantic facts
    └── topics/*.md                Domain expertise

<project>/.anton/                  Project scope
├── anton.md                       User-written project context
├── .env                           Secrets (API keys, config)
├── memory/
│   ├── rules.md
│   ├── lessons.md
│   └── topics/*.md
├── episodes/
│   └── YYYYMMDD_HHMMSS.jsonl     One file per session
└── scratchpad-venvs/              Isolated Python environments
```

## How to Create a ChatSession Programmatically

The server should mirror what `run_chat()` in `chat.py` does, but without the terminal UI:

1. Load `AntonSettings` with workspace path
2. Create `Workspace`, apply env
3. Create `LLMClient` with plan + code models
4. Create `Cortex` (global + project memory dirs, memory mode, coding LLM)
5. Create `EpisodicMemory` (episodes dir)
6. Create `HistoryStore` for resume support
7. Instantiate `ChatSession(llm_client, cortex=..., episodic=..., workspace=...)`
8. For each user message: `async for event in session.turn_stream(message): ...`

## Frontend Plan

See [plan/](plan/) for the web frontend design.
