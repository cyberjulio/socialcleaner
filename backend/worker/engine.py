import asyncio
import json
import logging
import random
import uuid
from datetime import datetime

from playwright.async_api import async_playwright, Browser

from backend.config import settings
from backend.database import get_db
from backend.platforms.base import PlatformClient
from backend.platforms.instagram import InstagramClient
from backend.platforms.twitter import TwitterClient
from backend.platforms.user_agents import USER_AGENTS
from backend.utils.crypto import decrypt_json
from backend.utils.events import event_bus
from backend.worker.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class WorkerEngine:
    def __init__(self):
        self._browser: Browser | None = None
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._pw = None

    async def start(self):
        """Initialize the browser and resume any interrupted tasks."""
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

        # Create a fresh browser per task — same as auth flow
        # This avoids stale state from the persistent browser
        task_browser = await self._pw.chromium.launch(headless=True)
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

        if platform == "instagram":
            return InstagramClient(context, cookies_dict), task_browser
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
                await event_bus.publish(task_id, "log", {"message": message, "level": level})
            client.set_log_callback(log_to_frontend)

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
                await event_bus.publish(task_id, "task_status", {"status": "scanning"})

                count = 0
                await event_bus.publish(task_id, "log", {"message": f"Starting scan for {task['target_type']} on {task['platform']}..."})
                try:
                    async for item_data in client.fetch_items(task["target_type"]):
                        item_id = str(uuid.uuid4())
                        await db.execute(
                            "INSERT INTO items (id, task_id, platform_id, item_type, metadata) VALUES (?, ?, ?, ?, ?)",
                            (item_id, task_id, item_data["platform_id"], item_data["item_type"], item_data.get("metadata")),
                        )
                        count += 1
                        await event_bus.publish(task_id, "log", {"message": f"Found item #{count}: {item_data['platform_id']}"})
                        if count % 20 == 0:
                            await db.commit()
                            await event_bus.publish(task_id, "scan_progress", {"found": count})

                        # Small delay between pagination requests
                        await asyncio.sleep(random.uniform(1, 3))
                except Exception as scan_err:
                    logger.error(f"Scan error: {scan_err}", exc_info=True)
                    await event_bus.publish(task_id, "log", {"message": f"SCAN ERROR: {scan_err}", "level": "error"})

                await event_bus.publish(task_id, "log", {"message": f"Scan complete. Found {count} items."})

                await db.execute(
                    "UPDATE tasks SET total_items = ?, updated_at = datetime('now') WHERE id = ?",
                    (count, task_id),
                )
                await db.commit()
                await event_bus.publish(task_id, "scan_complete", {"total": count})

            # Phase 2: Delete items
            await db.execute(
                "UPDATE tasks SET status = 'running', updated_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
            await db.commit()
            await event_bus.publish(task_id, "task_status", {"status": "running"})

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
                        await event_bus.publish(task_id, "item_deleted", {
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
                            await event_bus.publish(task_id, "item_failed", {
                                "item_id": item["id"],
                                "reason": "max retries",
                            })
                        else:
                            await db.execute(
                                "UPDATE items SET attempts = ? WHERE id = ?",
                                (attempts, item["id"]),
                            )

                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Error deleting item {item['id']}: {error_msg}")

                    if "429" in error_msg or "rate" in error_msg.lower():
                        rate_limiter.on_rate_limit()
                        await event_bus.publish(task_id, "rate_limited", {"message": "Rate limited, backing off"})
                    elif "checkpoint" in error_msg.lower():
                        rate_limiter.on_checkpoint_required()
                        await event_bus.publish(task_id, "checkpoint_required", {
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
            await event_bus.publish(task_id, "task_status", {"status": "completed"})
            logger.info(f"Task {task_id} completed")

        except asyncio.CancelledError:
            logger.info(f"Task {task_id} cancelled")
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}", exc_info=True)
            await db.execute(
                "UPDATE tasks SET status = 'failed', updated_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
            await db.commit()
            await event_bus.publish(task_id, "task_status", {"status": "failed", "error": str(e)})
        finally:
            if client:
                await client.close()
            if task_browser:
                await task_browser.close()
            await db.close()


# Singleton
worker_engine = WorkerEngine()
