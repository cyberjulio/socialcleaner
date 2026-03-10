import json
import re
import logging
import asyncio
import random
from typing import AsyncIterator
from playwright.async_api import BrowserContext, Page
from backend.platforms.base import PlatformClient

logger = logging.getLogger(__name__)

IG_BASE = "https://www.instagram.com"


class InstagramClient(PlatformClient):
    def __init__(self, context: BrowserContext, cookies: dict[str, str]):
        super().__init__(context, cookies)
        self.csrf_token = cookies.get("csrftoken", "")
        self.user_id = cookies.get("ds_user_id", "")
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    async def _check_cancelled(self):
        if self._cancelled:
            raise asyncio.CancelledError("Task cancelled by user")

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
            await self._log(f"Found {count} comments on page")

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
        Delete comments in tiny batches (1-3 at a time), verifying each round.
        Uses native mouse clicks (not JS .click()) and viewport-center matching
        for confirmation dialogs.
        """
        total_deleted = 0
        consecutive_failures = 0
        page = await self.context.new_page()

        try:
            await self._log("Opening Your Activity > Comments...")
            if not await self._navigate_to_comments(page):
                return False

            count_before = await self._count_comments(page)
            await self._log(f"Starting batch delete. Comments on page: {count_before}")

            if count_before == 0:
                await self._log("No comments to delete")
                return True

            while not self._cancelled:
                await self._check_cancelled()

                batch_size = random.randint(20, 25)
                await self._log(f"Removing up to {batch_size} comment(s)...")

                # Step 1: Click Select (native click)
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

                # Step 2: Click checkboxes (native clicks)
                # Scroll down to load enough checkboxes for the batch
                selected_count = 0
                scroll_attempts = 0
                while selected_count < batch_size and scroll_attempts < 10:
                    checkboxes = await page.evaluate("""
                        () => {
                            const cbs = document.querySelectorAll(
                                '[role="checkbox"], input[type="checkbox"], ' +
                                '[aria-label*="checkbox"], [aria-label*="select"], ' +
                                '[aria-label*="Toggle"]'
                            );
                            const results = [];
                            for (const cb of cbs) {
                                const checked = cb.getAttribute('aria-checked') === 'true' || cb.checked === true;
                                if (checked) continue;
                                const rect = cb.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0 && rect.y > 100) {
                                    results.push({
                                        x: rect.x + rect.width/2,
                                        y: rect.y + rect.height/2
                                    });
                                }
                            }
                            return results;
                        }
                    """)

                    if len(checkboxes) == 0 and selected_count == 0:
                        break  # No checkboxes at all

                    # Click unchecked checkboxes visible on screen
                    for cb in checkboxes:
                        if selected_count >= batch_size:
                            break
                        await page.mouse.click(cb["x"], cb["y"])
                        selected_count += 1
                        await page.wait_for_timeout(300)

                    if selected_count >= batch_size:
                        break

                    # Scroll down to reveal more
                    await page.evaluate("window.scrollBy(0, 400)")
                    await page.wait_for_timeout(1000)
                    scroll_attempts += 1

                actual_batch = selected_count
                if actual_batch == 0:
                    await self._log("No checkboxes found — done or page error", "warn")
                    break

                await self._log(f"Selected {actual_batch} comment(s)")
                await page.wait_for_timeout(500)

                # Step 3: Click Delete (bottom bar, native click)
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

                # Step 4: Click confirmation Delete (closest to viewport center)
                confirm_pos = await page.evaluate("""
                    () => {
                        const allEls = document.querySelectorAll('button, [role="button"], span, div, a');
                        const candidates = [];
                        for (const el of allEls) {
                            const tag = el.tagName.toLowerCase();
                            if (tag === 'title' || tag === 'svg') continue;
                            const text = el.textContent?.trim();
                            if (text !== 'Delete' && text !== 'Excluir') continue;
                            if (el.children.length > 1) continue;
                            const rect = el.getBoundingClientRect();
                            if (rect.width <= 0 || rect.height <= 0 || rect.height > 80) continue;
                            const cy = window.innerHeight / 2;
                            const cx = window.innerWidth / 2;
                            const dist = Math.sqrt(
                                Math.pow(rect.x + rect.width/2 - cx, 2) +
                                Math.pow(rect.y + rect.height/2 - cy, 2)
                            );
                            candidates.push({x: rect.x + rect.width/2, y: rect.y + rect.height/2, dist});
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
                    await self._navigate_to_comments(page)
                    count_before = await self._count_comments(page)
                    continue

                await page.mouse.click(confirm_pos["x"], confirm_pos["y"])
                await page.wait_for_timeout(3000)

                # Step 5: Verify dialog dismissed (confirm click worked)
                dialog_still_open = await page.evaluate("""
                    () => {
                        const els = document.querySelectorAll('*');
                        for (const el of els) {
                            const text = el.textContent?.trim();
                            if (text === 'Delete this comment?' || text === 'Excluir este comentário?')
                                return true;
                        }
                        return false;
                    }
                """)

                if dialog_still_open:
                    consecutive_failures += 1
                    await self._log(f"Confirm click didn't dismiss dialog, failure {consecutive_failures}/3", "warn")
                    if consecutive_failures >= 3:
                        await self._log("Too many confirm failures, stopping", "error")
                        break
                    await self._navigate_to_comments(page)
                    continue

                # Dialog dismissed = deletion succeeded. Count is unreliable
                # due to Instagram paginating in older comments, so trust the flow.
                total_deleted += actual_batch
                consecutive_failures = 0
                await self._report_progress(deleted=total_deleted)
                await self._log(f"Deleted {actual_batch} comment(s). Total deleted: {total_deleted}")

                # Reload page for next batch
                if not await self._navigate_to_comments(page):
                    await self._log("Page failed to load after deletion", "error")
                    verified = await self._pause_and_retry(page)
                    if not verified:
                        return total_deleted > 0

                # Check if any comments remain
                count_after = await self._count_comments(page)
                if count_after == 0:
                    await self._log("All comments deleted!")
                    return True

                await self._log(f"Comments on page: {count_after}. Continuing...")

                # Rest pause every 300 deletions to simulate human behavior
                if total_deleted > 0 and total_deleted % 300 < actual_batch:
                    rest_minutes = random.uniform(3, 6)
                    await self._log(f"Reached {total_deleted} deletions. Resting for {rest_minutes:.1f} minutes...")
                    rest_seconds = int(rest_minutes * 60)
                    for i in range(0, rest_seconds, 30):
                        await self._check_cancelled()
                        remaining = rest_seconds - i
                        await self._log(f"Resting... {remaining}s remaining")
                        await asyncio.sleep(min(30, remaining))

                # Random delay between batches (5-15 seconds)
                delay = random.uniform(5, 15)
                await page.wait_for_timeout(int(delay * 1000))

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
        Unlike likes one at a time, verifying each by checking
        that the image fingerprint disappears from the grid.
        """
        total_unliked = 0
        consecutive_failures = 0
        page = await self.context.new_page()

        try:
            await self._log("Opening Your Activity > Likes...")
            if not await self._navigate_to_likes(page):
                return False

            fingerprints_before = await self._get_grid_fingerprints(page)
            await self._log(f"Starting batch unlike. Likes visible: {len(fingerprints_before)}")

            if len(fingerprints_before) == 0:
                await self._log("No likes to remove")
                return True

            while not self._cancelled:
                await self._check_cancelled()

                batch_size = random.randint(20, 25)
                await self._log(f"Removing up to {batch_size} like(s)...")

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

                # Step 2: Click thumbnails (batch_size items, scroll to load more)
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
                                    // Check if this thumbnail has a selected overlay
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
                        break  # No thumbnails at all

                    # Click unselected thumbnails
                    already_selected = set(selected_files)
                    for item in grid_items:
                        if len(selected_files) >= batch_size:
                            break
                        if item["src_file"] in already_selected:
                            continue
                        await page.mouse.click(item["x"], item["y"])
                        selected_files.append(item["src_file"])
                        await page.wait_for_timeout(300)

                    if len(selected_files) >= batch_size:
                        break

                    # Scroll down to load more thumbnails
                    await page.evaluate("window.scrollBy(0, 400)")
                    await page.wait_for_timeout(1000)
                    scroll_attempts += 1

                actual_batch = len(selected_files)
                if actual_batch == 0:
                    await self._log("No grid thumbnails found — done or page error", "warn")
                    break

                await self._log(f"Selected {actual_batch} thumbnail(s)")
                await page.wait_for_timeout(500)

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
                    # Reload page and retry
                    await self._navigate_to_likes(page)
                    continue

                await page.mouse.click(confirm_pos["x"], confirm_pos["y"])
                await page.wait_for_timeout(3000)

                # Verify by reloading and checking fingerprints
                await self._navigate_to_likes(page)
                fp_after = await self._get_grid_fingerprints(page)
                removed = set(selected_files) - set(fp_after)

                if removed:
                    total_unliked += actual_batch
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

                # Rest pause every 300 unlikes to simulate human behavior
                if total_unliked > 0 and total_unliked % 300 < actual_batch:
                    rest_minutes = random.uniform(3, 6)
                    await self._log(f"Reached {total_unliked} unlikes. Resting for {rest_minutes:.1f} minutes...")
                    rest_seconds = int(rest_minutes * 60)
                    for i in range(0, rest_seconds, 30):
                        await self._check_cancelled()
                        remaining = rest_seconds - i
                        await self._log(f"Resting... {remaining}s remaining")
                        await asyncio.sleep(min(30, remaining))

                # Random delay between batches (5-15 seconds)
                delay = random.uniform(5, 15)
                await page.wait_for_timeout(int(delay * 1000))

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
