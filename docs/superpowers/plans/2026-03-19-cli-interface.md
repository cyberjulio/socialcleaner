# CLI Interface Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a terminal-based CLI interface to SocialCleaner that reuses the existing backend modules, shares the same database, and provides interactive browser login + rich TUI progress for non-technical users.

**Architecture:** The CLI is a new `cli/` package that imports backend modules directly (no HTTP). Engine is refactored with a `TaskEventSink` protocol to decouple from SSE. The CLI provides its own sink that feeds a `rich.live` display. Single-keypress menus via raw terminal input.

**Tech Stack:** Python 3.12+, rich (TUI), Playwright (browser login), existing backend modules

---

## File Structure

| File | Responsibility |
|------|---------------|
| `cli/__init__.py` | Package marker |
| `cli/__main__.py` | Entry point (`python -m cli`), resolves DB path, calls `app.main()` |
| `cli/app.py` | Main menu loop, console setup, signal handling |
| `cli/display.py` | Shared rich components: menus, keypress reader, panels, tables |
| `cli/auth.py` | Interactive Playwright browser login, account list/remove |
| `cli/tasks.py` | Task flows (unlike/comments): account selection, resume check, progress TUI |
| `backend/worker/engine.py` | Refactored: `TaskEventSink` protocol, `_run_task` accepts sink parameter |

---

## Chunk 1: Foundation — Engine Refactor + CLI Skeleton

### Task 1: Refactor engine.py to use TaskEventSink protocol

**Files:**
- Modify: `backend/worker/engine.py`
- Modify: `backend/utils/events.py`

The engine currently has ~15 hardcoded `event_bus.publish()` calls. We introduce a `TaskEventSink` protocol and inject it, so the CLI can provide its own sink.

- [ ] **Step 1: Add TaskEventSink protocol to engine.py**

Add at top of `backend/worker/engine.py`, after imports:

```python
from typing import Protocol

class TaskEventSink(Protocol):
    async def publish(self, task_id: str, event_type: str, data: dict) -> None: ...
```

- [ ] **Step 2: Add sink parameter to WorkerEngine.__init__**

```python
class WorkerEngine:
    def __init__(self, sink: TaskEventSink | None = None):
        self._browser: Browser | None = None
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._pw = None
        self._sink = sink or event_bus  # default to SSE event bus
```

- [ ] **Step 3: Replace all `event_bus.publish` calls with `self._sink.publish`**

In `_run_task`, replace every `await event_bus.publish(task_id, ...)` with `await self._sink.publish(task_id, ...)`. There are ~15 occurrences across lines 176, 194, 216, 219, 227, 238, 241, 246, 251, 258, 266, 298, 313, 329, 332, 361, 373, 381. Also update the `log_to_frontend` and `emit_event` closures (lines 175-176, 200-201) to use `self._sink.publish`.

- [ ] **Step 4: Update singleton to use default event_bus**

```python
# Singleton (default: SSE event bus)
worker_engine = WorkerEngine()
```

- [ ] **Step 5: Verify web version still works**

Run: `cd /Users/cyberjulio/Coding/cleaner && source venv/bin/activate && python -c "from backend.worker.engine import worker_engine; print('OK')"`

Expected: `OK` — no import errors.

- [ ] **Step 6: Commit**

```bash
git add backend/worker/engine.py
git commit -m "refactor: decouple engine from EventBus via TaskEventSink protocol"
```

---

### Task 2: Create CLI skeleton with main menu

**Files:**
- Create: `cli/__init__.py`
- Create: `cli/__main__.py`
- Create: `cli/display.py`
- Create: `cli/app.py`

- [ ] **Step 1: Create `cli/__init__.py`**

Empty file.

- [ ] **Step 2: Create `cli/display.py` — shared rich components + keypress reader**

