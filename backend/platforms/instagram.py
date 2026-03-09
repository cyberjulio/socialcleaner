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

    async def _new_page(self) -> Page:
        """Create a new page and navigate to IG."""
        page = await self.context.new_page()
        await self._log("Opening Instagram homepage...")
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

    # ── Comments: scan via network interception ──────────────────────

    async def _fetch_comments(self) -> AsyncIterator[dict]:
        """Navigate to Your Activity > Comments page and capture API responses."""
        page = await self._new_page()
        try:
            captured = []

            async def on_response(response):
                url = response.url
                if any(k in url for k in ["wbloks/fetch", "graphql"]):
                    try:
                        body = await response.text()
                        if len(body) > 1000:
                            captured.append({"url": url, "body": body})
                    except Exception:
                        pass

            page.on("response", on_response)

            await self._log("Navigating to Your Activity > Comments...")
            try:
                await page.goto(
                    f"{IG_BASE}/your_activity/interactions/comments",
                    wait_until="networkidle",
                    timeout=30000,
                )
            except Exception:
                pass
            await page.wait_for_timeout(3000)

            if "/accounts/login" in page.url:
                await self._log("Redirected to login — session invalid", "error")
                return

            await self._log(f"On page: {page.url}")
            await self._log(f"Captured {len(captured)} API responses")

            # Scroll to load more comments
            for scroll in range(5):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(random.uniform(2000, 3000))

            await self._log(f"After scrolling, {len(captured)} total API responses captured")

            # Parse comments from all captured responses
            comments = []
            for resp in captured:
                parsed = self._parse_comments_response(resp["body"])
                if parsed:
                    await self._log(f"Parsed {len(parsed)} comments from {resp['url'][:80]}")
                    comments.extend(parsed)

            # Deduplicate by comment_id
            seen = set()
            unique = []
            for c in comments:
                cid = c.get("comment_id", "")
                if cid and cid not in seen:
                    seen.add(cid)
                    unique.append(c)

            await self._log(f"Found {len(unique)} unique comments total")

            for i, c in enumerate(unique):
                text_preview = c.get("text", "")[:60]
                await self._log(f"Comment #{i+1}: '{text_preview}' (id={c.get('comment_id', '?')})")
                yield {
                    "platform_id": c.get("comment_id", f"comment_{i}"),
                    "item_type": "comment",
                    "metadata": json.dumps(c),
                }
        finally:
            await page.close()

    def _parse_comments_response(self, body: str) -> list[dict]:
        """Parse comments from a wbloks or graphql response."""
        comments = []

        # Try JSON parse first
        try:
            data = json.loads(body)
            # GraphQL response format
            comments.extend(self._extract_from_graphql(data))
        except json.JSONDecodeError:
            pass

        # Try wbloks format (may start with "for (;;);")
        clean = body
        if body.startswith("for (;;);"):
            clean = body[len("for (;;);"):]
            try:
                data = json.loads(clean)
                raw = json.dumps(data)
            except Exception:
                raw = clean
        else:
            raw = body

        comments.extend(self._extract_from_wbloks(raw))
        return comments

    def _extract_from_graphql(self, data: dict, depth: int = 0) -> list[dict]:
        """Recursively extract comments from GraphQL JSON response."""
        comments = []
        if depth > 10:
            return comments

        if isinstance(data, dict):
            # Check if this dict looks like a comment node
            has_text = "text" in data and isinstance(data.get("text"), str) and len(data["text"]) > 2
            has_id = any(k in data for k in ("id", "pk", "comment_id", "node_id"))

            if has_text and has_id:
                text = data["text"]
                # Filter out UI strings
                if not any(ui in text.lower() for ui in [
                    "sort & filter", "appears on", "select all", "delete",
                    "dtl:", "bk.", "ig_activity", "visible"
                ]):
                    comment_id = str(data.get("comment_id") or data.get("pk") or data.get("id") or data.get("node_id") or "")
                    media_id = str(data.get("media_id") or data.get("media_pk") or "")

                    # Try to find media_id in parent context
                    if not media_id:
                        media = data.get("media") or data.get("post") or {}
                        if isinstance(media, dict):
                            media_id = str(media.get("id") or media.get("pk") or "")

                    if comment_id:
                        comments.append({
                            "comment_id": comment_id,
                            "text": text[:500],
                            "media_id": media_id,
                        })

            # Recurse into all values
            for v in data.values():
                comments.extend(self._extract_from_graphql(v, depth + 1))

        elif isinstance(data, list):
            for item in data:
                comments.extend(self._extract_from_graphql(item, depth + 1))

        return comments

    def _extract_from_wbloks(self, raw: str) -> list[dict]:
        """Extract comments from wbloks response text using pattern matching."""
        comments = []
        seen_ids = set()

        # Pattern: look for comment structures in wbloks
        # wbloks data contains stringified arrays with comment data
        # Look for patterns like: "comment_id_value","comment_text","timestamp","media_id_value"

        # Find all long numeric IDs (potential comment_ids or media_ids)
        all_ids = re.findall(r'\b(\d{10,20})\b', raw)

        # Find all text strings that look like actual comment content
        # (not UI labels, not internal identifiers)
        all_strings = re.findall(r'"((?:[^"\\]|\\.){5,500})"', raw)
        comment_texts = []
        for s in all_strings:
            # Filter out UI text and internal strings
            if any(skip in s.lower() for skip in [
                "sort & filter", "appears on facebook", "dtl:", "bk.", "ig_",
                "com.instagram", "visible", "activity_center", "instagram.com",
                "select all", "\\u0", "function", "script", "style", "class=",
                "div>", "span>", "http", "www.", ".js", ".css", ".png", ".jpg",
            ]):
                continue
            # Must look like natural language (has spaces, not all digits)
            if " " in s and not s.isdigit() and len(s) > 5:
                comment_texts.append(s)

        # Try to pair comment texts with nearby IDs
        for text in comment_texts:
            pos = raw.find(f'"{text}"')
            if pos < 0:
                continue
            # Look for numeric IDs near this text (within 200 chars)
            nearby = raw[max(0, pos - 200):pos + len(text) + 200]
            nearby_ids = re.findall(r'\b(\d{10,20})\b', nearby)

            if nearby_ids:
                comment_id = nearby_ids[0]
                media_id = nearby_ids[1] if len(nearby_ids) > 1 else ""

                if comment_id not in seen_ids:
                    seen_ids.add(comment_id)
                    comments.append({
                        "comment_id": comment_id,
                        "text": text[:500],
                        "media_id": media_id,
                    })

        return comments

    # ── Comments: delete via UI ──────────────────────────────────────

    async def delete_item(self, item: dict) -> bool:
        """Delete an item by interacting with the UI."""
        if item["item_type"] == "like":
            return await self._unlike_via_post(item)
        elif item["item_type"] == "comment":
            return await self._delete_comment_via_ui(item)
        return False

    async def _delete_comment_via_ui(self, item: dict) -> bool:
        """Delete a comment using the Your Activity page's Select → Delete UI."""
        meta = json.loads(item.get("metadata", "{}"))
        comment_text = meta.get("text", "")
        if not comment_text:
            await self._log("No comment text to search for", "warn")
            return False

        page = await self._new_page()
        try:
            await self._log(f"Deleting comment: '{comment_text[:50]}'")

            # Navigate to Your Activity > Comments
            try:
                await page.goto(
                    f"{IG_BASE}/your_activity/interactions/comments",
                    wait_until="networkidle",
                    timeout=30000,
                )
            except Exception:
                pass
            await page.wait_for_timeout(3000)

            if "/accounts/login" in page.url:
                await self._log("Redirected to login", "error")
                return False

            # Click "Select" button to enter selection mode
            select_clicked = await page.evaluate("""
                () => {
                    const els = document.querySelectorAll('button, [role="button"], a, span');
                    for (const el of els) {
                        const text = el.innerText?.trim().toLowerCase();
                        if (text === 'select' || text === 'selecionar') {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }
            """)
            await self._log(f"Select button: {select_clicked}")
            if not select_clicked:
                await self._log("Could not find Select button", "warn")
                return False

            await page.wait_for_timeout(random.uniform(1000, 2000))

            # Find and click the checkbox/row for our comment
            search_text = comment_text[:40]
            found = await page.evaluate("""
                (searchText) => {
                    // Find all text nodes that contain our comment
                    const walker = document.createTreeWalker(
                        document.body, NodeFilter.SHOW_TEXT, null, false
                    );
                    let node;
                    while (node = walker.nextNode()) {
                        if (node.textContent?.includes(searchText)) {
                            // Found the text — walk up to find the clickable row
                            let el = node.parentElement;
                            for (let i = 0; i < 10 && el; i++) {
                                // Look for a checkbox or clickable container
                                const cb = el.querySelector('[role="checkbox"], input[type="checkbox"]');
                                if (cb) {
                                    cb.click();
                                    return 'checkbox';
                                }
                                // Check if this element itself is clickable
                                if (el.getAttribute('role') === 'checkbox') {
                                    el.click();
                                    return 'role-checkbox';
                                }
                                el = el.parentElement;
                            }
                            // Fallback: click the nearest parent div
                            node.parentElement?.closest('div[role="button"], div[style]')?.click();
                            return 'fallback-click';
                        }
                    }
                    return null;
                }
            """, search_text)
            await self._log(f"Comment selection: {found}")
            if not found:
                await self._log(f"Could not find comment on page", "warn")
                return False

            await page.wait_for_timeout(random.uniform(1000, 2000))

            # Click Delete button
            deleted = await page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button, [role="button"]');
                    for (const btn of buttons) {
                        const text = btn.innerText?.trim().toLowerCase();
                        if (text === 'delete' || text === 'excluir' || text === 'remove') {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }
            """)
            await self._log(f"Delete button: {deleted}")

            if deleted:
                await page.wait_for_timeout(random.uniform(1000, 2000))
                # Confirm deletion dialog
                await page.evaluate("""
                    () => {
                        const buttons = document.querySelectorAll('button, [role="button"]');
                        for (const btn of buttons) {
                            const text = btn.innerText?.trim().toLowerCase();
                            if (text === 'delete' || text === 'excluir' || text === 'confirm') {
                                btn.click();
                                return true;
                            }
                        }
                        return false;
                    }
                """)
                await page.wait_for_timeout(random.uniform(1500, 3000))
                await self._log("Comment deleted successfully")
                return True

            return False
        except Exception as e:
            await self._log(f"Error deleting comment: {e}", "error")
            return False
        finally:
            await page.close()

    # ── Likes: scan via API, unlike via post page ────────────────────

    async def _fetch_likes(self) -> AsyncIterator[dict]:
        """Navigate to Your Activity > Likes and capture liked posts."""
        page = await self._new_page()
        try:
            captured = []

            async def on_response(response):
                url = response.url
                if any(k in url for k in ["wbloks/fetch", "graphql", "liked"]):
                    try:
                        body = await response.text()
                        if len(body) > 1000:
                            captured.append({"url": url, "body": body})
                    except Exception:
                        pass

            page.on("response", on_response)

            await self._log("Navigating to Your Activity > Likes...")
            try:
                await page.goto(
                    f"{IG_BASE}/your_activity/interactions/likes",
                    wait_until="networkidle",
                    timeout=30000,
                )
            except Exception:
                pass
            await page.wait_for_timeout(3000)

            if "/accounts/login" in page.url:
                await self._log("Redirected to login", "error")
                return

            await self._log(f"On page: {page.url}, captured {len(captured)} responses")

            # Scroll to load more
            for scroll in range(5):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(random.uniform(2000, 3000))

            # Also extract shortcodes from visible links on the page
            links = await page.evaluate("""
                () => {
                    const results = [];
                    const links = document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]');
                    links.forEach(link => {
                        const href = link.getAttribute('href');
                        const match = href.match(/\\/(?:p|reel)\\/([A-Za-z0-9_-]+)/);
                        if (match) results.push(match[1]);
                    });
                    return [...new Set(results)];
                }
            """)
            await self._log(f"Found {len(links)} liked posts from page links")

            for sc in links:
                yield {
                    "platform_id": sc,
                    "item_type": "like",
                    "metadata": json.dumps({"shortcode": sc}),
                }
        finally:
            await page.close()

    async def _unlike_via_post(self, item: dict) -> bool:
        """Unlike a post by navigating to it and clicking the heart button."""
        meta = json.loads(item.get("metadata", "{}"))
        shortcode = meta.get("shortcode", item["platform_id"])
        page = await self._new_page()
        try:
            await self._log(f"Navigating to /{shortcode}/ to unlike")
            await page.goto(f"{IG_BASE}/p/{shortcode}/", wait_until="domcontentloaded")
            await page.wait_for_timeout(random.uniform(2000, 3000))

            liked = await page.evaluate("""
                () => {
                    const unlike = document.querySelector('[aria-label="Unlike"]');
                    if (unlike) {
                        unlike.closest('button')?.click() || unlike.click();
                        return true;
                    }
                    return false;
                }
            """)
            if liked:
                await self._log(f"Unliked post {shortcode}")
                await page.wait_for_timeout(random.uniform(500, 1500))
                return True
            else:
                await self._log(f"Unlike button not found for {shortcode}", "warn")
                return False
        except Exception as e:
            await self._log(f"Error unliking {shortcode}: {e}", "error")
            return False
        finally:
            await page.close()
