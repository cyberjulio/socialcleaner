import asyncio
import random
import time
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Human-like rate limiter with tiered delays, session breaks,
    and daily caps to avoid bot detection.

    Tiers:
    - Warm-up (first 5 actions): 8-15s between actions
    - Cruising (actions 6-50): 4-10s between actions
    - Every 30-60 actions: 2-5 minute "scroll break"
    - Every 150-200 actions: 15-45 minute "session break"
    - Daily cap: configurable per platform

    All delays include Gaussian jitter.
    """

    DAILY_CAPS = {
        "instagram": 400,
        "twitter": 350,
    }

    def __init__(self, platform: str):
        self.platform = platform
        self.action_count = 0
        self.daily_count = 0
        self.day_start = time.time()
        self._next_break_at = random.randint(30, 60)
        self._next_session_break_at = random.randint(150, 200)
        self._paused = False
        self._rate_limited_until: float = 0

    def _jitter(self, base: float) -> float:
        """Add Gaussian jitter to a base delay."""
        jitter = random.gauss(0, base * 0.15)
        return max(1.0, base + jitter)

    async def wait(self):
        """Wait the appropriate amount before the next action."""
        # Check daily cap
        if time.time() - self.day_start > 86400:
            self.daily_count = 0
            self.day_start = time.time()

        daily_cap = self.DAILY_CAPS.get(self.platform, 300)
        if self.daily_count >= daily_cap:
            wait_time = 86400 - (time.time() - self.day_start)
            logger.info(f"Daily cap reached ({daily_cap}), waiting {wait_time/3600:.1f}h")
            await asyncio.sleep(max(wait_time, 60))
            self.daily_count = 0
            self.day_start = time.time()

        # Check if we're rate-limited by the platform
        if self._rate_limited_until > time.time():
            wait = self._rate_limited_until - time.time()
            logger.info(f"Rate limited, waiting {wait:.0f}s")
            await asyncio.sleep(wait)

        self.action_count += 1
        self.daily_count += 1

        # Session break (long pause)
        if self.action_count >= self._next_session_break_at:
            pause = self._jitter(random.uniform(900, 2700))  # 15-45 min
            logger.info(f"Session break: pausing {pause/60:.1f} minutes after {self.action_count} actions")
            await asyncio.sleep(pause)
            self.action_count = 0
            self._next_break_at = random.randint(30, 60)
            self._next_session_break_at = random.randint(150, 200)
            return

        # Scroll break (medium pause)
        if self.action_count >= self._next_break_at:
            pause = self._jitter(random.uniform(120, 300))  # 2-5 min
            logger.info(f"Scroll break: pausing {pause/60:.1f} minutes after {self.action_count} actions")
            await asyncio.sleep(pause)
            self._next_break_at = self.action_count + random.randint(30, 60)
            return

        # Normal delay based on tier
        if self.action_count <= 5:
            # Warm-up: slow and cautious
            delay = self._jitter(random.uniform(8, 15))
        elif self.action_count <= 50:
            # Cruising: natural browsing speed
            delay = self._jitter(random.uniform(4, 10))
        else:
            # Sustained: slightly slower to stay safe
            delay = self._jitter(random.uniform(5, 12))

        await asyncio.sleep(delay)

    def on_rate_limit(self, retry_after: int = 0):
        """Called when the platform returns a rate limit error."""
        if retry_after > 0:
            self._rate_limited_until = time.time() + retry_after
        else:
            # Default: back off 30-60 minutes
            backoff = random.uniform(1800, 3600)
            self._rate_limited_until = time.time() + backoff
            logger.warning(f"Rate limited! Backing off for {backoff/60:.0f} minutes")

    def on_checkpoint_required(self):
        """Called when Instagram requires a checkpoint (verification)."""
        # Pause for a long time - user needs to manually verify
        self._rate_limited_until = time.time() + 7200  # 2 hours
        logger.warning("Checkpoint required! Pausing for 2 hours. User must verify in app.")
