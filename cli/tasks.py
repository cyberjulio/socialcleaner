"""Task execution flows — unlike posts / delete comments with rich TUI progress."""
import asyncio
import json
import select
import sys
import termios
import time
import tty
import uuid
from datetime import timedelta

from rich.live import Live
from rich.panel import Panel

from cli.display import console, confirm
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
        self.daily_cap_hit = False
        self.start_time = time.time()

    async def publish(self, task_id: str, event_type: str, data: dict) -> None:
        # Persist log events to DB so they appear on the web dashboard
        if event_type == "log":
            try:
                from backend.database import get_db
                db = await get_db()
                try:
                    await db.execute(
                        "INSERT INTO events (task_id, event_type, payload) VALUES (?, ?, ?)",
                        (task_id, event_type, json.dumps(data)),
                    )
                    await db.commit()
                finally:
                    await db.close()
            except Exception:
                pass

        if event_type == "log":
            msg = data.get("message", "")
            self.latest_message = msg
            if "daily cap reached" in msg.lower():
                self.daily_cap_hit = True
                self.status = "[yellow]Daily cap reached[/yellow]"
            elif data.get("level") == "error":
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
                self.status = "[green]Completed[/green]"
            elif self.task_status == "failed":
                error = data.get("error", "Unknown error")
                self.status = f"[red]Failed: {error[:50]}[/red]"
        elif event_type == "item_deleted":
            pass  # batch_progress already tracks this
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

        if elapsed > 0 and self.deleted > 0:
            speed = self.deleted / (elapsed / 3600)
            speed_str = f"~{int(speed)}/hr"
        else:
            speed_str = "—"

        if self.total > 0:
            pct = min(100, int(self.deleted / self.total * 100))
            bar_filled = pct // 5
            bar_empty = 20 - bar_filled
            bar = f"[green]{'█' * bar_filled}[/green][dim]{'░' * bar_empty}[/dim]  {pct}%"
        else:
            bar = "[dim]Scanning...[/dim]"

        op_label = "Unliked" if operation == "likes" else "Deleted"
        count_line = (
            f"  {op_label:10s} {self.deleted} / {self.total}"
            if self.total > 0
            else f"  {op_label:10s} {self.deleted}"
        )

        lines = [
            f"  Progress  {bar}",
            count_line,
            f"  Speed     {speed_str}",
            f"  Elapsed   {elapsed_str}",
            f"  Status    {self.status}",
            "",
            f"  [dim]Latest: {self.latest_message[:55]}[/dim]"
            if self.latest_message
            else "",
            "",
            "  [dim]Press Q to stop · Press P to pause[/dim]",
        ]

        title = (
            f"{'Unliking Posts' if operation == 'likes' else 'Deleting Comments'}"
            f" · @{username}"
        )
        return Panel(
            "\n".join(lines), title=title, border_style="cyan", padding=(1, 1)
        )


