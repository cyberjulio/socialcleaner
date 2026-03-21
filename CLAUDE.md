# SocialCleaner — CLAUDE.md

## Project Overview
Self-hosted tool for bulk-removing likes and comments from Instagram (Twitter/X in development). Browser automation via Playwright — credentials never leave the user's machine. Session cookies are encrypted at rest with Fernet (AES-128-CBC).

## Tech Stack
- **Backend**: FastAPI + Uvicorn + aiosqlite (SQLite, WAL mode) + Playwright + sse-starlette
- **CLI**: rich (TUI) + Playwright (interactive browser login)
- **Frontend**: React 19 + Vite 6 + Tailwind CSS 4
- **DB**: SQLite (`cleaner.db`)
- **Encryption**: Fernet via `cryptography` package
- **Config**: pydantic-settings from `.env`

## Running the Project

```bash
# CLI (recommended for users)
source venv/bin/activate && python -m cli

# Web dashboard only
source venv/bin/activate && uvicorn backend.main:app --host 127.0.0.1 --port 8647

# Development (both servers with hot reload)
./scripts/dev.sh
# Backend:  http://127.0.0.1:8647
# Frontend: http://127.0.0.1:5173
```

## Key Structure
```
cli/
  __main__.py      # Entry point (python -m cli)
  app.py           # Main menu loop + rich console setup
  auth.py          # Interactive browser login + account management
  tasks.py         # Task execution + rich progress display
  display.py       # Shared rich components (menus, panels, keypress)

backend/
  main.py          # FastAPI app entry point (lifespan management)
  config.py        # Settings (secret key, host, port, db path)
  models.py        # Pydantic request/response models
  database.py      # SQLite schema + connection
  routers/
    auth.py        # /api/auth/* — session management + browser login flow
    tasks.py       # /api/tasks/* — task CRUD + SSE streaming
  platforms/
    base.py        # Abstract PlatformClient
    instagram.py   # Instagram automation (likes/comments)
    twitter.py     # Twitter/X stub
    user_agents.py # Browser UA strings
  utils/
    crypto.py      # Fernet encrypt/decrypt
    events.py      # EventBus for SSE per task_id
  worker/
    engine.py      # Task executor, browser lifecycle, TaskEventSink protocol
    rate_limiter.py# Human-like tiered delays + Gaussian jitter

frontend/src/
  App.jsx          # Main shell, hash-based bookmarklet import (#import/<base64>)
  api.js           # Unified fetch wrapper
  components/
    Dashboard.jsx      # Account list + task list
    TaskCard.jsx       # Live SSE progress display
    CookieWizard.jsx   # Account connection flow (browser login, console snippet, manual paste)
```

## Architecture Notes
- **Async-first**: All I/O uses async/await (aiosqlite, Playwright async APIs)
- **TaskEventSink protocol**: Engine uses injectable event sink — `EventBus` for web (SSE), `CLIEventSink` for CLI (rich TUI). Decouples task execution from transport.
- **SSE streaming**: Web real-time progress via EventBus → `/api/tasks/{id}/stream`
- **CLI progress**: rich `Live` display updated via `CLIEventSink` callbacks
- **Browser matching**: Uses Firefox for Firefox UAs, Chromium otherwise (avoids bot detection)
- **Browser login**: Both CLI and web offer interactive browser login — launches visible Firefox, user logs in normally, cookies captured automatically. Web also has console snippet and manual paste as fallbacks.
- **CLI UA**: Sessions created via CLI or browser login use a generic Firefox UA; cookie-based web sessions reuse the captured UA from the portal
- **Rate limiting tiers**: warm-up (8-15s) → cruising (4-10s) → scroll breaks (2-5min every 30-60 actions) → session breaks (15-45min every 150-200 actions); daily caps: 800 (Instagram), 350 (Twitter)
- **Daily cap behaviour**: When daily cap is hit, `DailyCapReached` is raised (in `base.py`) and the engine marks the task `completed` immediately — no waiting
- **Session resumption**: Tasks in `running`/`scanning` state at startup are reset to `pending` and re-queued
- **Orphaned process cleanup**: Engine kills stray browser processes from crashed runs

