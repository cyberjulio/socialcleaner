from abc import ABC, abstractmethod
from typing import AsyncIterator
from playwright.async_api import BrowserContext


class DailyCapReached(Exception):
    """Raised when the platform's daily action cap is hit."""


class PlatformClient(ABC):
    def __init__(self, context: BrowserContext, cookies: dict[str, str]):
        self.context = context
        self.cookies = cookies
        self._log_callback = None
        self._progress_callback = None
        self._event_callback = None

    def set_log_callback(self, callback):
        """Set a callback for live logging to the frontend."""
        self._log_callback = callback

    def set_progress_callback(self, callback):
        """Set a callback for reporting batch progress (deleted count, total)."""
        self._progress_callback = callback

    def set_event_callback(self, callback):
        """Set a callback for emitting arbitrary SSE events."""
        self._event_callback = callback

    async def _emit_event(self, event_type: str, data: dict):
        """Emit a custom SSE event to the frontend."""
        if self._event_callback:
            await self._event_callback(event_type, data)

    async def _log(self, message: str, level: str = "info"):
        """Log to both Python logger and optional frontend callback."""
        import logging
        logger = logging.getLogger(self.__class__.__module__)
        getattr(logger, level, logger.info)(message)
        if self._log_callback:
            await self._log_callback(message, level)

    async def _report_progress(self, deleted: int, total: int | None = None):
        """Report batch operation progress to the engine."""
        if self._progress_callback:
            await self._progress_callback(deleted, total)

    @abstractmethod
    async def validate_session(self) -> dict:
        """Verify cookies work. Return {"username": "...", "user_id": "..."}."""
        ...

    @abstractmethod
    async def fetch_items(self, target_type: str) -> AsyncIterator[dict]:
        """Yield items (likes or comments) via pagination."""
        ...

    @abstractmethod
    async def delete_item(self, item: dict) -> bool:
        """Delete a single item. Return True on success."""
        ...

    async def close(self):
        await self.context.close()