```python
"""Shared display components for the CLI."""
import sys
import tty
import termios
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

def read_key() -> str:
    """Read a single keypress without requiring Enter."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        # Handle Ctrl+C
        if ch == '\x03':
            raise KeyboardInterrupt
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def show_menu(title: str, options: list[str], border_style: str = "cyan") -> str | None:
    """Display a numbered menu and return the key pressed.

    Returns the character pressed, or None if invalid.
    """
    lines = []
    for i, option in enumerate(options, 1):
        lines.append(f"  {i}. {option}")

    content = "\n".join(lines)
    panel = Panel(content, title=title, border_style=border_style, padding=(1, 2))
    console.print(panel)
    console.print("  [dim]Press a number to select[/dim]\n")

    key = read_key()
    return key


def show_panel(title: str, content: str, border_style: str = "cyan"):
    """Display a simple panel."""
    panel = Panel(content, title=title, border_style=border_style, padding=(1, 2))
    console.print(panel)


def confirm(message: str) -> bool:
    """Ask for Y/N confirmation with single keypress."""
    console.print(f"\n  {message} [cyan](Y/N)[/cyan] ", end="")
    key = read_key().lower()
    console.print(key)
    return key == "y"
```

- [ ] **Step 3: Create `cli/__main__.py` — entry point**

```python
"""Entry point: python -m cli"""
import os
import sys

# Resolve project root (parent of cli/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)

# Ensure project root is on sys.path so backend imports work
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio
from cli.app import main

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Create `cli/app.py` — main menu loop**

```python
"""Main CLI application — menu loop and signal handling."""
import signal
import sys
from cli.display import console, show_menu, show_panel, read_key

BANNER = r"""[bold cyan]
  ____             _       _  ____ _
 / ___|  ___   ___(_) __ _| |/ ___| | ___  __ _ _ __   ___ _ __
 \___ \ / _ \ / __| |/ _` | | |   | |/ _ \/ _` | '_ \ / _ \ '__|
  ___) | (_) | (__| | (_| | | |___| |  __/ (_| | | | |  __/ |
 |____/ \___/ \___|_|\__,_|_|\____|_|\___|\__,_|_| |_|\___|_|
[/bold cyan]"""

async def main():
    """Main CLI entry point."""
    from backend.database import init_db
    await init_db()

    console.clear()
    console.print(BANNER)

    while True:
        try:
            key = show_menu(
                "SocialCleaner",
                [
                    "Start Web Dashboard",
                    "Unlike Instagram Posts",
                    "Delete Instagram Comments",
                    "Manage Accounts",
                    "About",
                    "Quit",
                ],
            )

            if key == "1":
                await start_web_dashboard()
            elif key == "2":
                from cli.tasks import run_task_flow
                await run_task_flow("likes")
            elif key == "3":
                from cli.tasks import run_task_flow
                await run_task_flow("comments")
            elif key == "4":
                from cli.auth import manage_accounts
                await manage_accounts()
            elif key == "5":
                show_about()
            elif key == "6":
                console.print("\n  [cyan]Goodbye![/cyan]\n")
                sys.exit(0)

            console.print()  # spacing between actions

        except KeyboardInterrupt:
            console.print("\n  [cyan]Goodbye![/cyan]\n")
            sys.exit(0)


async def start_web_dashboard():
    """Launch uvicorn serving the full app (backend + frontend static files)."""
    import socket
    import subprocess
    import webbrowser
    import asyncio

    # Check port availability
    port = 8000
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            console.print(f"\n  [red]Port {port} is already in use.[/red]")
            console.print("  [dim]Stop the other process and try again.[/dim]")
            console.print("\n  Press any key to return to menu...")
            read_key()
            return

    console.print(f"\n  [cyan]Starting web dashboard...[/cyan]")

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Give server a moment to start
    await asyncio.sleep(2)

    url = f"http://127.0.0.1:{port}"
    webbrowser.open(url)
    console.print(f"  [green]Web dashboard running at {url}[/green]")
    console.print("  [dim]Press Q to stop the server and return to menu[/dim]\n")

    try:
        while True:
            key = read_key().lower()
            if key == "q":
                break
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        console.print("  [cyan]Server stopped.[/cyan]")