## Instagram Comment Deletion — Implementation Notes
Batch deletion on `/your_activity/interactions/comments` is tricky; key lessons learned:

- **Page load**: The React SPA needs 6–9s to render comments after `domcontentloaded`. Both `_fetch_comments` and `_batch_delete_comments` retry `_count_comments` up to 3× (3s each) and log the page body on zero — do not reduce this.
- **Session init**: Both `_batch_delete_comments` and `_batch_unlike_likes` MUST use `_new_page()` (visits homepage first) — not raw `context.new_page()`. Instagram requires a homepage visit to activate session cookies before navigating to deep URLs; without it, the user gets redirected to login.
- **Count detection**: `_count_comments` matches short timestamp spans (`2w`, `3h`, etc.) + fallback to "Select" button presence. Does NOT match long-form dates ("March 5") — keep the regex tight.
- **Batch flow**: Select → wait for checkboxes (poll up to 5s) → click checkboxes → click action-bar Delete → wait for dialog (poll up to 10s) → click dialog Delete → reload.
- **Checkbox wait (step 2)**: After clicking Select, poll up to 5× 1s for `[aria-label="Toggle checkbox"]` elements to appear. A fixed wait is unreliable — the SPA can be slow to enter selection mode.
- **Action-bar Delete (step 3)**: Use `button, [role="button"]` with `innerText` (not `textContent`) to avoid matching parent wrapper divs. Pick the element closest to the viewport bottom.
- **Confirmation dialog (step 4)**: Poll up to 20× 500ms (10s) for a "Cancel" `get_by_text` match (appears in dialog). When found, use `page.get_by_text("Delete", exact=True)` + `.click(force=True)` on the candidate whose Y differs from the action-bar button by >30px. `el.click()` (JS) and `page.mouse.click` (coordinates) both fail on Instagram's SPA — only Playwright's native CDP click works.
- **Verification**: Do NOT use count comparison before/after deletion. Instagram lazy-loads more comments on page reload, causing the count to go UP even after successful deletion. Instead, trust the confirmation dialog click as proof of success. Only use count to detect when no comments remain (count=0 → done).
- **Concurrent tasks**: The API prevents creating a new task when another is active for the same session (409 Conflict). Two headless browsers on the same Instagram account would cause session conflicts.
- **Session dedup**: Reconnecting an account (browser login or cookie paste) replaces the existing session for the same platform+username, preventing duplicates.
- **Log messages**: Engine and frontend suppress internal IDs (`batch_comments`, `batch_likes`) from user-visible logs. Log messages should be human-readable.

## Database Schema
| Table | Purpose |
|-------|---------|
| `sessions` | Encrypted cookies, UA string, username, validity flag |
| `tasks` | Cleaning jobs (platform, target_type, status, counts) |
| `items` | Individual likes/comments (platform_id, status, attempts, errors) |
| `events` | Persisted SSE event logs for task history |

## Environment Variables (`.env`)
```
CLEANER_SECRET_KEY=<random>   # Fernet key for cookie encryption (required)
HOST=127.0.0.1
PORT=8647
```

## Frontend Build
The FastAPI app serves `frontend/dist/` as static files. After ANY frontend change, rebuild with `cd frontend && npm run build` — the served app uses the built bundle, not the source. Forgetting to rebuild means users see stale code even though the source is correct.

## Tests
No formal test suite. `test_unlike.py`, `test_comments.py`, `test_debug/` in root are dev debugging artifacts. Use `pytest` + `pytest-asyncio` to add tests.

## Conventions
- All API routes under `/api/` prefix
- UUID for all entity IDs (session, task, item)
- `datetime('now')` for SQLite timestamps
- Pydantic response models for all endpoints

## Process Management — IMPORTANT
- **NEVER** use `lsof -ti :PORT | xargs kill` or similar broad patterns to kill processes. This kills ANY process with a connection to that port — including the user's browser if it has a tab open to that address.
- To stop a server you started, track its PID and `kill $PID`.
- If you must find a listening server by port, use `lsof -ti :PORT -sTCP:LISTEN | xargs kill` to only target the listener, not clients.
- Never use `pgrep`/`pkill` with broad patterns that could match user applications.
