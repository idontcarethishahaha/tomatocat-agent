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
        self._observe_queue: asyncio.Queue[object] | None = None
        self._observe_task: asyncio.Task[None] | None = None
        self._closed = False

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
        failed_count = 0
        for r in results:
            if isinstance(r, Exception):
                failed_count += 1
                logger.error("event observer error: %s", r)
        if failed_count:
            logger.warning(
                "fanout completed with observer errors: event=%s failed=%d total=%d",
                type(event).__name__,
                failed_count,
                len(handlers),
            )

    async def _safe_observe(self, event: object, handler: EventHandler[object]) -> None:
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.exception("observer error for %s: %s", type(event).__name__, e)

    def enqueue(self, event: object) -> None:
        if self._closed:
            logger.warning("event enqueue ignored after close: %s", type(event).__name__)
            return
        queue = self._ensure_observe_queue()
        queue.put_nowait(event)

    async def drain(self) -> None:
        queue = self._observe_queue
        if queue is None:
            return
        self._ensure_observe_task()
        await queue.join()

    async def aclose(self) -> None:
        await self.drain()
        self._closed = True
        task = self._observe_task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _ensure_observe_queue(self) -> asyncio.Queue[object]:
        if self._observe_queue is None:
            self._observe_queue = asyncio.Queue()
        self._ensure_observe_task()
        return self._observe_queue

    def _ensure_observe_task(self) -> None:
        if self._closed:
            return
        if self._observe_task is not None and not self._observe_task.done():
            return
        task = asyncio.create_task(
            self._run_observe_queue(),
            name="event_bus_observe_queue",
        )
        self._observe_task = task
        task.add_done_callback(self._on_observe_task_done)

    async def _run_observe_queue(self) -> None:
        while True:
            queue = self._observe_queue
            if queue is None:
                return
            event = await queue.get()
            try:
                await self.fanout(event)
            finally:
                queue.task_done()

    def _on_observe_task_done(self, task: asyncio.Task[None]) -> None:
        if self._observe_task is task:
            self._observe_task = None
        if self._closed or task.cancelled():
            return
        try:
            exc = task.exception()
        except Exception as e:
            logger.warning("event dispatcher inspect failed: %s", e)
            exc = None
        if exc is not None:
            logger.error("event dispatcher stopped unexpectedly", exc_info=(type(exc), exc, exc.__traceback__))
        if self._observe_queue is not None:
            self._ensure_observe_task()


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
