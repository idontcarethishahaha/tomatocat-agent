"""渠道基类"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Awaitable

# MessageHandler 接收 (session_key, text, channel, **kwargs)
# kwargs 可能包含 on_delta 等流式回调
# 返回 dict: {"text": str, "media_paths": list[Path], "thinking": str, "tool_calls": list}
# 向后兼容：也接受 str（会被 _handle_message 包装成 dict）
MessageHandler = Callable[..., Awaitable["dict[str, Any] | str"]]


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

    async def _handle_message(
        self, session_key: str, text: str, channel: str, **kwargs: Any
    ) -> dict[str, Any]:
        """调用消息处理器，统一返回 dict 格式。

        返回值保证为 {"text": str, "media_paths": list[Path], ...}。
        """
        if self._handler is None:
            return {"text": "未设置消息处理器", "media_paths": []}
        result = await self._handler(session_key, text, channel, **kwargs)
        if isinstance(result, str):
            return {"text": result, "media_paths": []}
        if isinstance(result, dict):
            media_paths = result.get("media_paths")
            return {
                "text": str(result.get("text", "")),
                "media_paths": list(media_paths) if media_paths else [],
                "thinking": result.get("thinking", ""),
                "tool_calls": result.get("tool_calls", []),
            }
        return {"text": str(result), "media_paths": []}
