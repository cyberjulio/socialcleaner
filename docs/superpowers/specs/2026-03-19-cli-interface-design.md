# SocialCleaner CLI Interface — Design Spec

## Overview

A terminal-based interface for SocialCleaner that provides an alternative to the web dashboard. Targets non-technical users — all interactions must be intuitive with zero dev knowledge required.

## Entry Point

`python -m cli` launches the main menu. Single-keypress navigation throughout (no Enter key needed).

## Main Menu

```
╭─ SocialCleaner ─────────────────────────╮
│                                         │
│  1. Start Web Dashboard                 │
│  2. Unlike Instagram Posts              │
│  3. Delete Instagram Comments           │
│  4. Manage Accounts                     │
│  5. About                               │
│  6. Quit                                │
│                                         │
╰─────────────────────────────────────────╯
```

Menu loops after each action completes. Ctrl+C anywhere triggers graceful cleanup and returns to menu (or exits if at menu).

## 1. Start Web Dashboard

- Checks port 8000 availability first; shows clear error if occupied
- Launches a single uvicorn process serving the FastAPI backend (which serves the pre-built frontend from `frontend/dist/` as static files)
- Opens the user's default browser to `http://127.0.0.1:8000`
- Shows "Web dashboard running at http://127.0.0.1:8000 — Press Q to stop"
- Q returns to main menu, stopping the server

## 2. Unlike Instagram Posts / 3. Delete Instagram Comments

Both follow identical flow, differing only in the operation type.

### Step 0 — Resume Check
Before starting a new task, check DB for any existing `paused` or `pending` tasks of the same type for this account. If found, ask: "You have a paused task (342/812 unliked). Resume? (Y/N)". Y resumes from where it left off; N starts fresh.

### Step 1 — Account Selection
- If one account: auto-select, show "Using account @username"
- If multiple: numbered list, single keypress to pick
- If none: prompt to add account first, redirect to Manage Accounts

### Step 2 — Confirmation
```
Ready to unlike Instagram posts for @username
This will remove likes starting from the oldest.
Press Y to start, any other key to cancel.
```

### Step 3 — Live Progress (rich TUI)
```
╭─ Unliking Posts · @username ────────────────╮
│                                             │
│  Progress  [████████░░░░░░░░░░]  42%        │
│  Unliked   342 / 812                        │
│  Speed     ~48/hr                           │
│  Elapsed   1h 23m                           │
│  Status    Cruising                         │
│                                             │
│  Latest: Unliked post by @photographer123   │
│                                             │
│  Press Q to stop · Press P to pause         │
╰─────────────────────────────────────────────╯
```

- `rich.live` for real-time updates
- Shows: progress bar, count, speed, elapsed time, current phase (warm-up/cruising/scroll break/session break)
- During breaks: countdown timer (`Session break: resuming in 32m 15s`)
- Q: graceful stop (task saved as `paused`, can resume later)
- P: pause/resume toggle

**Error states displayed in Status line:**
- Rate limited: `Rate limited — backing off 45m`
- Session break: `Session break — resuming in 32m 15s`
- Checkpoint required: `Instagram needs verification — check browser window`
- Action block: `Action blocked by Instagram — waiting 24h`
- Session expired: `Session expired — re-login needed` (stops task, returns to menu)

### Step 4 — Completion Summary
```
Done! Unliked 812 posts in 4h 12m.
```
Or if capped: `Daily cap reached (800 actions today). Run again tomorrow to continue.`

## 4. Manage Accounts

Submenu (single-keypress navigation):

```
╭─ Manage Accounts ───────────────────────╮
│                                         │
│  1. Add Instagram Account               │
│  2. View Connected Accounts             │
│  3. Remove Account                      │
│  4. Back to Main Menu                   │
│                                         │
╰─────────────────────────────────────────╯
```

### Add Instagram Account
1. Show "Opening Instagram login page..." with spinner
2. Playwright opens a **visible** Firefox browser to `instagram.com/accounts/login/`
3. User logs in manually (handles 2FA in the browser)
4. CLI polls for `sessionid` cookie presence (checks every 2s)
5. On cookie detection: extract `sessionid`, `csrftoken`, `ds_user_id`
6. Validate session by calling `InstagramClient.validate_session()` — confirms cookies actually work
7. Capture username from validation response
8. Encrypt cookies with Fernet, store in `cleaner.db` sessions table with generic Firefox UA
9. Close browser, show "Account @username connected successfully"
10. If validation fails: show "Login detected but session couldn't be verified. Try again?" (y/n)