def show_about():
    """Display the about screen."""
    about_text = (
        "[bold]SocialCleaner[/bold] v1.0\n\n"
        "Bulk-remove likes and comments from\n"
        "Instagram. Self-hosted and private —\n"
        "your data never leaves your machine.\n\n"
        "[dim]GitHub: github.com/socialcleaner[/dim]\n"
        "[dim]License: MIT[/dim]"
    )
    console.print()
    show_panel("About SocialCleaner", about_text, border_style="bright_blue")
    console.print("  [dim]Press any key to return to menu[/dim]")
    read_key()
```

- [ ] **Step 5: Test CLI launches and menu renders**

Run: `cd /Users/cyberjulio/Coding/cleaner && source venv/bin/activate && python -c "from cli.display import show_menu; print('display OK')" && python -c "from cli.app import main; print('app OK')"`

Expected: Both print OK without import errors.

- [ ] **Step 6: Commit**

```bash
git add cli/
git commit -m "feat: add CLI skeleton with main menu and rich display components"
```

---

## Chunk 2: Account Management — Interactive Browser Login

### Task 3: Implement CLI auth (browser login + account management)

**Files:**
- Create: `cli/auth.py`

- [ ] **Step 1: Create `cli/auth.py`**

```python
"""Account management — interactive browser login, list, remove."""
import uuid
import asyncio
from datetime import datetime

from rich.table import Table
from rich.spinner import Spinner
from rich.live import Live

from cli.display import console, show_menu, read_key, confirm

CLI_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0"


async def manage_accounts():
    """Account management submenu."""
    while True:
        key = show_menu(
            "Manage Accounts",
            [
                "Add Instagram Account",
                "View Connected Accounts",
                "Remove Account",
                "Back to Main Menu",
            ],
            border_style="green",
        )

        if key == "1":
            await add_instagram_account()
        elif key == "2":
            await view_accounts()
        elif key == "3":
            await remove_account()
        elif key == "4":
            return

        console.print()


async def add_instagram_account():
    """Open a visible Firefox browser for the user to log in to Instagram."""
    from playwright.async_api import async_playwright
    from backend.database import get_db
    from backend.utils.crypto import encrypt_json

    console.print("\n  [cyan]Opening Instagram login page...[/cyan]")
    console.print("  [dim]Log in normally in the browser window that opens.[/dim]")
    console.print("  [dim]The CLI will detect when you're logged in.[/dim]\n")

    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.firefox.launch(
            headless=False,
            args=["--width=1024", "--height=768"],
        )
        context = await browser.new_context(
            user_agent=CLI_USER_AGENT,
            viewport={"width": 1024, "height": 768},
            locale="en-US",
        )
        page = await context.new_page()
        await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")

        # Poll for successful login (sessionid cookie appears)
        logged_in = False
        with Live(Spinner("dots", text="Waiting for login..."), console=console, refresh_per_second=4):
            for _ in range(300):  # 10 minute timeout (300 * 2s)
                await asyncio.sleep(2)
                cookies = await context.cookies("https://www.instagram.com")
                cookie_dict = {c["name"]: c["value"] for c in cookies}
                if "sessionid" in cookie_dict:
                    logged_in = True
                    break

        if not logged_in:
            console.print("  [red]Login timed out. Please try again.[/red]")
            return

        # Extract required cookies
        required = {"sessionid", "csrftoken", "ds_user_id"}
        missing = required - set(cookie_dict.keys())
        if missing:
            console.print(f"  [red]Missing cookies: {', '.join(missing)}. Try again.[/red]")
            return

        ig_cookies = {k: cookie_dict[k] for k in required}

        # Validate session
        console.print("  [cyan]Validating session...[/cyan]")
        from backend.platforms.instagram import InstagramClient
        client = InstagramClient(context, ig_cookies)
        try:
            user_info = await client.validate_session()
            username = user_info.get("username", "unknown")
        except Exception:
            console.print("  [red]Login detected but session couldn't be verified.[/red]")
            if confirm("Try again?"):
                await client.close()
                await add_instagram_account()
            return
        finally:
            await client.close()

        # Store in database
        session_id = str(uuid.uuid4())
        cookies_enc = encrypt_json(ig_cookies)

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO sessions (id, platform, cookies_enc, user_agent, username) VALUES (?, ?, ?, ?, ?)",
                (session_id, "instagram", cookies_enc, CLI_USER_AGENT, username),
            )
            await db.commit()
        finally:
            await db.close()

        console.print(f"\n  [green]Account @{username} connected successfully![/green]")

    except KeyboardInterrupt:
        console.print("\n  [yellow]Login cancelled.[/yellow]")
    except Exception as e:
        console.print(f"\n  [red]Error: {e}[/red]")
    finally:
        if browser:
            await browser.close()
        await pw.stop()


