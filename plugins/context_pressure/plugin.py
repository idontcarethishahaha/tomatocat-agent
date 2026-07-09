"""
ContextPressure 插件 - 上下文压力管理

当对话历史过长导致上下文压力过高时，自动请求阶段性收尾，
防止超出模型的上下文窗口限制。
"""

from __future__ import annotations

import logging
from typing import Any

from tomatocat.plugins import Plugin

logger = logging.getLogger("plugin.context_pressure")

_MODEL_CONTEXT_WINDOW_TOKENS = 1_000_000
_CONTEXT_PRESSURE_STOP_THRESHOLD_TOKENS = _MODEL_CONTEXT_WINDOW_TOKENS * 80 // 100


class ContextPressurePlugin(Plugin):
    name = "context_pressure"
    version = "0.1.0"
    desc = "上下文压力过高时请求被动循环阶段性收尾"

    def __init__(self) -> None:
        super().__init__()
        self._last_context_tokens = 0

    async def initialize(self) -> None:
        logger.info("[context_pressure] 上下文压力管理已启用，阈值: %d tokens", _CONTEXT_PRESSURE_STOP_THRESHOLD_TOKENS)

    async def on_after_step(self, step_data: dict[str, Any]) -> None:
        """在每个工具步骤后检查上下文压力"""
        context_tokens = step_data.get("context_tokens", 0)
        if not isinstance(context_tokens, int):
            context_tokens = 0

        self._last_context_tokens = context_tokens

        if context_tokens > _CONTEXT_PRESSURE_STOP_THRESHOLD_TOKENS:
            logger.warning(
                "[context_pressure] 上下文压力过高: %d tokens，已超过阈值 %d，请求阶段性收尾",
                context_tokens,
                _CONTEXT_PRESSURE_STOP_THRESHOLD_TOKENS,
            )

    def should_stop(self) -> bool:
        """检查是否应该停止当前对话轮次"""
        return self._last_context_tokens > _CONTEXT_PRESSURE_STOP_THRESHOLD_TOKENS

    def get_pressure_status(self) -> dict[str, int]:
        """获取当前上下文压力状态"""
        return {
            "current_tokens": self._last_context_tokens,
            "threshold": _CONTEXT_PRESSURE_STOP_THRESHOLD_TOKENS,
            "max_window": _MODEL_CONTEXT_WINDOW_TOKENS,
            "percent_used": int(self._last_context_tokens / _MODEL_CONTEXT_WINDOW_TOKENS * 100),
        }
