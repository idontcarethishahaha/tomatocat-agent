"""渠道基类"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Awaitable


MessageHandler = Callable[[str, str, str], Awaitable[str]]


class Channel(ABC):
    """所有通信渠道的基类"""

    name: str = "base"

    def __init__(self) -> None:
        self._handler: MessageHandler | None = None

    def set_handler(self, handler: MessageHandler) -> None:
        self._handler = handler

    @abstractmethod
    async def start(self) -> None:
        ...

    @abstractmethod
    async def stop(self) -> None:
        ...

    async def _handle_message(self, session_key: str, text: str, channel: str) -> str:
        if self._handler is None:
            return "未设置消息处理器"
        return await self._handler(session_key, text, channel)
