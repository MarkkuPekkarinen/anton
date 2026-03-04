# Anton Web — Phase 1 (Chat MVP)

A web frontend for Anton. Chat with Anton in your browser with real-time streaming, file uploads, and inline output rendering.

## Quick Start

### 1. Backend

From the **repo root** (`anton/`):

```bash
# Install backend dependencies (one-time)
uv pip install fastapi 'uvicorn[standard]' websockets python-multipart

# Start the API server
cd anton-web
uv run --project .. uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

The server creates its workspace at `~/.anton-web` and reads API keys from your existing `~/.anton/.env`.

### 2. Frontend

In a separate terminal:

```bash
cd anton-web/frontend
npm install    # one-time
npm run dev
```

Open **http://localhost:5173** in your browser.

## Architecture

```
Browser (React) ←→ WebSocket ←→ FastAPI ←→ ChatSession (anton package)
```

- **Backend** (`api/`): Thin FastAPI wrapper around `ChatSession.turn_stream()`. No agent logic — just plumbing.
- **Frontend** (`frontend/`): React + Tailwind CSS chat UI with streaming markdown rendering.

## What Works

- Real-time text streaming (typewriter effect)
- Working indicator during tool execution (spinner + status message)
- File upload via drag-and-drop or button
- Inline rendering of HTML outputs (iframe) and images
- Open outputs in new tab
- Session resume via WebSocket
- Markdown rendering with GFM tables, code blocks, etc.