async def view_accounts():
    """Display connected accounts in a rich table."""
    from backend.database import get_db

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, platform, username, valid, created_at FROM sessions ORDER BY created_at DESC"
        )
    finally:
        await db.close()

    if not rows:
        console.print("\n  [yellow]No accounts connected yet.[/yellow]")
        console.print("  [dim]Use 'Add Instagram Account' to get started.[/dim]")
        return

    table = Table(title="Connected Accounts", border_style="green")
    table.add_column("#", style="dim", width=3)
    table.add_column("Platform", style="cyan")
    table.add_column("Username", style="bold")
    table.add_column("Status")
    table.add_column("Added", style="dim")

    for i, row in enumerate(rows, 1):
        status = "[green]Active[/green]" if row["valid"] else "[red]Invalid[/red]"
        added = row["created_at"][:10] if row["created_at"] else "—"
        table.add_row(str(i), row["platform"].title(), f"@{row['username']}", status, added)

    console.print()
    console.print(table)


async def remove_account():
    """Remove a connected account."""
    from backend.database import get_db

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, platform, username FROM sessions ORDER BY created_at DESC"
        )
    finally:
        await db.close()

    if not rows:
        console.print("\n  [yellow]No accounts to remove.[/yellow]")
        return

    console.print("\n  [bold]Select account to remove:[/bold]\n")
    for i, row in enumerate(rows, 1):
        console.print(f"    {i}. {row['platform'].title()} — @{row['username']}")
    console.print(f"    {len(rows)+1}. Cancel")
    console.print()

    key = read_key()
    try:
        idx = int(key) - 1
    except ValueError:
        return

    if idx < 0 or idx >= len(rows):
        return

    row = rows[idx]
    if not confirm(f"Remove @{row['username']}?"):
        console.print("  [dim]Cancelled.[/dim]")
        return

    db = await get_db()
    try:
        await db.execute("DELETE FROM sessions WHERE id = ?", (row["id"],))
        await db.commit()
    finally:
        await db.close()

    console.print(f"  [green]Account @{row['username']} removed.[/green]")


async def select_account(platform: str = "instagram") -> dict | None:
    """Select an account for a task. Returns session row dict or None."""
    from backend.database import get_db

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM sessions WHERE platform = ? AND valid = 1 ORDER BY created_at DESC",
            (platform,),
        )
    finally:
        await db.close()

    if not rows:
        console.print("\n  [yellow]No Instagram accounts connected.[/yellow]")
        if confirm("Add one now?"):
            await add_instagram_account()
            # Retry after adding
            return await select_account(platform)
        return None

    if len(rows) == 1:
        console.print(f"\n  Using account [bold]@{rows[0]['username']}[/bold]")
        return dict(rows[0])

    console.print("\n  [bold]Select account:[/bold]\n")
    for i, row in enumerate(rows, 1):
        console.print(f"    {i}. @{row['username']}")
    console.print()

    key = read_key()
    try:
        idx = int(key) - 1
    except ValueError:
        return None

    if 0 <= idx < len(rows):
        return dict(rows[idx])
    return None
