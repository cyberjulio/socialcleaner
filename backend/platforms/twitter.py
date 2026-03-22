import json
import logging
from typing import AsyncIterator
from playwright.async_api import BrowserContext
from backend.platforms.base import PlatformClient

logger = logging.getLogger(__name__)

X_BASE = "https://x.com"
X_API = "https://x.com/i/api/graphql"

# Twitter's public web bearer token (embedded in their JS bundle, not a secret)
BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"


class TwitterClient(PlatformClient):
    def __init__(self, context: BrowserContext, cookies: dict[str, str]):
        super().__init__(context, cookies)
        self.csrf_token = cookies.get("ct0", "")
        self.user_id = None
        self._query_ids: dict[str, str] = {}

    async def _ensure_query_ids(self, page):
        """Extract GraphQL query IDs from Twitter's main JS bundle."""
        if self._query_ids:
            return

        # Navigate to X and intercept the main JS bundle
        await page.goto(X_BASE, wait_until="domcontentloaded")

        self._query_ids = await page.evaluate("""
            async () => {
                // Fetch the main page to find JS bundle URLs
                const html = document.documentElement.innerHTML;
                const scriptUrls = [...html.matchAll(/src="(https:\\/\\/abs\\.twimg\\.com\\/responsive-web\\/client-web[^"]+\\.js)"/g)]
                    .map(m => m[1]);

                const queryMap = {};
                for (const url of scriptUrls.slice(0, 10)) {
                    try {
                        const r = await fetch(url);
                        const text = await r.text();
                        // Match patterns like {queryId:"xxx",operationName:"Likes",...}
                        const matches = [...text.matchAll(/queryId:"([^"]+)",operationName:"([^"]+)"/g)];
                        for (const m of matches) {
                            queryMap[m[2]] = m[1];
                        }
                    } catch {}
                }
                return queryMap;
            }
        """)

        if not self._query_ids:
            # Fallback: use known query IDs (may need periodic updates)
            logger.warning("Could not extract query IDs from bundle, using fallbacks")
            self._query_ids = {
                "Likes": "qVjGmJdfRKE3WIgaSbQm0Q",
                "UnfavoriteTweet": "ZYKSe-w7KEslx3JhSIk5LA",
                "UserTweetsAndReplies": "BlkHfE-PVQE_dop1Oi3dZw",
                "DeleteTweet": "VaenaVgh5q5ih7kvyVjgtg",
                "Viewer": "W62NnYgkgziw9bwyoVht0g",
            }

    async def validate_session(self) -> dict:
        page = await self.context.new_page()
        try:
            await page.goto(X_BASE, wait_until="domcontentloaded")

            # Check if redirected to login
            if "/login" in page.url or "/i/flow/login" in page.url:
                raise ValueError("Session expired or invalid cookies")

            user_data = await page.evaluate("""
                async () => {
                    const r = await fetch('/i/api/1.1/account/verify_credentials.json', {
                        headers: {
                            'authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA',
                            'x-csrf-token': document.cookie.match(/ct0=([^;]+)/)?.[1] || '',
                            'x-twitter-auth-type': 'OAuth2Session',
                            'x-twitter-active-user': 'yes'
                        }
                    });
                    return r.json();
                }
            """)

            self.user_id = str(user_data.get("id_str", ""))
            return {
                "username": user_data.get("screen_name", "unknown"),
                "user_id": self.user_id,
            }
        finally:
            await page.close()

    async def fetch_items(self, target_type: str) -> AsyncIterator[dict]:
        if target_type == "likes":
            async for item in self._fetch_likes():
                yield item
        elif target_type == "comments":
            async for item in self._fetch_replies():
                yield item

    async def _fetch_likes(self) -> AsyncIterator[dict]:
        page = await self.context.new_page()
        try:
            await self._ensure_query_ids(page)

            if not self.user_id:
                info = await self.validate_session()
                self.user_id = info["user_id"]

            query_id = self._query_ids.get("Likes", "")
            cursor = None

            while True:
                variables = {
                    "userId": self.user_id,
                    "count": 20,
                    "includePromotedContent": False,
                }
                if cursor:
                    variables["cursor"] = cursor

                features = {
                    "rweb_tipjar_consumption_enabled": True,
                    "responsive_web_graphql_exclude_directive_enabled": True,
                    "verified_phone_label_enabled": False,
                    "responsive_web_graphql_timeline_navigation_enabled": True,
                    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                    "creator_subscriptions_tweet_preview_api_enabled": True,
                    "tweetypie_unmention_optimization_enabled": True,
                    "responsive_web_edit_tweet_api_enabled": True,
                    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
                    "view_counts_everywhere_api_enabled": True,
                    "longform_notetweets_consumption_enabled": True,
                    "responsive_web_twitter_article_tweet_consumption_enabled": True,
                    "tweet_awards_web_tipping_enabled": False,
                    "freedom_of_speech_not_reach_fetch_enabled": True,
                    "standardized_nudges_misinfo": True,
                    "longform_notetweets_rich_text_read_enabled": True,
                    "longform_notetweets_inline_media_enabled": True,
                    "responsive_web_enhance_cards_enabled": False,
                }

                vars_encoded = json.dumps(variables)
                features_encoded = json.dumps(features)

                resp = await page.evaluate("""
                    async ([queryId, varsEncoded, featuresEncoded, bearerToken]) => {
                        const url = '/i/api/graphql/' + queryId + '/Likes?variables=' +
                            encodeURIComponent(varsEncoded) +
                            '&features=' + encodeURIComponent(featuresEncoded);
                        const r = await fetch(url, {
                            headers: {
                                'authorization': 'Bearer ' + bearerToken,
                                'x-csrf-token': document.cookie.match(/ct0=([^;]+)/)?.[1] || '',
                                'x-twitter-auth-type': 'OAuth2Session',
                                'x-twitter-active-user': 'yes',
                                'content-type': 'application/json'
                            }
                        });
                        return r.json();
                    }
                """, [query_id, vars_encoded, features_encoded, BEARER_TOKEN])

                timeline = resp.get("data", {}).get("user", {}).get("result", {}).get("timeline_v2", resp.get("data", {}).get("user", {}).get("result", {}).get("timeline", {}))
                instructions = timeline.get("timeline", {}).get("instructions", [])

                entries = []
                next_cursor = None
                for inst in instructions:
                    for entry in inst.get("entries", []):
                        content = entry.get("content", {})
                        if content.get("entryType") == "TimelineTimelineItem":
                            tweet_result = content.get("itemContent", {}).get("tweet_results", {}).get("result", {})
                            legacy = tweet_result.get("legacy", {})
                            tweet_id = legacy.get("id_str", tweet_result.get("rest_id", ""))
                            if tweet_id:
                                user_legacy = tweet_result.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {})
                                entries.append({
                                    "platform_id": tweet_id,
                                    "item_type": "like",
                                    "metadata": json.dumps({
                                        "text": legacy.get("full_text", "")[:100],
                                        "author": user_legacy.get("screen_name", ""),
                                    }),
                                })
                        elif content.get("entryType") == "TimelineTimelineCursor":
                            if content.get("cursorType") == "Bottom":
                                next_cursor = content.get("value")

                for e in entries:
                    yield e

                if not next_cursor or not entries:
                    break
                cursor = next_cursor
        finally:
            await page.close()

    async def _fetch_replies(self) -> AsyncIterator[dict]:
        page = await self.context.new_page()
        try:
            await self._ensure_query_ids(page)

            if not self.user_id:
                info = await self.validate_session()
                self.user_id = info["user_id"]

            query_id = self._query_ids.get("UserTweetsAndReplies", "")
            cursor = None

            while True:
                variables = {
                    "userId": self.user_id,
                    "count": 20,
                    "includePromotedContent": False,
                }
                if cursor:
                    variables["cursor"] = cursor

                features = {
                    "rweb_tipjar_consumption_enabled": True,
                    "responsive_web_graphql_exclude_directive_enabled": True,
                    "verified_phone_label_enabled": False,
                    "responsive_web_graphql_timeline_navigation_enabled": True,
                    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                    "creator_subscriptions_tweet_preview_api_enabled": True,
                    "tweetypie_unmention_optimization_enabled": True,
                    "responsive_web_edit_tweet_api_enabled": True,
                    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
                    "view_counts_everywhere_api_enabled": True,
                    "longform_notetweets_consumption_enabled": True,
                    "responsive_web_twitter_article_tweet_consumption_enabled": True,
                    "tweet_awards_web_tipping_enabled": False,
                    "freedom_of_speech_not_reach_fetch_enabled": True,
                    "standardized_nudges_misinfo": True,
                    "longform_notetweets_rich_text_read_enabled": True,
                    "longform_notetweets_inline_media_enabled": True,
                    "responsive_web_enhance_cards_enabled": False,
                }

                vars_encoded = json.dumps(variables)
                features_encoded = json.dumps(features)

                resp = await page.evaluate("""
                    async ([queryId, varsEncoded, featuresEncoded, bearerToken]) => {
                        const url = '/i/api/graphql/' + queryId + '/UserTweetsAndReplies?variables=' +
                            encodeURIComponent(varsEncoded) +
                            '&features=' + encodeURIComponent(featuresEncoded);
                        const r = await fetch(url, {
                            headers: {
                                'authorization': 'Bearer ' + bearerToken,
                                'x-csrf-token': document.cookie.match(/ct0=([^;]+)/)?.[1] || '',
                                'x-twitter-auth-type': 'OAuth2Session',
                                'x-twitter-active-user': 'yes',
                                'content-type': 'application/json'
                            }
                        });
                        return r.json();
                    }
                """, [query_id, vars_encoded, features_encoded, BEARER_TOKEN])

                timeline = resp.get("data", {}).get("user", {}).get("result", {}).get("timeline_v2", resp.get("data", {}).get("user", {}).get("result", {}).get("timeline", {}))
                instructions = timeline.get("timeline", {}).get("instructions", [])

                entries = []
                next_cursor = None
                for inst in instructions:
                    for entry in inst.get("entries", []):
                        content = entry.get("content", {})
                        if content.get("entryType") == "TimelineTimelineItem":
                            tweet_result = content.get("itemContent", {}).get("tweet_results", {}).get("result", {})
                            legacy = tweet_result.get("legacy", {})
                            tweet_id = legacy.get("id_str", tweet_result.get("rest_id", ""))
                            # Only include replies (in_reply_to_status_id is set)
                            if tweet_id and legacy.get("in_reply_to_status_id_str"):
                                yield {
                                    "platform_id": tweet_id,
                                    "item_type": "comment",
                                    "metadata": json.dumps({
                                        "text": legacy.get("full_text", "")[:100],
                                        "reply_to": legacy.get("in_reply_to_screen_name", ""),
                                    }),
                                }
                        elif content.get("entryType") == "TimelineTimelineCursor":
                            if content.get("cursorType") == "Bottom":
                                next_cursor = content.get("value")

                if not next_cursor:
                    break
                cursor = next_cursor
        finally:
            await page.close()

    async def delete_item(self, item: dict) -> bool:
        page = await self.context.new_page()
        try:
            await self._ensure_query_ids(page)
            await page.goto(X_BASE, wait_until="domcontentloaded")

            if item["item_type"] == "like":
                query_id = self._query_ids.get("UnfavoriteTweet", "")
                variables = json.dumps({"tweet_id": item["platform_id"]})

                resp = await page.evaluate("""
                    async ([queryId, variables, bearerToken]) => {
                        const r = await fetch('/i/api/graphql/' + queryId + '/UnfavoriteTweet', {
                            method: 'POST',
                            headers: {
                                'authorization': 'Bearer ' + bearerToken,
                                'x-csrf-token': document.cookie.match(/ct0=([^;]+)/)?.[1] || '',
                                'x-twitter-auth-type': 'OAuth2Session',
                                'x-twitter-active-user': 'yes',
                                'content-type': 'application/json'
                            },
                            body: JSON.stringify({ variables: JSON.parse(variables) })
                        });
                        return r.json();
                    }
                """, [query_id, variables, BEARER_TOKEN])
                return "errors" not in resp

            elif item["item_type"] == "comment":
                query_id = self._query_ids.get("DeleteTweet", "")
                variables = json.dumps({"tweet_id": item["platform_id"]})

                resp = await page.evaluate("""
                    async ([queryId, variables, bearerToken]) => {
                        const r = await fetch('/i/api/graphql/' + queryId + '/DeleteTweet', {
                            method: 'POST',
                            headers: {
                                'authorization': 'Bearer ' + bearerToken,
                                'x-csrf-token': document.cookie.match(/ct0=([^;]+)/)?.[1] || '',
                                'x-twitter-auth-type': 'OAuth2Session',
                                'x-twitter-active-user': 'yes',
                                'content-type': 'application/json'
                            },
                            body: JSON.stringify({ variables: JSON.parse(variables) })
                        });
                        return r.json();
                    }
                """, [query_id, variables, BEARER_TOKEN])
                return "errors" not in resp

            return False
        finally:
            await page.close()
