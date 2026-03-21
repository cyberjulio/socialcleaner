import asyncio
import json
import logging
import os
import random
import signal
import subprocess
import uuid
from datetime import datetime
from typing import Protocol

from playwright.async_api import async_playwright, Browser

from backend.config import settings
from backend.database import get_db
from backend.platforms.base import PlatformClient, DailyCapReached
from backend.platforms.instagram import InstagramClient
from backend.platforms.twitter import TwitterClient
from backend.platforms.user_agents import USER_AGENTS
from backend.utils.crypto import decrypt_json
from backend.utils.events import event_bus
from backend.worker.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class TaskEventSink(Protocol):
    """Protocol for receiving task events. EventBus and CLI sink both satisfy this."""
    async def publish(self, task_id: str, event_type: str, data: dict) -> None: ...


class WorkerEngine:
    def __init__(self, sink: TaskEventSink | None = None):
        self._browser: Browser | None = None
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._pw = None
        self._sink: TaskEventSink = sink or event_bus

    @staticmethod
    def _cleanup_orphaned_browsers():
        """Kill orphaned Playwright-launched headless browser processes from previous runs.
        Only targets browsers spawned from the Playwright cache directory to avoid
        killing the user's system Firefox or Chromium."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", "ms-playwright"],
                capture_output=True, text=True
            )
            pids = result.stdout.strip().split("\n")
            my_pid = os.getpid()
            killed = 0
            for pid_str in pids:
                if not pid_str:
                    continue
                pid = int(pid_str)
                if pid != my_pid:
                    try:
                        os.kill(pid, signal.SIGTERM)
                        killed += 1
                    except ProcessLookupError:
                        pass
            if killed:
                logger.info(f"Cleaned up {killed} orphaned Playwright browser process(es)")
        except Exception as e:
            logger.debug(f"Browser cleanup check: {e}")

    async def start(self):
        """Initialize the browser and resume any interrupted tasks."""
        self._cleanup_orphaned_browsers()

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)

        # Resume interrupted tasks
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "UPDATE tasks SET status = 'pending' WHERE status = 'running' OR status = 'scanning'"
            )
            await db.commit()
            pending = await db.execute_fetchall(
                "SELECT id FROM tasks WHERE status = 'pending'"
            )
            for row in pending:
                self.schedule_task(row["id"])
        finally:
            await db.close()

    def cancel_task(self, task_id: str):
        """Cancel a running task immediately."""
        asyncio_task = self._running_tasks.get(task_id)
        if asyncio_task:
            asyncio_task.cancel()
            logger.info(f"Cancelled asyncio task for {task_id}")

    async def stop(self):
        """Gracefully shutdown."""
        for task_id, asyncio_task in self._running_tasks.items():
            asyncio_task.cancel()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    def schedule_task(self, task_id: str):
        """Schedule a task for execution."""
        if task_id not in self._running_tasks:
            t = asyncio.create_task(self._run_task(task_id))
            self._running_tasks[task_id] = t
            t.add_done_callback(lambda _: self._running_tasks.pop(task_id, None))

    async def _create_client(self, session_row) -> tuple[PlatformClient, "Browser"]:
        """Create a platform client with a FRESH browser (matches auth flow)."""
        cookies_dict = decrypt_json(session_row["cookies_enc"])
        platform = session_row["platform"]

        # Use the same user-agent captured from the user's real browser
        user_agent = session_row["user_agent"] or random.choice(USER_AGENTS)
        logger.info(f"Using user-agent: {user_agent[:80]}...")

        # Use Firefox if the session was created with Firefox, otherwise Chromium
        is_firefox = "Firefox" in user_agent or "Gecko" in user_agent
        browser_type = self._pw.firefox if is_firefox else self._pw.chromium
        logger.info(f"Launching {'Firefox' if is_firefox else 'Chromium'} (headless)")
        task_browser = await browser_type.launch(headless=True)
        context = await task_browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )

        # Inject cookies into the browser context
        domain = ".instagram.com" if platform == "instagram" else ".x.com"
        browser_cookies = []
        for name, value in cookies_dict.items():
            browser_cookies.append({
                "name": name,
                "value": value,
                "domain": domain,
                "path": "/",
                "httpOnly": name not in ("csrftoken", "ct0"),
                "secure": True,
                "sameSite": "None",
            })
        await context.add_cookies(browser_cookies)
        logger.info(f"Injected {len(browser_cookies)} cookies for domain {domain}")

        session_id = session_row["id"]
        if platform == "instagram":
            return InstagramClient(context, cookies_dict, session_id=session_id), task_browser
        elif platform == "twitter":
            return TwitterClient(context, cookies_dict), task_browser
        else:
            raise ValueError(f"Unknown platform: {platform}")

    async def _run_task(self, task_id: str):
        """Execute a cleaning task."""
        db = await get_db()
        client = None
        task_browser = None
        try:
            # Load task and session
            task = await db.execute_fetchall(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            )
            if not task:
                return
            task = task[0]

            session = await db.execute_fetchall(
                "SELECT * FROM sessions WHERE id = ?", (task["session_id"],)
            )
            if not session:
                return
            session = session[0]

            # Brief delay to let SSE stream connect before we start emitting events
            await asyncio.sleep(2)

            client, task_browser = await self._create_client(session)

            # Wire up live logging to SSE
            async def log_to_frontend(message, level="info"):
                await self._sink.publish(task_id, "log", {"message": message, "level": level})
            client.set_log_callback(log_to_frontend)

            # Wire up batch progress reporting
            async def report_progress(deleted, total=None):
                progress_db = await get_db()
                try:
                    if total is not None:
                        await progress_db.execute(
                            "UPDATE tasks SET deleted = ?, total_items = ?, updated_at = datetime('now') WHERE id = ?",
                            (deleted, total, task_id),
                        )
                    else:
                        await progress_db.execute(
                            "UPDATE tasks SET deleted = ?, updated_at = datetime('now') WHERE id = ?",
                            (deleted, task_id),
                        )
                    await progress_db.commit()
                    await self._sink.publish(task_id, "batch_progress", {"deleted": deleted, "total": total})
                finally:
                    await progress_db.close()
            client.set_progress_callback(report_progress)

            # Wire up generic event emitter for custom SSE events (e.g. ETA)
            async def emit_event(event_type, data):
                await self._sink.publish(task_id, event_type, data)
            client.set_event_callback(emit_event)

            rate_limiter = RateLimiter(task["platform"])

            # Phase 1: Scan for items (if not already scanned)
            existing_items = await db.execute_fetchall(
                "SELECT COUNT(*) as cnt FROM items WHERE task_id = ?", (task_id,)
            )
            if existing_items[0]["cnt"] == 0:
                await db.execute(
                    "UPDATE tasks SET status = 'scanning', updated_at = datetime('now') WHERE id = ?",
                    (task_id,),
                )
                await db.commit()
                await self._sink.publish(task_id, "task_status", {"status": "scanning"})

                count = 0
                await self._sink.publish(task_id, "log", {"message": f"Starting scan for {task['target_type']} on {task['platform']}..."})
                try:
                    async for item_data in client.fetch_items(task["target_type"]):
                        # Check for cancellation during scan
                        current = await db.execute_fetchall(
                            "SELECT status FROM tasks WHERE id = ?", (task_id,)
                        )
                        if current and current[0]["status"] in ("paused", "cancelled"):
                            await self._sink.publish(task_id, "log", {"message": f"Task {current[0]['status']} during scan"})
                            if hasattr(client, 'cancel'):
                                client.cancel()
                            break

                        item_id = str(uuid.uuid4())
                        await db.execute(
                            "INSERT INTO items (id, task_id, platform_id, item_type, metadata) VALUES (?, ?, ?, ?, ?)",
                            (item_id, task_id, item_data["platform_id"], item_data["item_type"], item_data.get("metadata")),
                        )
                        count += 1
                        # Human-friendly log: skip internal batch IDs
                        if not item_data["platform_id"].startswith("batch_"):
                            await self._sink.publish(task_id, "log", {"message": f"Found item #{count}: {item_data['platform_id']}"})
                        if count % 20 == 0:
                            await db.commit()
                            await self._sink.publish(task_id, "scan_progress", {"found": count})

                        # Small delay between pagination requests
                        await asyncio.sleep(random.uniform(1, 3))
                except asyncio.CancelledError:
                    await self._sink.publish(task_id, "log", {"message": "Task cancelled"})
                except Exception as scan_err:
                    logger.error(f"Scan error: {scan_err}", exc_info=True)
                    await self._sink.publish(task_id, "log", {"message": f"SCAN ERROR: {scan_err}", "level": "error"})

                if count > 1:
                    await self._sink.publish(task_id, "log", {"message": f"Scan complete. Found {count} items."})

                await db.execute(
                    "UPDATE tasks SET total_items = ?, updated_at = datetime('now') WHERE id = ?",
                    (count, task_id),
                )
                await db.commit()
                await self._sink.publish(task_id, "scan_complete", {"total": count})

            # Phase 2: Delete items
            await db.execute(
                "UPDATE tasks SET status = 'running', updated_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
            await db.commit()
            await self._sink.publish(task_id, "task_status", {"status": "running"})

            while True:
                # Check if task is paused or cancelled
                current = await db.execute_fetchall(
                    "SELECT status FROM tasks WHERE id = ?", (task_id,)
                )
                if not current or current[0]["status"] in ("paused", "cancelled"):
                    logger.info(f"Task {task_id} is {current[0]['status']}, stopping")
                    return

                # Get next pending item
                items = await db.execute_fetchall(
                    "SELECT * FROM items WHERE task_id = ? AND status = 'pending' ORDER BY created_at LIMIT 1",
                    (task_id,),
                )
                if not items:
                    break

                item = items[0]

                try:
                    success = await client.delete_item(dict(item))
                    if success:
                        await db.execute(
                            "UPDATE items SET status = 'deleted', deleted_at = datetime('now') WHERE id = ?",
                            (item["id"],),
                        )
                        await db.execute(
                            "UPDATE tasks SET deleted = deleted + 1, updated_at = datetime('now') WHERE id = ?",
                            (task_id,),
                        )
                        await self._sink.publish(task_id, "item_deleted", {
                            "item_id": item["id"],
                            "platform_id": item["platform_id"],
                        })
                    else:
                        attempts = item["attempts"] + 1
                        if attempts >= 3:
                            await db.execute(
                                "UPDATE items SET status = 'failed', attempts = ?, last_error = 'max retries' WHERE id = ?",
                                (attempts, item["id"]),
                            )
                            await db.execute(
                                "UPDATE tasks SET failed = failed + 1, updated_at = datetime('now') WHERE id = ?",
                                (task_id,),
                            )
                            await self._sink.publish(task_id, "item_failed", {
                                "item_id": item["id"],
                                "reason": "max retries",
                            })
                        else:
                            await db.execute(
                                "UPDATE items SET attempts = ? WHERE id = ?",
                                (attempts, item["id"]),
                            )

                except (DailyCapReached, asyncio.CancelledError):
                    raise
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Error deleting item {item['id']}: {error_msg}")

                    if "429" in error_msg or "rate" in error_msg.lower():
                        rate_limiter.on_rate_limit()
                        await self._sink.publish(task_id, "rate_limited", {"message": "Rate limited, backing off"})
                    elif "checkpoint" in error_msg.lower():
                        rate_limiter.on_checkpoint_required()
                        await self._sink.publish(task_id, "checkpoint_required", {
                            "message": "Platform requires verification. Please verify in your app."
                        })
                    else:
                        attempts = item["attempts"] + 1
                        if attempts >= 3:
                            await db.execute(
                                "UPDATE items SET status = 'failed', attempts = ?, last_error = ? WHERE id = ?",
                                (attempts, error_msg[:500], item["id"]),
                            )
                            await db.execute(
                                "UPDATE tasks SET failed = failed + 1, updated_at = datetime('now') WHERE id = ?",
                                (task_id,),
                            )
                        else:
                            await db.execute(
                                "UPDATE items SET attempts = ?, last_error = ? WHERE id = ?",
                                (attempts, error_msg[:500], item["id"]),
                            )

                await db.commit()
                await rate_limiter.wait()

            # Task complete
            await db.execute(
                "UPDATE tasks SET status = 'completed', updated_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
            await db.commit()
            await self._sink.publish(task_id, "task_status", {"status": "completed"})
            logger.info(f"Task {task_id} completed")

        except asyncio.CancelledError:
            logger.info(f"Task {task_id} cancelled")
        except DailyCapReached:
            logger.info(f"Task {task_id} hit daily cap — marking completed")
            await db.execute(
                "UPDATE tasks SET status = 'completed', updated_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
            await db.commit()
            await self._sink.publish(task_id, "task_status", {"status": "completed"})
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}", exc_info=True)
            await db.execute(
                "UPDATE tasks SET status = 'failed', updated_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
            await db.commit()
            await self._sink.publish(task_id, "task_status", {"status": "failed", "error": str(e)})
        finally:
            if client:
                await client.close()
            if task_browser:
                await task_browser.close()
            await db.close()


# Singleton
worker_engine = WorkerEngine()