```

- [ ] **Step 2: Test auth module imports correctly**

Run: `cd /Users/cyberjulio/Coding/cleaner && source venv/bin/activate && python -c "from cli.auth import manage_accounts, select_account; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add cli/auth.py
git commit -m "feat: add CLI account management with interactive browser login"
```

---

## Chunk 3: Task Execution — Rich Progress TUI

### Task 4: Implement task flows with rich live progress display

**Files:**
- Create: `cli/tasks.py`

- [ ] **Step 1: Create `cli/tasks.py`**

```python
"""Task execution flows — unlike posts / delete comments with rich TUI progress."""
import asyncio
import time
import uuid
from datetime import timedelta

from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, TextColumn, Progress
from rich.text import Text
from rich.table import Table
from rich.layout import Layout

from cli.display import console, confirm, read_key
from cli.auth import select_account


class CLIEventSink:
    """Routes engine events to the rich live display instead of SSE."""

    def __init__(self):
        self.status = "Starting..."
        self.deleted = 0
        self.total = 0
        self.failed = 0
        self.latest_message = ""
        self.task_status = "pending"
        self.start_time = time.time()
        self._stop_requested = False
        self._pause_requested = False

    async def publish(self, task_id: str, event_type: str, data: dict) -> None:
        if event_type == "log":
            msg = data.get("message", "")
            self.latest_message = msg
            level = data.get("level", "info")
            if level == "error":
                self.status = f"[red]{msg[:60]}[/red]"
        elif event_type == "batch_progress":
            self.deleted = data.get("deleted", self.deleted)
            if data.get("total") is not None:
                self.total = data["total"]
        elif event_type == "scan_progress":
            self.total = data.get("found", self.total)
            self.status = f"Scanning... found {self.total} items"
        elif event_type == "scan_complete":
            self.total = data.get("total", self.total)
            self.status = "Scan complete"
        elif event_type == "task_status":
            self.task_status = data.get("status", self.task_status)
            if self.task_status == "scanning":
                self.status = "Scanning..."
            elif self.task_status == "running":
                self.status = "Running"
            elif self.task_status == "completed":
                self.status = "Completed"
            elif self.task_status == "failed":
                error = data.get("error", "Unknown error")
                self.status = f"[red]Failed: {error[:50]}[/red]"
        elif event_type == "item_deleted":
            self.deleted += 1
        elif event_type == "item_failed":
            self.failed += 1
        elif event_type == "rate_limited":
            self.status = "[yellow]Rate limited — backing off[/yellow]"
        elif event_type == "checkpoint_required":
            self.status = "[yellow]Instagram needs verification — check browser[/yellow]"

    def build_display(self, operation: str, username: str) -> Panel:
        """Build the rich panel for live display."""
        elapsed = time.time() - self.start_time
        elapsed_str = str(timedelta(seconds=int(elapsed)))

        # Speed calc
        if elapsed > 0 and self.deleted > 0:
            speed = self.deleted / (elapsed / 3600)
            speed_str = f"~{int(speed)}/hr"
        else:
            speed_str = "—"

        # Progress percentage
        if self.total > 0:
            pct = min(100, int(self.deleted / self.total * 100))
            bar_filled = pct // 5
            bar_empty = 20 - bar_filled
            bar = f"[green]{'█' * bar_filled}[/green][dim]{'░' * bar_empty}[/dim]  {pct}%"
        else:
            bar = "[dim]Scanning...[/dim]"

        op_label = "Unliked" if operation == "likes" else "Deleted"
        lines = [
            f"  Progress  {bar}",
            f"  {op_label:10s} {self.deleted} / {self.total}" if self.total > 0 else f"  {op_label:10s} {self.deleted}",
            f"  Speed     {speed_str}",
            f"  Elapsed   {elapsed_str}",
            f"  Status    {self.status}",
            "",
            f"  [dim]Latest: {self.latest_message[:55]}[/dim]" if self.latest_message else "",
            "",
            "  [dim]Press Q to stop · Press P to pause[/dim]",
        ]

        title = f"{'Unliking Posts' if operation == 'likes' else 'Deleting Comments'} · @{username}"
        return Panel("\n".join(lines), title=title, border_style="cyan", padding=(1, 1))


