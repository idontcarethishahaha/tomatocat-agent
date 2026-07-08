from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _empty_str_list() -> list[str]:
    return []


def _empty_metadata() -> dict[str, Any]:
    return {}


@dataclass
class BeforeTurnCtx:
    session_key: str
    channel: str
    chat_id: str
    content: str
    timestamp: datetime
    retrieved_memory_block: str = ""
    history_messages: tuple[Any, ...] = field(default_factory=tuple)
    skill_names: list[str] = field(default_factory=_empty_str_list)
    abort: bool = False
    abort_reply: str = ""
    extra_hints: list[str] = field(default_factory=_empty_str_list)
    extra_metadata: dict[str, Any] = field(default_factory=_empty_metadata)


@dataclass
class BeforeReasoningCtx:
    session_key: str
    channel: str
    chat_id: str
    content: str
    timestamp: datetime
    skill_names: list[str] = field(default_factory=_empty_str_list)
    retrieved_memory_block: str = ""
    extra_hints: list[str] = field(default_factory=_empty_str_list)
    abort: bool = False
    abort_reply: str = ""


@dataclass
class PromptRenderCtx:
    session_key: str
    channel: str
    chat_id: str
    content: str
    timestamp: datetime
    history: list[dict[str, Any]] = field(default_factory=list)
    skill_names: list[str] = field(default_factory=_empty_str_list)
    retrieved_memory_block: str = ""
    extra_hints: list[str] = field(default_factory=_empty_str_list)


@dataclass
class BeforeStepCtx:
    session_key: str
    channel: str
    chat_id: str
    iteration: int
    input_tokens_estimate: int = 0
    visible_tool_names: frozenset[str] | None = None
    extra_hints: list[str] = field(default_factory=_empty_str_list)
    early_stop: bool = False
    early_stop_reply: str = ""


@dataclass(frozen=True)
class AfterStepCtx:
    session_key: str
    channel: str
    chat_id: str
    iteration: int
    tools_called: tuple[str, ...] = field(default_factory=tuple)
    partial_reply: str = ""
    tools_used_so_far: tuple[str, ...] = field(default_factory=tuple)
    has_more: bool = False
    early_stop: bool = False
    early_stop_reason: str = ""
    extra_metadata: dict[str, Any] = field(default_factory=_empty_metadata)


@dataclass
class AfterReasoningCtx:
    session_key: str
    channel: str
    chat_id: str
    tools_used: tuple[str, ...] = field(default_factory=tuple)
    thinking: str | None = None
    reply: str = ""
    media: list[str] = field(default_factory=_empty_str_list)
    outbound_metadata: dict[str, Any] = field(default_factory=_empty_metadata)


@dataclass(frozen=True)
class AfterTurnCtx:
    session_key: str
    channel: str
    chat_id: str
    reply: str = ""
    tools_used: tuple[str, ...] = field(default_factory=tuple)
    thinking: str | None = None
    will_dispatch: bool = True
    extra_metadata: dict[str, Any] = field(default_factory=_empty_metadata)


@dataclass(frozen=True)
class BeforeToolCallCtx:
    session_key: str
    channel: str
    chat_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AfterToolResultCtx:
    session_key: str
    channel: str
    chat_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    status: str = "success"