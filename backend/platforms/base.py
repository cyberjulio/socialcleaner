from abc import ABC, abstractmethod
from typing import AsyncIterator
from playwright.async_api import BrowserContext


class PlatformClient(ABC):
    def __init__(self, context: BrowserContext, cookies: dict[str, str]):
        self.context = context
        self.cookies = cookies
        self._log_callback = None

    def set_log_callback(self, callback):
        """Set a callback for live logging to the frontend."""
        self._log_callback = callback

    async def _log(self, message: str, level: str = "info"):
        """Log to both Python logger and optional frontend callback."""
        import logging
        logger = logging.getLogger(self.__class__.__module__)
        getattr(logger, level, logger.info)(message)
        if self._log_callback:
            await self._log_callback(message, level)

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
