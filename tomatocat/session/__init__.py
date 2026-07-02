"""会话管理 - 管理每个用户的对话历史"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    role: str
    content: str | list[dict[str, Any]]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


class Session:
    def __init__(self, session_key: str, max_history: int = 40) -> None:
        self.session_key = session_key
        self.max_history = max_history
        self.messages: list[ChatMessage] = []

    def add_message(self, message: ChatMessage) -> None:
        self.messages.append(message)
        self._trim_history()

    def add_user_message(self, text: str, images: list[dict[str, Any]] | None = None) -> None:
        """添加用户消息。images 为 OpenAI 兼容格式的 image_url 列表。"""
        if images:
            content: list[dict[str, Any]] = list(images)
            content.append({"type": "text", "text": text})
            self.add_message(ChatMessage(role="user", content=content))
        else:
            self.add_message(ChatMessage(role="user", content=text))

    def add_assistant_message(self, text: str, tool_calls: list[dict] | None = None) -> None:
        self.add_message(ChatMessage(
            role="assistant",
            content=text,
            tool_calls=tool_calls or [],
        ))

    def add_tool_result(self, tool_call_id: str, tool_name: str, result: str) -> None:
        self.add_message(ChatMessage(
            role="tool",
            content=result,
            tool_call_id=tool_call_id,
            name=tool_name,
        ))

    def get_messages(self) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.messages]

    def _trim_history(self) -> None:
        if len(self.messages) > self.max_history:
            system_msg = self.messages[0] if self.messages and self.messages[0].role == "system" else None
            remaining = self.messages[-(self.max_history - 1):] if system_msg else self.messages[-self.max_history:]
            self.messages = ([system_msg] if system_msg else []) + remaining


class SessionManager:
    def __init__(self, workspace: Path | None = None, max_history: int = 40) -> None:
        self._sessions: dict[str, Session] = {}
        self._max_history = max_history
        self._workspace = workspace

    def get_or_create(self, session_key: str) -> Session:
        if session_key not in self._sessions:
            session = Session(session_key, max_history=self._max_history)
            self._sessions[session_key] = session
            logger.info("[session] 创建新会话: %s", session_key)
        return self._sessions[session_key]

    def get(self, session_key: str) -> Session | None:
        return self._sessions.get(session_key)

    def reset(self, session_key: str) -> None:
        if session_key in self._sessions:
            del self._sessions[session_key]
            logger.info("[session] 重置会话: %s", session_key)