async def run_task_flow(target_type: str):
    """Main flow for unlike posts or delete comments."""
    from backend.database import get_db

    # Step 1: Account selection
    session = await select_account("instagram")
    if not session:
        return

    username = session["username"]

    # Step 0: Resume check
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
        console.print(
            f"\n  You have a paused task ({row['deleted']}/{row['total_items']} {op})."
        )
        if confirm("Resume?"):
            task_id = row["id"]
            db = await get_db()
            try:
                await db.execute(
                    "UPDATE tasks SET status = 'pending', updated_at = datetime('now') WHERE id = ?",
                    (task_id,),
                )
                await db.commit()
            finally:
                await db.close()

    # Step 2: Confirmation (new tasks)
    if not task_id:
        op_desc = (
            "unlike Instagram posts"
            if target_type == "likes"
            else "delete Instagram comments"
        )
        console.print(f"\n  Ready to [bold]{op_desc}[/bold] for @{username}")
        console.print(
            "  [dim]This may take a while depending on how many items you have.[/dim]"
        )
        if not confirm("Start?"):
            console.print("  [dim]Cancelled.[/dim]")
            return

        task_id = str(uuid.uuid4())
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO tasks (id, session_id, platform, target_type, status) "
                "VALUES (?, ?, ?, ?, 'pending')",
                (task_id, session["id"], "instagram", target_type),
            )
            await db.commit()
        finally:
            await db.close()

    # Step 3: Run with live progress
    sink = CLIEventSink()

    from backend.worker.engine import WorkerEngine
    from playwright.async_api import async_playwright

    engine = WorkerEngine(sink=sink)
    pw = await async_playwright().start()
    engine._pw = pw

    task_future = asyncio.create_task(engine._run_task(task_id))

    try:
        with Live(
            sink.build_display(target_type, username),
            console=console,
            refresh_per_second=2,
        ) as live:
            while not task_future.done():
                key = await _check_key_async()
                if key == "q":
                    await _pause_task(task_id)
                    console.print("\n  [yellow]Stopping... saving progress.[/yellow]")
                    task_future.cancel()
                    try:
                        await task_future
                    except asyncio.CancelledError:
                        pass
                    break
                elif key == "p":
                    await _toggle_pause(task_id, sink)

                live.update(sink.build_display(target_type, username))
                await asyncio.sleep(0.5)

    except KeyboardInterrupt:
        await _pause_task(task_id)
        task_future.cancel()
        try:
            await task_future
        except asyncio.CancelledError:
            pass
        console.print("\n  [yellow]Stopped. Progress saved.[/yellow]")
    finally:
        await pw.stop()

    # Step 4: Completion summary
    _show_summary(sink, target_type)
    console.print("\n  [dim]Press any key to return to menu[/dim]")
    from cli.display import read_key
    read_key()


async def _pause_task(task_id: str):
    """Mark task as paused in DB."""
    from backend.database import get_db

    db = await get_db()
    try:
        await db.execute(
            "UPDATE tasks SET status = 'paused', updated_at = datetime('now') WHERE id = ?",
            (task_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def _toggle_pause(task_id: str, sink: CLIEventSink):
    """Toggle pause/resume for a task."""
    from backend.database import get_db

    db = await get_db()
    try:
        current = await db.execute_fetchall(
            "SELECT status FROM tasks WHERE id = ?", (task_id,)
        )
        if current and current[0]["status"] == "paused":
            await db.execute(
                "UPDATE tasks SET status = 'running', updated_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
            sink.status = "Resumed"
        else:
            await db.execute(
                "UPDATE tasks SET status = 'paused', updated_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
            sink.status = "[yellow]Paused[/yellow]"
        await db.commit()
    finally:
        await db.close()


def _show_summary(sink: CLIEventSink, target_type: str):
    """Show completion summary."""
    if sink.daily_cap_hit:
        op = "Unliked" if target_type == "likes" else "Deleted"
        console.print(
            f"\n  [yellow]Daily cap reached (800 actions today).[/yellow]"
        )
        if sink.deleted > 0:
            console.print(f"  [green]{op} {sink.deleted} items this session.[/green]")
        console.print("  [dim]Run again tomorrow to continue.[/dim]")
    elif sink.task_status == "completed":
        elapsed = time.time() - sink.start_time
        elapsed_str = str(timedelta(seconds=int(elapsed)))
        op = "Unliked" if target_type == "likes" else "Deleted"
        console.print(
            f"\n  [green]Done! {op} {sink.deleted} items in {elapsed_str}.[/green]"
        )
    elif sink.task_status == "failed":
        console.print(f"\n  [red]Task failed. {sink.latest_message}[/red]")
    elif sink.task_status not in ("completed", "failed"):
        console.print(
            "\n  [cyan]Progress saved. You can resume this task later.[/cyan]"
        )


async def _check_key_async() -> str | None:
    """Non-blocking key check using asyncio."""
    if select.select([sys.stdin], [], [], 0)[0]:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x03":
                raise KeyboardInterrupt
            return ch.lower()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return None
