import json
import math
import re
import logging
import asyncio
import random
import time
from typing import AsyncIterator
from playwright.async_api import BrowserContext, Page
from backend.platforms.base import PlatformClient

logger = logging.getLogger(__name__)

IG_BASE = "https://www.instagram.com"

# ── Rate limiting constants (tuned from community research) ────────────
BATCH_SIZE_MIN = 20
BATCH_SIZE_MAX = 25
INTER_BATCH_DELAY_MIN = 20   # seconds
INTER_BATCH_DELAY_MAX = 45
CLICK_DELAY_MIN = 200         # ms between checkbox/thumbnail clicks
CLICK_DELAY_MAX = 600
READING_PAUSE_CHANCE = 0.20   # 20% chance of a "reading pause" per batch
READING_PAUSE_MIN = 3         # seconds
READING_PAUSE_MAX = 8
SESSION_ACTIVE_MINUTES = 50   # max active time before session rest
SESSION_REST_MIN = 30         # minutes
SESSION_REST_MAX = 45
DAILY_CAP = 800               # max actions per 24h rolling window
ACTION_BLOCK_COOLDOWN_H = 24  # hours to pause on action block


class InstagramClient(PlatformClient):
    def __init__(self, context: BrowserContext, cookies: dict[str, str]):
        super().__init__(context, cookies)
        self.csrf_token = cookies.get("csrftoken", "")
        self.user_id = cookies.get("ds_user_id", "")
        self._cancelled = False
        # Rate limiting state
        self._session_start = time.time()
        self._session_actions = 0
        self._daily_actions = 0
        self._daily_reset = time.time()

    def cancel(self):
        self._cancelled = True

    async def _check_cancelled(self):
        if self._cancelled:
            raise asyncio.CancelledError("Task cancelled by user")

    # ── Rate limiting helpers ─────────────────────────────────────────

    def _estimate_duration(self, total_items: int) -> str:
        """Calculate human-readable ETA for a task."""
        avg_batch = (BATCH_SIZE_MIN + BATCH_SIZE_MAX) / 2
        avg_batch_time_s = (
            avg_batch * (CLICK_DELAY_MIN + CLICK_DELAY_MAX) / 2 / 1000  # click delays
            + 1.5   # Select click + wait
            + 2.0   # Delete/Unlike click + wait
            + 3.0   # Confirm click + wait
            + 3.0   # Page reload
            + (INTER_BATCH_DELAY_MIN + INTER_BATCH_DELAY_MAX) / 2  # inter-batch delay
            + READING_PAUSE_CHANCE * (READING_PAUSE_MIN + READING_PAUSE_MAX) / 2  # avg reading pause
        )
        total_batches = math.ceil(total_items / avg_batch)
        active_time_s = total_batches * avg_batch_time_s

        # Session rests: every SESSION_ACTIVE_MINUTES, rest for avg 37.5 min
        active_per_session = SESSION_ACTIVE_MINUTES * 60
        sessions_needed = math.ceil(active_time_s / active_per_session)
        avg_rest = (SESSION_REST_MIN + SESSION_REST_MAX) / 2 * 60
        rest_time_s = max(0, sessions_needed - 1) * avg_rest

        # Daily cap
        days_needed = math.ceil(total_items / DAILY_CAP)

        if days_needed <= 1:
            total_time_s = active_time_s + rest_time_s
            if total_time_s < 3600:
                return f"~{int(total_time_s / 60)} minutes"
            return f"~{total_time_s / 3600:.1f} hours"

        return f"~{days_needed} days (processing ~{DAILY_CAP} items/day)"

    async def _check_action_blocked(self, page: Page) -> bool:
        """Check if Instagram is showing an action block message."""
        blocked = await page.evaluate("""
            () => {
                const text = document.body?.innerText || '';
                const lower = text.toLowerCase();
                return lower.includes('try again later') ||
                       lower.includes('action blocked') ||
                       lower.includes('we restrict certain activity') ||
                       lower.includes('temporarily blocked') ||
                       lower.includes('tente novamente mais tarde') ||
                       lower.includes('ação bloqueada');
            }
        """)
        return blocked

    async def _handle_action_block(self):
        """Pause for ACTION_BLOCK_COOLDOWN_H hours when blocked."""
        cooldown_s = ACTION_BLOCK_COOLDOWN_H * 3600
        await self._log(
            f"Action blocked by Instagram. Pausing for {ACTION_BLOCK_COOLDOWN_H}h to avoid escalation.",
            "error",
        )
        for i in range(0, cooldown_s, 60):
            await self._check_cancelled()
            remaining_h = (cooldown_s - i) / 3600
            if i % 1800 == 0:  # log every 30 min
                await self._log(f"Action block cooldown: {remaining_h:.1f}h remaining", "warn")
            await asyncio.sleep(60)
        await self._log("Action block cooldown complete. Resuming...")

    async def _reading_pause(self):
        """Random 'reading pause' — 20% chance, 3-8 seconds."""
        if random.random() < READING_PAUSE_CHANCE:
            pause = random.uniform(READING_PAUSE_MIN, READING_PAUSE_MAX)
            await asyncio.sleep(pause)

    async def _inter_batch_delay(self):
        """Random delay between batches (20-45 seconds)."""
        delay = random.uniform(INTER_BATCH_DELAY_MIN, INTER_BATCH_DELAY_MAX)
        await asyncio.sleep(delay)

    async def _check_session_limits(self):
        """Enforce session time limits and daily cap. Returns False if daily cap hit and reset waited."""
        await self._check_cancelled()

        # Daily cap check
        if time.time() - self._daily_reset > 86400:
            self._daily_actions = 0
            self._daily_reset = time.time()

        if self._daily_actions >= DAILY_CAP:
            wait_s = 86400 - (time.time() - self._daily_reset)
            wait_h = max(wait_s, 3600) / 3600
            await self._log(
                f"Daily cap reached ({DAILY_CAP} actions). Waiting {wait_h:.1f}h until next window.",
                "warn",
            )
            # Wait in 5-minute increments so cancel can interrupt
            for i in range(0, int(max(wait_s, 3600)), 300):
                await self._check_cancelled()
                remaining_h = (max(wait_s, 3600) - i) / 3600
                if i % 1800 == 0:
                    await self._log(f"Daily cap wait: {remaining_h:.1f}h remaining")
                await asyncio.sleep(300)
            self._daily_actions = 0
            self._daily_reset = time.time()
            self._session_start = time.time()
            self._session_actions = 0

        # Session time limit check
        session_elapsed = (time.time() - self._session_start) / 60
        if session_elapsed >= SESSION_ACTIVE_MINUTES:
            rest_min = random.uniform(SESSION_REST_MIN, SESSION_REST_MAX)
            await self._log(
                f"Session active for {session_elapsed:.0f}min. Resting for {rest_min:.0f} minutes...",
                "warn",
            )
            rest_s = int(rest_min * 60)
            for i in range(0, rest_s, 30):
                await self._check_cancelled()
                remaining = rest_s - i
                if i % 300 == 0:
                    await self._log(f"Session rest: {remaining // 60}min remaining")
                await asyncio.sleep(min(30, remaining))
            # Reset session counters
            self._session_start = time.time()
            self._session_actions = 0
            await self._log("Session rest complete. Resuming...")

    def _record_actions(self, count: int):
        """Record that `count` actions were performed."""
        self._session_actions += count
        self._daily_actions += count

    async def _new_page(self) -> Page:
        """Create a new page with IG session loaded."""
        page = await self.context.new_page()
        await page.goto(IG_BASE, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        return page

    async def validate_session(self) -> dict:
        page = await self._new_page()
        try:
            result = await page.evaluate(f"""
                async () => {{
                    try {{
                        const r = await fetch('/api/v1/users/{self.user_id}/info/', {{
                            headers: {{
                                'X-CSRFToken': '{self.csrf_token}',
                                'X-IG-App-ID': '936619743392459',
                                'X-Requested-With': 'XMLHttpRequest'
                            }}
                        }});
                        const data = await r.json();
                        return data?.user?.username || null;
                    }} catch {{ return null; }}
                }}
            """)
            username = result or "unknown"
            logger.info(f"validate_session: username={username}")
            return {"username": username, "user_id": self.user_id}
        finally:
            await page.close()

    async def fetch_items(self, target_type: str) -> AsyncIterator[dict]:
        if target_type == "likes":
            async for item in self._fetch_likes():
                yield item
        elif target_type == "comments":
            async for item in self._fetch_comments():
                yield item

    # ── Comments ─────────────────────────────────────────────────────

    async def _fetch_comments(self) -> AsyncIterator[dict]:
        """Yield a single batch_delete item — actual work happens in delete_item."""
        page = await self._new_page()
        try:
            await self._check_cancelled()
            await self._log("Navigating to Your Activity > Comments...")
            try:
                await page.goto(
                    f"{IG_BASE}/your_activity/interactions/comments",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except Exception:
                pass
            await page.wait_for_timeout(3000)

            if "/accounts/login" in page.url:
                await self._log("Redirected to login — session invalid", "error")
                return

            count = await self._count_comments(page)

            if count == 0:
                await self._log("No comments to delete")
                return

            yield {
                "platform_id": "batch_comments",
                "item_type": "comment",
                "metadata": json.dumps({"mode": "batch_delete", "initial_count": count}),
            }
        finally:
            await page.close()

    async def _count_comments(self, page: Page) -> int:
        """Count comment entries by looking for timestamp patterns (2w, 3d, 1h, etc.)."""
        return await page.evaluate("""
            () => {
                const spans = document.querySelectorAll('span');
                let count = 0;
                for (const span of spans) {
                    const t = span.textContent?.trim();
                    if (/^\\d+[smhdw]$/.test(t)) count++;
                }
                return count;
            }
        """)

    async def _navigate_to_comments(self, page: Page) -> bool:
        """Navigate (or reload) the Your Activity > Comments page."""
        try:
            await page.goto(
                f"{IG_BASE}/your_activity/interactions/comments",
                wait_until="domcontentloaded",
                timeout=30000,
            )
        except Exception:
            pass
        await page.wait_for_timeout(3000)

        if "/accounts/login" in page.url:
            await self._log("Redirected to login", "error")
            return False

        # Check for error state
        page_text = await page.evaluate("() => document.body?.innerText?.substring(0, 200) || ''")
        if "failed to load" in page_text.lower():
            await self._log("Page shows 'Failed to load' — Instagram is blocking", "error")
            return False

        return True

    async def delete_item(self, item: dict) -> bool:
        """Route to the right delete method."""
        await self._check_cancelled()
        meta = json.loads(item.get("metadata", "{}"))
        if item["item_type"] == "like" and meta.get("mode") == "batch_delete":
            return await self._batch_unlike_likes()
        elif item["item_type"] == "comment" and meta.get("mode") == "batch_delete":
            return await self._batch_delete_comments()
        return False

    async def _batch_delete_comments(self) -> bool:
        """
        Delete comments in batches of 20-25, with session/daily limits,
        action block detection, and human-like timing.
        """
        total_deleted = 0
        consecutive_failures = 0
        page = await self.context.new_page()

        try:
            await self._log("Opening Your Activity > Comments...")
            if not await self._navigate_to_comments(page):
                return False

            count_initial = await self._count_comments(page)

            if count_initial == 0:
                await self._log("No comments to delete")
                return True

            await self._log("Comments found, starting batch deletion...")

            while not self._cancelled:
                # Enforce session time and daily cap limits
                await self._check_session_limits()

                batch_size = random.randint(BATCH_SIZE_MIN, BATCH_SIZE_MAX)
                await self._log(f"Batch: removing up to {batch_size} comment(s)... [daily: {self._daily_actions}/{DAILY_CAP}]")

                # Step 1: Click Select
                select_pos = await page.evaluate("""
                    () => {
                        const els = document.querySelectorAll('span, button, [role="button"], a');
                        for (const el of els) {
                            const text = el.textContent?.trim().toLowerCase();
                            if (text === 'select' || text === 'selecionar') {
                                const rect = el.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                                }
                            }
                        }
                        return null;
                    }
                """)
                if not select_pos:
                    await self._log("Could not find 'Select' button", "error")
                    break

                await page.mouse.click(select_pos["x"], select_pos["y"])
                await page.wait_for_timeout(1500)

                # Capture fingerprints of visible comments before selection
                fp_before = await self._get_comment_fingerprints(page)

                # Step 2: Click checkboxes using Playwright locators with force=True
                # (bypasses the overlapping "Image with button" element).
                # Iterate through all checkbox elements, scroll each into view.
                all_cbs = page.locator('[aria-label="Toggle checkbox"]')
                total_cbs = await all_cbs.count()
                clicked_count = 0

                for i in range(min(total_cbs, batch_size)):
                    cb = all_cbs.nth(i)
                    try:
                        await cb.scroll_into_view_if_needed(timeout=2000)
                        await cb.click(force=True, timeout=3000)
                        clicked_count += 1
                        await page.wait_for_timeout(random.randint(CLICK_DELAY_MIN, CLICK_DELAY_MAX))
                    except Exception:
                        continue

                if clicked_count == 0:
                    await self._log("No checkboxes found — done or page error", "warn")
                    break

                # Read how many Instagram actually registered
                selected_count = await self._read_ui_selected(page)
                if selected_count == 0:
                    selected_count = clicked_count  # fallback if UI text not found
                await self._log(f"Selected {selected_count} comment(s)")

                # Optional reading pause before action
                await self._reading_pause()

                # Step 3: Click Delete (bottom bar)
                delete_pos = await page.evaluate("""
                    () => {
                        const els = document.querySelectorAll('span, div, button');
                        for (const el of els) {
                            const text = el.textContent?.trim();
                            const tag = el.tagName.toLowerCase();
                            if (tag === 'title') continue;
                            if (text === 'Delete' || text === 'Excluir') {
                                const rect = el.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                                }
                            }
                        }
                        return null;
                    }
                """)
                if not delete_pos:
                    await self._log("Could not find Delete button", "error")
                    break

                await page.mouse.click(delete_pos["x"], delete_pos["y"])
                await page.wait_for_timeout(2000)

                # Step 4: Click confirmation Delete INSIDE the dialog
                # Instagram doesn't use [role="dialog"]. Instead, find the Delete
                # button that's NOT the bottom-bar one (the confirmation appears as
                # an overlay, typically in the center of the viewport).
                confirm_pos = await page.evaluate("""
                    () => {
                        const vh = window.innerHeight;
                        const vw = window.innerWidth;
                        const candidates = [];
                        const els = document.querySelectorAll('button, [role="button"], span, a, div');
                        for (const el of els) {
                            const text = el.textContent?.trim();
                            if (text !== 'Delete' && text !== 'Excluir') continue;
                            if (el.children.length > 2) continue;
                            const rect = el.getBoundingClientRect();
                            if (rect.width <= 0 || rect.height <= 0 || rect.height > 80) continue;
                            // Dialog buttons appear in the center portion of the screen
                            // Bottom bar is at the very bottom (y > vh - 80)
                            candidates.push({
                                x: rect.x + rect.width/2,
                                y: rect.y + rect.height/2,
                                w: rect.width,
                                h: rect.height,
                                distFromCenter: Math.abs(rect.y + rect.height/2 - vh/2)
                            });
                        }
                        if (candidates.length === 0) return null;
                        // If multiple Delete buttons, pick the one closest to viewport center
                        // (the dialog one), NOT the bottom bar one
                        candidates.sort((a, b) => a.distFromCenter - b.distFromCenter);
                        // Safety: if there's only one and it's at the bottom, it's the bar button
                        if (candidates.length === 1 && candidates[0].y > vh - 100) return null;
                        return candidates[0];
                    }
                """)

                if not confirm_pos:
                    await self._log("No confirmation dialog found", "warn")
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        await self._log("Too many failures, stopping", "error")
                        break
                    await self._navigate_to_comments(page)
                    continue

                await self._log(f"Clicking confirm at ({confirm_pos['x']:.0f}, {confirm_pos['y']:.0f})")
                await page.mouse.click(confirm_pos["x"], confirm_pos["y"])
                await page.wait_for_timeout(3000)

                # Step 5: Reload and verify deletion via fingerprints
                if not await self._navigate_to_comments(page):
                    await self._log("Page failed to load after deletion", "error")
                    verified = await self._pause_and_retry(page)
                    if not verified:
                        return total_deleted > 0

                if await self._check_action_blocked(page):
                    await self._handle_action_block()
                    if not await self._navigate_to_comments(page):
                        return total_deleted > 0

                fp_after = await self._get_comment_fingerprints(page)
                removed = set(fp_before) - set(fp_after)

                if removed:
                    batch_deleted = selected_count if selected_count > 0 else clicked_count
                    total_deleted += batch_deleted
                    self._record_actions(batch_deleted)
                    consecutive_failures = 0
                    await self._report_progress(deleted=total_deleted)
                    await self._log(f"Batch done: {batch_deleted} deleted. Total: {total_deleted}")
                else:
                    consecutive_failures += 1
                    await self._log(
                        f"Deletion not verified — same comments still present (failure {consecutive_failures}/3)",
                        "error"
                    )
                    if consecutive_failures >= 3:
                        await self._log("Deletion is not working — stopping to avoid wasted actions", "error")
                        break
                    continue

                # Inter-batch delay (20-45 seconds)
                await self._inter_batch_delay()

            await self._log(f"Batch delete finished. Total deleted: {total_deleted}")
            return total_deleted > 0

        except asyncio.CancelledError:
            await self._log(f"Cancelled. Deleted {total_deleted} comment(s) before stopping.")
            return total_deleted > 0
        except Exception as e:
            await self._log(f"Error in batch delete: {e}", "error")
            return total_deleted > 0
        finally:
            await page.close()

    async def _pause_and_retry(self, page: Page) -> bool:
        """
        Pause for 5 minutes, reload, and check if the page works.
        Returns True if recovered, False if still broken (task should stop).
        """
        await self._log("Pausing for 5 minutes before retrying...", "warn")

        # Wait 5 minutes in 30-second increments (so cancel can interrupt)
        for i in range(10):
            await self._check_cancelled()
            remaining = (10 - i) * 30
            await self._log(f"Waiting... {remaining}s remaining")
            await asyncio.sleep(30)

        await self._log("Retrying after 5-minute pause...")

        if not await self._navigate_to_comments(page):
            await self._log("Page still broken after 5-minute pause. Stopping task to avoid further blocking.", "error")
            return False

        count = await self._count_comments(page)
        await self._log(f"Page recovered. Comments remaining: {count}")
        return True

    # ── Likes ────────────────────────────────────────────────────────

    async def _fetch_likes(self) -> AsyncIterator[dict]:
        """Yield a single batch_delete item — actual work happens in delete_item."""
        page = await self._new_page()
        try:
            await self._check_cancelled()
            await self._log("Navigating to Your Activity > Likes...")
            try:
                await page.goto(
                    f"{IG_BASE}/your_activity/interactions/likes",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except Exception:
                pass
            await page.wait_for_timeout(3000)

            await self._log(f"On page: {page.url}")

            if "/accounts/login" in page.url:
                await self._log("Redirected to login — session invalid", "error")
                return

            # The likes page may show a grid of thumbnails — scroll to load them
            # and click the "Likes" tab if needed
            await page.evaluate("""
                () => {
                    // Click "Likes" tab/link if visible (in case we landed on the overview)
                    const els = document.querySelectorAll('a, span, button, [role="tab"]');
                    for (const el of els) {
                        const text = el.textContent?.trim();
                        if (text === 'Likes' || text === 'Curtidas') {
                            el.click();
                            break;
                        }
                    }
                }
            """)
            await page.wait_for_timeout(2000)

            # Scroll down to load liked posts
            for _ in range(3):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1500)

            # Count likes by images/thumbnails in the grid (not timestamps — likes page uses thumbnails)
            count = await page.evaluate("""
                () => {
                    // Try post/reel links first
                    const postLinks = document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]');
                    if (postLinks.length > 0) return postLinks.length;

                    // Try image thumbnails (likes grid shows images without direct post links)
                    // Filter out nav/profile images by checking if they're inside the main content area
                    const allImgs = document.querySelectorAll('img');
                    let count = 0;
                    for (const img of allImgs) {
                        const src = img.src || '';
                        const w = img.width || img.naturalWidth || 0;
                        // Liked post thumbnails are typically square and > 50px, skip tiny icons
                        if (w >= 50 && !src.includes('profile') && !src.includes('static')) {
                            count++;
                        }
                    }
                    return count;
                }
            """)
            await self._log(f"Found {count} liked posts on page")

            if count == 0:
                await self._log("No likes found — page may not have loaded correctly")
                # Log debug info
                debug = await page.evaluate("""
                    () => ({
                        url: location.href,
                        imgs: document.querySelectorAll('img').length,
                        text: document.body?.innerText?.substring(0, 300) || ''
                    })
                """)
                await self._log(f"Debug: {debug}")
                return

            yield {
                "platform_id": "batch_likes",
                "item_type": "like",
                "metadata": json.dumps({"mode": "batch_delete", "initial_count": count}),
            }
        finally:
            await page.close()

    async def _count_likes(self, page: Page) -> int:
        """Count liked posts by post links or image thumbnails."""
        # Click Likes tab and scroll to ensure content is loaded
        await page.evaluate("""
            () => {
                const els = document.querySelectorAll('a, span, button, [role="tab"]');
                for (const el of els) {
                    const text = el.textContent?.trim();
                    if (text === 'Likes' || text === 'Curtidas') {
                        el.click();
                        break;
                    }
                }
            }
        """)
        await page.wait_for_timeout(1500)
        for _ in range(2):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)

        return await page.evaluate("""
            () => {
                const postLinks = document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]');
                if (postLinks.length > 0) return postLinks.length;
                const allImgs = document.querySelectorAll('img');
                let count = 0;
                for (const img of allImgs) {
                    const src = img.src || '';
                    const w = img.width || img.naturalWidth || 0;
                    if (w >= 50 && !src.includes('profile') && !src.includes('static')) count++;
                }
                return count;
            }
        """)

    async def _navigate_to_likes(self, page: Page) -> bool:
        """Navigate (or reload) the Your Activity > Likes page and ensure content loads."""
        try:
            await page.goto(
                f"{IG_BASE}/your_activity/interactions/likes",
                wait_until="domcontentloaded",
                timeout=30000,
            )
        except Exception:
            pass
        await page.wait_for_timeout(3000)

        if "/accounts/login" in page.url:
            await self._log("Redirected to login", "error")
            return False

        page_text = await page.evaluate("() => document.body?.innerText?.substring(0, 200) || ''")
        if "failed to load" in page_text.lower():
            await self._log("Page shows 'Failed to load' — Instagram is blocking", "error")
            return False

        # Click Likes tab and scroll to load content
        await page.evaluate("""
            () => {
                const els = document.querySelectorAll('a, span, button, [role="tab"]');
                for (const el of els) {
                    const text = el.textContent?.trim();
                    if (text === 'Likes' || text === 'Curtidas') {
                        el.click();
                        break;
                    }
                }
            }
        """)
        await page.wait_for_timeout(2000)
        for _ in range(2):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)

        return True

    async def _get_grid_image_srcs(self, page: Page) -> list[str]:
        """Get the src of all thumbnail images currently in the likes grid."""
        return await page.evaluate("""
            () => {
                const srcs = [];
                const imgs = document.querySelectorAll('img');
                for (const img of imgs) {
                    const w = img.width || img.naturalWidth || 0;
                    const src = img.src || '';
                    if (w >= 50 && !src.includes('profile') && !src.includes('static')) {
                        srcs.push(src);
                    }
                }
                return srcs;
            }
        """)

    async def _read_ui_selected(self, page) -> int:
        """Read the 'N selected' counter from Instagram's UI. Returns 0 if not found."""
        return await page.evaluate("""
            () => {
                const els = document.querySelectorAll('span');
                for (const el of els) {
                    const text = el.textContent?.trim() || '';
                    const match = text.match(/(\\d+)\\s*selected/i);
                    if (match) return parseInt(match[1]);
                }
                return 0;
            }
        """)

    async def _get_comment_fingerprints(self, page) -> list[str]:
        """Get text-based fingerprints of visible comment items on the Your Activity > Comments page."""
        return await page.evaluate("""
            () => {
                const fingerprints = [];
                // Each comment row on the activity page typically contains the comment text.
                // We look for the list items or row containers that hold individual comments.
                const rows = document.querySelectorAll(
                    '[role="listitem"], [role="row"], article, ' +
                    'div[style*="flex"] > div[style*="flex"]'
                );
                for (const row of rows) {
                    const rect = row.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0 || rect.height > 200) continue;
                    // Extract meaningful text (skip very short or generic text)
                    const text = (row.innerText || '').trim().replace(/\\s+/g, ' ');
                    if (text.length > 10 && text.length < 500) {
                        fingerprints.push(text);
                    }
                }
                // Deduplicate while preserving order
                return [...new Set(fingerprints)];
            }
        """)

    async def _get_grid_fingerprints(self, page) -> list[str]:
        """Get stable filename-based fingerprints of all grid images."""
        return await page.evaluate("""
            () => {
                const imgs = document.querySelectorAll('img');
                const files = [];
                for (const img of imgs) {
                    const alt = img.alt || '';
                    const src = img.src || '';
                    const w = img.width || img.naturalWidth || 0;
                    if (w < 100) continue;
                    if (alt.includes('profile picture')) continue;
                    if (src.includes('static')) continue;
                    const rect = img.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0 || rect.y <= 0) continue;
                    const filename = src.split('?')[0].split('/').pop() || '';
                    if (filename) files.push(filename);
                }
                return files;
            }
        """)

    async def _batch_unlike_likes(self) -> bool:
        """
        Unlike likes in batches of 20-25, with session/daily limits,
        action block detection, and human-like timing.
        """
        total_unliked = 0
        consecutive_failures = 0
        page = await self.context.new_page()

        try:
            await self._log("Opening Your Activity > Likes...")
            if not await self._navigate_to_likes(page):
                return False

            fingerprints_before = await self._get_grid_fingerprints(page)
            await self._log(f"Likes visible: {len(fingerprints_before)}")

            if len(fingerprints_before) == 0:
                await self._log("No likes to remove")
                return True

            # ETA estimate
            eta = self._estimate_duration(len(fingerprints_before))
            await self._log(f"Estimated duration: {eta}")
            await self._emit_event("eta", {"estimate": eta, "total_items": len(fingerprints_before)})

            while not self._cancelled:
                # Enforce session time and daily cap limits
                await self._check_session_limits()

                batch_size = random.randint(BATCH_SIZE_MIN, BATCH_SIZE_MAX)
                await self._log(f"Batch: removing up to {batch_size} like(s)... [daily: {self._daily_actions}/{DAILY_CAP}]")

                # Get fingerprints before this batch
                fp_before = await self._get_grid_fingerprints(page)

                # Step 1: Click "Select"
                select_pos = await page.evaluate("""
                    () => {
                        const els = document.querySelectorAll('span, button, [role="button"], a');
                        for (const el of els) {
                            const text = el.textContent?.trim().toLowerCase();
                            if (text === 'select' || text === 'selecionar') {
                                const rect = el.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                                }
                            }
                        }
                        return null;
                    }
                """)
                if not select_pos:
                    await self._log("Could not find 'Select' button", "error")
                    break

                await page.mouse.click(select_pos["x"], select_pos["y"])
                await page.wait_for_timeout(1500)

                # Step 2: Click thumbnails with scroll-to-load
                selected_files = []
                scroll_attempts = 0
                while len(selected_files) < batch_size and scroll_attempts < 10:
                    grid_items = await page.evaluate("""
                        () => {
                            const result = [];
                            const imgs = document.querySelectorAll('img');
                            for (const img of imgs) {
                                const alt = img.alt || '';
                                const src = img.src || '';
                                const w = img.width || img.naturalWidth || 0;
                                if (w < 100) continue;
                                if (alt.includes('profile picture')) continue;
                                if (src.includes('static')) continue;
                                const rect = img.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0 && rect.y > 0) {
                                    const parent = img.closest('div');
                                    const isSelected = parent?.querySelector('[aria-checked="true"]') ||
                                                      img.style.opacity === '0.5' ||
                                                      parent?.style.opacity === '0.5';
                                    result.push({
                                        src_file: src.split('?')[0].split('/').pop() || '',
                                        x: rect.x + rect.width / 2,
                                        y: rect.y + rect.height / 2,
                                        selected: !!isSelected,
                                    });
                                }
                            }
                            result.sort((a, b) => {
                                const rowA = Math.floor(a.y / 100);
                                const rowB = Math.floor(b.y / 100);
                                if (rowA !== rowB) return rowA - rowB;
                                return a.x - b.x;
                            });
                            return result;
                        }
                    """)

                    if len(grid_items) == 0 and len(selected_files) == 0:
                        break

                    already_selected = set(selected_files)
                    for item in grid_items:
                        if len(selected_files) >= batch_size:
                            break
                        if item["src_file"] in already_selected:
                            continue
                        await page.mouse.click(item["x"], item["y"])
                        selected_files.append(item["src_file"])
                        click_delay = random.randint(CLICK_DELAY_MIN, CLICK_DELAY_MAX)
                        await page.wait_for_timeout(click_delay)

                    if len(selected_files) >= batch_size:
                        break

                    await page.evaluate("window.scrollBy(0, 400)")
                    await page.wait_for_timeout(1000)
                    scroll_attempts += 1

                actual_batch = len(selected_files)
                if actual_batch == 0:
                    await self._log("No grid thumbnails found — done or page error", "warn")
                    break

                await self._log(f"Selected {actual_batch} thumbnail(s)")

                # Optional reading pause before action
                await self._reading_pause()

                # Step 3: Click Unlike (bottom bar)
                unlike_pos = await page.evaluate("""
                    () => {
                        const els = document.querySelectorAll('span, div, button');
                        for (const el of els) {
                            const text = el.textContent?.trim();
                            const tag = el.tagName.toLowerCase();
                            if (tag === 'title') continue;
                            if (text === 'Unlike' || text === 'Descurtir') {
                                const rect = el.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                                }
                            }
                        }
                        return null;
                    }
                """)

                if not unlike_pos:
                    await self._log("Could not find Unlike button", "error")
                    break

                await page.mouse.click(unlike_pos["x"], unlike_pos["y"])
                await page.wait_for_timeout(2000)

                # Step 4: Click confirmation dialog Unlike (closest to viewport center)
                confirm_pos = await page.evaluate("""
                    () => {
                        const allEls = document.querySelectorAll('button, [role="button"], span, div, a');
                        const candidates = [];
                        for (const el of allEls) {
                            const tag = el.tagName.toLowerCase();
                            if (tag === 'title' || tag === 'svg') continue;
                            const text = el.textContent?.trim();
                            if (text !== 'Unlike' && text !== 'Descurtir') continue;
                            if (el.children.length > 1) continue;
                            const rect = el.getBoundingClientRect();
                            if (rect.width <= 0 || rect.height <= 0) continue;
                            if (rect.height > 80) continue;
                            const cy = window.innerHeight / 2;
                            const cx = window.innerWidth / 2;
                            const dist = Math.sqrt(
                                Math.pow(rect.x + rect.width/2 - cx, 2) +
                                Math.pow(rect.y + rect.height/2 - cy, 2)
                            );
                            candidates.push({
                                x: rect.x + rect.width/2,
                                y: rect.y + rect.height/2,
                                dist
                            });
                        }
                        candidates.sort((a, b) => a.dist - b.dist);
                        return candidates.length > 0 ? candidates[0] : null;
                    }
                """)

                if not confirm_pos:
                    await self._log("No confirmation dialog found", "warn")
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        await self._log("Too many failures, stopping", "error")
                        break
                    await self._navigate_to_likes(page)
                    continue

                await page.mouse.click(confirm_pos["x"], confirm_pos["y"])
                await page.wait_for_timeout(3000)

                # Verify by reloading and checking fingerprints
                await self._navigate_to_likes(page)

                # Check for action block
                if await self._check_action_blocked(page):
                    await self._handle_action_block()
                    if not await self._navigate_to_likes(page):
                        return total_unliked > 0

                fp_after = await self._get_grid_fingerprints(page)
                removed = set(selected_files) - set(fp_after)

                if removed:
                    total_unliked += actual_batch
                    self._record_actions(actual_batch)
                    consecutive_failures = 0
                    await self._report_progress(deleted=total_unliked)
                    await self._log(f"Verified: {len(removed)} like(s) removed. Total: {total_unliked}")
                else:
                    consecutive_failures += 1
                    await self._log(f"Unlike not verified — images still present (failure {consecutive_failures}/3)", "warn")
                    if consecutive_failures >= 3:
                        await self._log("3 consecutive failures, pausing...")
                        if not await self._pause_and_retry_likes(page):
                            break
                        consecutive_failures = 0

                if len(fp_after) == 0:
                    await self._log("All likes removed!")
                    return True

                # Inter-batch delay (20-45 seconds)
                await self._inter_batch_delay()

            await self._log(f"Batch unlike finished. Total unliked: {total_unliked}")
            return total_unliked > 0

        except asyncio.CancelledError:
            await self._log(f"Cancelled. Unliked {total_unliked} post(s) before stopping.")
            return total_unliked > 0
        except Exception as e:
            await self._log(f"Error in batch unlike: {e}", "error")
            return total_unliked > 0
        finally:
            await page.close()

    async def _pause_and_retry_likes(self, page: Page) -> bool:
        """
        Pause for 5 minutes, reload likes page, and check if it works.
        Returns True if recovered, False if still broken.
        """
        await self._log("Pausing for 5 minutes before retrying...", "warn")

        for i in range(10):
            await self._check_cancelled()
            remaining = (10 - i) * 30
            await self._log(f"Waiting... {remaining}s remaining")
            await asyncio.sleep(30)

        await self._log("Retrying after 5-minute pause...")

        if not await self._navigate_to_likes(page):
            await self._log("Page still broken after 5-minute pause. Stopping task to avoid further blocking.", "error")
            return False

        count = await self._count_likes(page)
        await self._log(f"Page recovered. Likes remaining: {count}")
        return True
