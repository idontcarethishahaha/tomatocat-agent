"""
ToolLoopGuard 插件 - 工具循环防护

检测连续重复的工具调用并提前截断，防止无限循环消耗资源。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from tomatocat.plugins import Plugin

logger = logging.getLogger("plugin.tool_loop_guard")

_DEFAULT_REPEAT_LIMIT = 3
_DENY_PREFIX = "tool_loop_guard:"
_EXCLUDED_TOOLS = frozenset({"task_output", "task_stop", "schedule"})


@dataclass
class _LoopState:
    signature: str = ""
    repeat_count: int = 0


class ToolLoopGuardPlugin(Plugin):
    name = "tool_loop_guard"
    version = "0.1.0"
    desc = "检测连续重复的工具调用并提前截断"

    def __init__(self) -> None:
        super().__init__()
        self._states: dict[str, _LoopState] = {}
        self._repeat_limit = _DEFAULT_REPEAT_LIMIT

    async def initialize(self) -> None:
        config = getattr(self.context, "config", None)
        raw_limit = _DEFAULT_REPEAT_LIMIT
        if config:
            plugin_config = getattr(config, "tool_loop_guard", None)
            if plugin_config:
                raw_limit = getattr(plugin_config, "repeat_limit", _DEFAULT_REPEAT_LIMIT)

        try:
            self._repeat_limit = max(2, int(raw_limit))
        except (TypeError, ValueError):
            self._repeat_limit = _DEFAULT_REPEAT_LIMIT

        logger.info("[tool_loop_guard] 工具循环防护已启用，重复限制: %d", self._repeat_limit)

    async def on_tool_pre(self, tool_name: str, arguments: dict[str, Any], session_key: str = "", **kwargs) -> tuple[bool, str]:
        """工具调用前检查，返回 (是否允许, 拒绝原因)"""
        if tool_name in _EXCLUDED_TOOLS:
            return True, ""

        signature = self._signature(tool_name, arguments)
        state_key = f"{session_key}:{tool_name}"

        state = self._states.setdefault(state_key, _LoopState())

        if signature == state.signature:
            state.repeat_count += 1
        else:
            state.signature = signature
            state.repeat_count = 1

        if state.repeat_count >= self._repeat_limit:
            reason = f"{_DENY_PREFIX}连续重复调用工具 {tool_name} {state.repeat_count} 次，已截断并进入收尾。"
            logger.warning(f"[tool_loop_guard] 拦截重复调用: {tool_name} ({state.repeat_count}次)")
            return False, reason

        return True, ""

    def _signature(self, tool_name: str, arguments: dict[str, Any]) -> str:
        args = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        return f"{tool_name}:{args}"

    def reset_session(self, session_key: str) -> None:
        """重置指定会话的循环状态"""
        keys_to_remove = [k for k in self._states if k.startswith(f"{session_key}:")]
        for key in keys_to_remove:
            self._states.pop(key, None)

    def get_loop_states(self) -> dict[str, Any]:
        """获取当前所有循环状态"""
        return {
            key: {
                "signature": state.signature,
                "repeat_count": state.repeat_count,
            }
            for key, state in self._states.items()
        }