async def run_task_flow(target_type: str):
    """Main flow for unlike posts or delete comments."""
    from backend.database import get_db

    # Step 1: Account selection
    session = await select_account("instagram")
    if not session:
        return

    username = session["username"]

    # Step 0: Resume check — look for existing paused/pending tasks
    db = await get_db()
    try:
        existing = await db.execute_fetchall(
            "SELECT id, status, deleted, total_items FROM tasks "
            "WHERE session_id = ? AND target_type = ? AND status IN ('paused', 'pending') "
            "ORDER BY updated_at DESC LIMIT 1",
            (session["id"], target_type),
        )
    finally:
        await db.close()

    task_id = None
    if existing:
        row = existing[0]
        op = "unliked" if target_type == "likes" else "deleted"
        console.print(f"\n  You have a paused task ({row['deleted']}/{row['total_items']} {op}).")
        if confirm("Resume?"):
            task_id = row["id"]
            # Reset status to pending so engine picks it up
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE tasks SET status = 'pending', updated_at = datetime('now') WHERE id = ?",
                    (task_id,),
                )
                await db.commit()
            finally:
                await db.close()

    # Step 2: Confirmation (for new tasks)
    if not task_id:
        op_desc = "unlike Instagram posts" if target_type == "likes" else "delete Instagram comments"
        console.print(f"\n  Ready to [bold]{op_desc}[/bold] for @{username}")
        console.print("  [dim]This may take a while depending on how many items you have.[/dim]")
        if not confirm("Start?"):
            console.print("  [dim]Cancelled.[/dim]")
            return

        # Create new task in DB
        task_id = str(uuid.uuid4())
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO tasks (id, session_id, platform, target_type, status) VALUES (?, ?, ?, ?, 'pending')",
                (task_id, session["id"], "instagram", target_type),
            )
            await db.commit()
        finally:
            await db.close()

    # Step 3: Run with live progress
    sink = CLIEventSink()
    operation = target_type  # "likes" or "comments"

    from backend.worker.engine import WorkerEngine

    engine = WorkerEngine(sink=sink)
    engine._pw = None  # will be initialized in _run_task via _create_client

    # We need Playwright initialized for the engine
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    engine._pw = pw

    # Start the task
    task_future = asyncio.create_task(engine._run_task(task_id))

    # Live display loop with keyboard input
    try:
        with Live(sink.build_display(operation, username), console=console, refresh_per_second=2) as live:
            while not task_future.done():
                # Non-blocking key check
                key = await _check_key_async()
                if key == "q":
                    # Graceful stop
                    db = await get_db()
                    try:
                        await db.execute(
                            "UPDATE tasks SET status = 'paused', updated_at = datetime('now') WHERE id = ?",
                            (task_id,),
                        )
                        await db.commit()
                    finally:
                        await db.close()
                    console.print("\n  [yellow]Stopping... saving progress.[/yellow]")
                    task_future.cancel()
                    try:
                        await task_future
                    except asyncio.CancelledError:
                        pass
                    break
                elif key == "p":
                    if sink._pause_requested:
                        sink._pause_requested = False
                        db = await get_db()
                        try:
                            await db.execute(
                                "UPDATE tasks SET status = 'running', updated_at = datetime('now') WHERE id = ?",
                                (task_id,),
                            )
                            await db.commit()
                        finally:
                            await db.close()
                        sink.status = "Resumed"
                    else:
                        sink._pause_requested = True
                        db = await get_db()
                        try:
                            await db.execute(
                                "UPDATE tasks SET status = 'paused', updated_at = datetime('now') WHERE id = ?",
                                (task_id,),
                            )
                            await db.commit()
                        finally:
                            await db.close()
                        sink.status = "[yellow]Paused[/yellow]"

                live.update(sink.build_display(operation, username))
                await asyncio.sleep(0.5)

    except KeyboardInterrupt:
        # Ctrl+C = graceful stop (same as Q)
        db = await get_db()
        try:
            await db.execute(
                "UPDATE tasks SET status = 'paused', updated_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
            await db.commit()
        finally:
            await db.close()
        task_future.cancel()
        try:
            await task_future
        except asyncio.CancelledError:
            pass
        console.print("\n  [yellow]Stopped. Progress saved.[/yellow]")
    finally:
        await pw.stop()

    # Step 4: Completion summary
    if sink.task_status == "completed":
        elapsed = time.time() - sink.start_time
        elapsed_str = str(timedelta(seconds=int(elapsed)))
        op = "Unliked" if target_type == "likes" else "Deleted"
        console.print(f"\n  [green]Done! {op} {sink.deleted} items in {elapsed_str}.[/green]")
        if sink.deleted >= 800:
            console.print("  [yellow]Daily cap reached (800 actions today). Run again tomorrow to continue.[/yellow]")
    elif sink.task_status == "failed":
        console.print(f"\n  [red]Task failed. {sink.latest_message}[/red]")
    elif sink.task_status not in ("completed", "failed"):
        console.print(f"\n  [cyan]Progress saved. You can resume this task later.[/cyan]")


async def _check_key_async() -> str | None:
    """Non-blocking key check using asyncio."""
    import sys
    import select

    if select.select([sys.stdin], [], [], 0)[0]:
        import tty
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == '\x03':
                raise KeyboardInterrupt
            return ch.lower()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return None
```

- [ ] **Step 2: Test task module imports correctly**

Run: `cd /Users/cyberjulio/Coding/cleaner && source venv/bin/activate && python -c "from cli.tasks import run_task_flow, CLIEventSink; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add cli/tasks.py
git commit -m "feat: add CLI task execution with rich live progress display"
```

---

## Chunk 4: Add rich dependency + Documentation + Final Integration

### Task 5: Add rich dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add rich to requirements.txt**

Append `rich>=13.0` to `requirements.txt`.

- [ ] **Step 2: Install it**

Run: `cd /Users/cyberjulio/Coding/cleaner && source venv/bin/activate && pip install rich`

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "deps: add rich for CLI TUI components"
```

### Task 6: Update documentation

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update README.md**

Add a CLI section alongside existing web usage docs. Include:
- How to launch: `python -m cli`
- Menu overview
- How to add an account (browser login flow)
- How to unlike posts / delete comments
- How to launch the web dashboard from CLI

- [ ] **Step 2: Update CLAUDE.md**

Add `cli/` to the Key Structure section:
```
cli/
  __main__.py      # Entry point (python -m cli)
  app.py           # Main menu loop + rich console setup
  auth.py          # Interactive browser login + account management
  tasks.py         # Task execution + rich progress display
  display.py       # Shared rich components (menus, panels, keypress)
```

Add note about `TaskEventSink` protocol in Architecture Notes.

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: add CLI usage instructions to README and CLAUDE.md"
```

### Task 7: End-to-end testing

- [ ] **Step 1: Test CLI launches with menu**

Run: `cd /Users/cyberjulio/Coding/cleaner && source venv/bin/activate && timeout 5 python -m cli || true`

Verify: Banner and menu render correctly.

- [ ] **Step 2: Test single-keypress navigation**

Manual test: launch CLI, press 5 (About), verify about screen, press any key, verify return to menu, press 6 (Quit).

- [ ] **Step 3: Test "View Connected Accounts"**

Launch CLI, press 4 (Manage Accounts), press 2 (View), verify table renders (empty or with existing accounts).

- [ ] **Step 4: Test "Start Web Dashboard"**

Launch CLI, press 1, verify server starts and browser opens to `http://127.0.0.1:8000`, press Q to stop.

- [ ] **Step 5: Test "Add Instagram Account"**

Launch CLI, press 4, press 1, verify Firefox opens to Instagram login page. (Manual login needed — flag to user.)

- [ ] **Step 6: Test unlike/delete flows**

Requires a connected account. If one exists, launch flow and verify progress TUI renders. (Manual — flag to user.)
