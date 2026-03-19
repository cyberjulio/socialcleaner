# SocialCleaner — CLAUDE.md

## Project Overview
Self-hosted tool for bulk-removing likes and comments from Instagram (Twitter/X in development). Browser automation via Playwright — credentials never leave the user's machine. Session cookies are encrypted at rest with Fernet (AES-128-CBC).

## Tech Stack
- **Backend**: FastAPI + Uvicorn + aiosqlite (SQLite, WAL mode) + Playwright + sse-starlette
- **Frontend**: React 19 + Vite 6 + Tailwind CSS 4
- **DB**: SQLite (`cleaner.db`)
- **Encryption**: Fernet via `cryptography` package
- **Config**: pydantic-settings from `.env`

## Running the Project

```bash
# Development (both servers at once)
./scripts/dev.sh
# Backend:  http://127.0.0.1:8000
# Frontend: http://127.0.0.1:5173

# Or separately:
source venv/bin/activate && uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
cd frontend && npm run dev
```

## Key Structure
```
backend/
  main.py          # FastAPI app entry point (lifespan management)
  config.py        # Settings (secret key, host, port, db path)
  models.py        # Pydantic request/response models
  database.py      # SQLite schema + connection
  routers/
    auth.py        # /api/auth/* — session management
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
    engine.py      # Task executor, browser lifecycle
    rate_limiter.py# Human-like tiered delays + Gaussian jitter

frontend/src/
  App.jsx          # Main shell, hash-based bookmarklet import (#import/<base64>)
  api.js           # Unified fetch wrapper
  components/
    Dashboard.jsx      # Account list + task list
    TaskCard.jsx       # Live SSE progress display
    CookieWizard.jsx   # Account connection flow
```

## Architecture Notes
- **Async-first**: All I/O uses async/await (aiosqlite, Playwright async APIs)
- **SSE streaming**: Real-time progress via EventBus → `/api/tasks/{id}/stream`
- **Browser matching**: Uses Firefox for Firefox UAs, Chromium otherwise (avoids bot detection)
- **Rate limiting tiers**: warm-up (8-15s) → cruising (4-10s) → scroll breaks (2-5min every 30-60 actions) → session breaks (15-45min every 150-200 actions); daily caps: 800 (Instagram), 350 (Twitter)
- **Daily cap behaviour**: When daily cap is hit, `DailyCapReached` is raised (in `base.py`) and the engine marks the task `completed` immediately — no waiting
- **Session resumption**: Tasks in `running`/`scanning` state at startup are reset to `pending` and re-queued
- **Orphaned process cleanup**: Engine kills stray browser processes from crashed runs

## Instagram Comment Deletion — Implementation Notes
Batch deletion on `/your_activity/interactions/comments` is tricky; key lessons learned:

- **Page load**: The React SPA needs 6–9s to render comments after `domcontentloaded`. `_fetch_comments` retries `_count_comments` up to 3× (3s each) and logs the page body on zero — do not reduce this.
- **Count detection**: `_count_comments` matches short timestamp spans (`2w`, `3h`, etc.) + fallback to "Select" button presence. Does NOT match long-form dates ("March 5") — keep the regex tight.
- **Batch flow**: Select → check checkboxes → click action-bar Delete → wait for dialog → click dialog Delete → reload → verify count dropped.
- **Action-bar Delete (step 3)**: Use `button, [role="button"]` with `innerText` (not `textContent`) to avoid matching parent wrapper divs. Pick the element closest to the viewport bottom.
- **Confirmation dialog (step 4)**: Poll for a "Cancel" `get_by_text` match (appears in nav and in dialog). When found, use `page.get_by_text("Delete", exact=True)` + `.click(force=True)` on the candidate whose Y differs from the action-bar button by >30px. `el.click()` (JS) and `page.mouse.click` (coordinates) both fail on Instagram's SPA — only Playwright's native CDP click works.
- **Verification**: Compare `_count_comments` before vs after reload. If count decreased, credit `selected_count` as deleted (not the raw delta, which can be inflated by lazy-loaded timestamps).

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
PORT=8000
```

## Tests
No formal test suite. `test_unlike.py`, `test_comments.py`, `test_debug/` in root are dev debugging artifacts. Use `pytest` + `pytest-asyncio` to add tests.

## Conventions
- All API routes under `/api/` prefix
- UUID for all entity IDs (session, task, item)
- `datetime('now')` for SQLite timestamps
- Pydantic response models for all endpoints