This is **new code** — does not reuse `backend/routers/auth.py` (which receives pre-extracted cookies from the web frontend). Shared components: `encrypt_json` from crypto.py, `get_db` from database.py, sessions table schema.

### View Connected Accounts
Rich table: username, platform, date added, status (valid/invalid)

### Remove Account
Numbered list of accounts, single keypress to select, y/n confirmation.

## 5. About

```
╭─ About SocialCleaner ──────────────────────╮
│                                            │
│  SocialCleaner v1.0                        │
│  Bulk-remove likes and comments from       │
│  Instagram. Self-hosted, private,          │
│  your data never leaves your machine.      │
│                                            │
│  GitHub: github.com/...                    │
│  License: MIT                              │
│                                            │
│  Press any key to return to menu           │
╰────────────────────────────────────────────╯
```

## Architecture

### File Structure
```
cli/
  __init__.py
  __main__.py      # Entry point (python -m cli)
  app.py           # Main menu loop + rich console setup
  auth.py          # Interactive browser login + account management
  tasks.py         # Unlike/comments flows + rich progress display
  display.py       # Shared rich components (menus, tables, panels)
```

### Database Initialization
The CLI must call `await init_db()` from `backend.database` at startup before any DB operations. This is the same initialization the web app does in its lifespan handler.

### Database Path
`backend.config.Settings` defaults `db_path` to `"cleaner.db"` (relative). The CLI resolves this to an absolute path relative to the project root (where `backend/` lives) to ensure the same DB is used regardless of the user's working directory.

### Engine Integration — TaskEventSink Protocol
The existing `WorkerEngine._run_task()` has ~15 hardcoded `event_bus.publish()` calls alongside the platform client callbacks. To support CLI without duplicating task orchestration logic:

1. Introduce a `TaskEventSink` protocol in `backend/worker/engine.py`:
   ```python
   class TaskEventSink(Protocol):
       async def publish(self, task_id: str, event_type: str, data: dict): ...
   ```
2. Replace direct `event_bus.publish()` calls in `_run_task` with `self._sink.publish()`
3. The existing `EventBus` satisfies this protocol (web path unchanged)
4. The CLI provides its own sink that routes events to the `rich.live` display

This is a small refactor (~15 lines changed in engine.py) that cleanly decouples the engine from the SSE transport.

### Rate Limiting
The engine uses `backend.worker.rate_limiter.RateLimiter` for inter-action delays. `InstagramClient` has its own session/daily tracking (`_session_start`, `_daily_actions`, `DAILY_CAP`). Both are active during task execution — the engine's `RateLimiter` governs inter-action wait times, while the client's internal tracking governs session breaks and daily caps. This is the same dual system the web version uses; no changes needed.

### Import Safety
The CLI must not transitively import `backend.main` (which creates the FastAPI app). It imports only: `backend.database`, `backend.platforms.*`, `backend.worker.engine`, `backend.worker.rate_limiter`, `backend.utils.crypto`, `backend.config`.

### Core Principles

- **Direct module imports** — no HTTP calls. Reuses backend modules directly.
- **Shared database** — same `cleaner.db`. Accounts added via CLI appear in web and vice versa.
- **TaskEventSink** — engine refactored to accept an event sink; CLI provides a rich-based sink.
- **Single new dependency** — `rich`.

### User-Agent Logic
- CLI-created sessions: generic Firefox UA (`Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0`)
- Web-created sessions: reuse the captured UA from the web portal
- The session's stored UA is always used when running tasks (regardless of which interface started the task)

### Single-Keypress Input
Uses raw terminal mode (`tty`/`termios` on Unix, `msvcrt` on Windows) for instant menu navigation. No Enter key required.

### Signal Handling
Ctrl+C (SIGINT) is caught globally. During task execution: triggers graceful stop (same as Q), saves progress. At menu: exits cleanly.

## Documentation Updates

- Update `README.md` with CLI usage instructions alongside web usage
- Add CLI section explaining: installation, how to run, menu walkthrough
- Update `CLAUDE.md` with CLI module structure

## Testing Plan

1. CLI launches and displays menu correctly
2. Single-keypress navigation works (keys 1-6)
3. "Start Web Dashboard" checks port, launches server, opens browser
4. "Manage Accounts > Add" opens visible Firefox browser to Instagram login
5. "View Connected Accounts" shows accounts from shared DB
6. "Remove Account" works with confirmation
7. "Unlike" and "Delete Comments" flows: account selection, resume check, confirmation, progress display
8. Error states render correctly in progress TUI
9. Q/P keybindings work during task execution
10. Ctrl+C triggers graceful stop
11. About screen renders correctly
12. Quit exits cleanly
13. Accounts created in CLI appear in web dashboard and vice versa
