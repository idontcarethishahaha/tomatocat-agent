"""事件总线 - 番茄猫内部模块解耦核心"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Awaitable
from typing import TypeVar, Generic, cast

logger = logging.getLogger(__name__)

E = TypeVar("E")
EventHandler = Callable[[E], Awaitable[E | None] | E | None]


class EventBus:
    """事件总线：支持顺序拦截（emit）和并发观察（observe）"""

    def __init__(self) -> None:
        self._handlers: dict[type[object], list[EventHandler[object]]] = {}

    def on(self, event_type: type[E], handler: EventHandler[E]) -> None:
        handlers = self._handlers.setdefault(cast(type[object], event_type), [])
        handlers.append(cast(EventHandler[object], handler))

    async def emit(self, event: E) -> E:
        event_type = type(event)
        for handler in self._handlers.get(event_type, []):
            result = handler(event)
            if asyncio.iscoroutine(result):
                result = await result
            if result is not None:
                event = cast(E, result)
        return event

    async def fanout(self, event: object) -> None:
        handlers = list(self._handlers.get(type(event), []))
        if not handlers:
            return
        tasks = []
        for handler in handlers:
            coro = self._safe_observe(event, handler)
            tasks.append(asyncio.create_task(coro))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.error("event observer error: %s", r)

    async def _safe_observe(self, event: object, handler: EventHandler[object]) -> None:
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.exception("observer error for %s: %s", type(event).__name__, e)


# ── 常用事件类型 ────────────────────────────────────────────────────────────

class InboundMessage:
    """入站消息：用户发来的消息"""
    def __init__(self, session_key: str, text: str, channel: str = "cli") -> None:
        self.session_key = session_key
        self.text = text
        self.channel = channel


class OutboundMessage:
    """出站消息：要发给用户的消息"""
    def __init__(self, session_key: str, text: str, channel: str = "cli") -> None:
        self.session_key = session_key
        self.text = text
        self.channel = channel


class TurnStartEvent:
    """一轮对话开始"""
    def __init__(self, session_key: str) -> None:
        self.session_key = session_key


class TurnEndEvent:
    """一轮对话结束"""
    def __init__(self, session_key: str, response: str) -> None:
        self.session_key = session_key
        self.response = response
