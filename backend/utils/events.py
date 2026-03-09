import asyncio
import json
from collections import defaultdict


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subscribers[task_id].append(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue):
        if q in self._subscribers[task_id]:
            self._subscribers[task_id].remove(q)
        if not self._subscribers[task_id]:
            del self._subscribers[task_id]

    async def publish(self, task_id: str, event_type: str, data: dict):
        for q in self._subscribers.get(task_id, []):
            try:
                q.put_nowait({"event": event_type, "data": data})
            except asyncio.QueueFull:
                pass


event_bus = EventBus()
