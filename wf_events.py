from __future__ import annotations

from __future__ import annotations

import inspect
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, DefaultDict


Handler = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class Subscription:
    event: str
    handler: Handler


class EventBus:
    def __init__(self) -> None:
        self._handlers: DefaultDict[str, list[Handler]] = defaultdict(list)

    def on(self, event: str, handler: Handler) -> Subscription:
        self._handlers[event].append(handler)
        return Subscription(event=event, handler=handler)

    def off(self, sub: Subscription) -> None:
        handlers = self._handlers.get(sub.event)
        if not handlers:
            return
        try:
            handlers.remove(sub.handler)
        except ValueError:
            return

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        for handler in list(self._handlers.get(event, [])):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result

