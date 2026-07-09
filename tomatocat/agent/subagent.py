"""
SubAgent — 通用子 Agent

有固定工具集、独立的 LLM 循环，执行单个任务后返回结果。
可作为后台任务执行引擎，也可用于未来其他子 Agent 场景。

用法示例：
    agent = SubAgent(
        provider=provider,
        model="glm-4.5-flash",
        tools=[...],
        system_prompt="你是后台研究助手...",
    )
    result = await agent.run("调研最新的 agent 相关论文，总结后发给我")
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from .llm import LLMProvider, LLMResponse, ToolCall

logger = logging.getLogger("subagent")

_REFLECT_PROMPT = (
    "根据上述工具结果，决定下一步操作。\n"
    "若任务已完成，直接输出最终结果；若需要继续，继续调用工具。\n"
    "禁止把工具调用失败的原因写进最终回复，遇到失败时换个方式或跳过该步骤。"
)
_REFLECT_PROMPT_WARN = (
    "根据上述工具结果，决定下一步操作。\n"
    "⚠️ 步骤预算剩余 {remaining} 步，请优先完成核心目标，跳过非必要步骤。\n"
    "若任务已完成，直接输出最终结果；若需要继续，继续调用工具。\n"
    "禁止把工具调用失败的原因写进最终回复，遇到失败时换个方式或跳过该步骤。"
)
_REFLECT_PROMPT_LAST = (
    "⚠️ 步骤预算将在下一步耗尽。请立即优先完成核心目标，"
    "下一步将进入强制收尾。"
)
_CLEANUP_PROMPT = (
    "步骤预算已耗尽，进入强制收尾阶段。\n"
    "你必须调用 {tool_name}，如实汇报当前进度（已完成的步骤、产出路径、未完成的原因）。"
)
_WARN_THRESHOLD = 5
_MAX_TOOL_RESULT_CHARS = 100_000
_RECENT_TOOL_ROUNDS = 3
_CLEARED = "[已清除]"
_SUMMARY_MAX_TOKENS = 512
_INCOMPLETE_SUMMARY_PROMPT = (
    "当前任务未在步骤预算内完成，请直接输出中文进度总结，不要 JSON。\n"
    "必须覆盖：1) 已完成内容；2) 当前未完成点；3) 下一步计划。\n"
    "禁止输出模板句“已达到最大迭代次数”。"
)
_FORCED_FINAL_SUMMARY_PROMPT = (
    "你已用完任务执行预算，禁止再调用工具。\n"
    "现在必须直接输出中文最终总结，供主 agent 回传给用户。\n"
    "必须覆盖：1) 已完成内容；2) 当前未完成内容；3) 产出文件路径（如果有）；4) 下一步建议。\n"
    "禁止：继续规划工具调用；说“需要继续调用工具”；输出“已达到最大迭代次数”等模板句。"
)
_FORCED_FINAL_SUMMARY_FALLBACK = (
    "这次后台任务已先停在当前进度。我已经完成了一部分关键步骤，"
    "但还有剩余工作未收束；下一次可从当前检查点继续推进。"
)


def _trim_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将旧轮次的 tool result 替换为占位符，防止长对话累积撑爆上下文。"""
    tool_round_indices = [
        i
        for i, m in enumerate(messages)
        if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    if len(tool_round_indices) <= _RECENT_TOOL_ROUNDS:
        return messages

    cutoff = tool_round_indices[-_RECENT_TOOL_ROUNDS]

    out = []
    for i, m in enumerate(messages):
        if m.get("role") == "tool" and i < cutoff:
            out.append({**m, "content": _CLEARED})
        else:
            out.append(m)
    return out


class SubAgent:
    """有界子 Agent：固定工具集 + 单任务执行。

    与主 Agent 的区别：
    - 不维护对话历史，每次 run() 是独立的一次性任务
    - 工具集在构造时固定，不可在运行时扩展
    - 没有 session/memory 写入能力（由调用方决定是否保存结果）
    """

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        tools: list[Any],
        *,
        system_prompt: str = "",
        max_iterations: int = 30,
        max_tokens: int = 8192,
        mandatory_exit_tools: Sequence[str] = (),
    ) -> None:
        self._provider = provider
        self._model = model
        self._system_prompt = system_prompt
        self._max_iterations = max_iterations
        self._max_tokens = max_tokens
        self._mandatory_exit_tools = list(mandatory_exit_tools)
        self.last_exit_reason: str = "idle"
        self.iterations_used: int = 0
        self.tools_called: list[str] = []
        self._run_seq = 0
        self._tool_map: dict[str, Any] = {}
        self._tool_schemas: list[dict[str, Any]] = []
        self._register_tools(tools)

    def _register_tools(self, tools: list[Any]) -> None:
        for tool in tools:
            name = getattr(tool, "name", None) or tool.__class__.__name__
            schema = getattr(tool, "schema", None)
            if callable(schema):
                schema = schema()
            self._tool_map[name] = tool
            if schema:
                self._tool_schemas.append(schema)

    async def run(self, task: str) -> str:
        """执行任务并返回文本结果。

        - 任务正常完成：返回最终结果文本
        - 命中循环保护或达到最大迭代：返回进度收尾总结
        - LLM 调用等硬错误：返回空字符串
        """
        messages: list[dict[str, Any]] = []
        self.last_exit_reason = "running"
        self.iterations_used = 0
        self.tools_called = []
        self._run_seq += 1
        tool_session_key = f"subagent:{id(self)}:{self._run_seq}"

        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": task})

        for iteration in range(self._max_iterations):
            self.iterations_used = iteration + 1
            try:
                response = await self._provider.chat(
                    messages=_trim_tool_results(messages),
                    tools=self._tool_schemas if self._tool_schemas else None,
                    model=self._model,
                    max_tokens=self._max_tokens,
                    tool_choice="auto" if self._tool_schemas else None,
                )
            except Exception as e:
                logger.error("[subagent] LLM 调用失败 iteration=%d: %s", iteration, e)
                self.last_exit_reason = "error"
                return ""

            if not response.tool_calls:
                logger.info("[subagent] 任务完成 iterations=%d", iteration + 1)
                self.last_exit_reason = "completed"
                return (response.content or "").strip()

            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [tc.model_dump() for tc in response.tool_calls],
                }
            )

            for tc in response.tool_calls:
                logger.info(
                    "[subagent] 调用工具 %s args=%s",
                    tc.name,
                    str(tc.arguments)[:120],
                )
                try:
                    result_text = await self._execute_tool(tc, tool_session_key)
                except Exception as e:
                    logger.exception("[subagent] 工具执行失败 %s: %s", tc.name, e)
                    result_text = f"工具执行失败: {e}"

                if tc.name not in self.tools_called:
                    self.tools_called.append(tc.name)

                if len(result_text) > _MAX_TOOL_RESULT_CHARS:
                    original_len = len(result_text)
                    result_text = (
                        result_text[:_MAX_TOOL_RESULT_CHARS]
                        + f"\n...[结果已截断，原始长度 {original_len} 字符，超出上限 {_MAX_TOOL_RESULT_CHARS}]"
                    )
                    logger.warning(
                        "[subagent] 工具结果 %s 过长已截断 original=%d",
                        tc.name,
                        original_len,
                    )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                        "name": tc.name,
                    }
                )

            remaining = self._max_iterations - iteration - 1
            if remaining == 0:
                reflect = _REFLECT_PROMPT_LAST
            elif remaining <= _WARN_THRESHOLD:
                reflect = _REFLECT_PROMPT_WARN.format(remaining=remaining)
            else:
                reflect = _REFLECT_PROMPT
            messages.append({"role": "user", "content": reflect})

        logger.warning("[subagent] 已达到最大迭代次数 %d", self._max_iterations)
        return await self._force_final_summary(
            messages,
            reason="max_iterations",
            iteration=self._max_iterations,
        )

    async def _execute_tool(self, tc: ToolCall, session_key: str) -> str:
        tool = self._tool_map.get(tc.name)
        if tool is None:
            return f"未知工具: {tc.name}"

        execute = getattr(tool, "execute", None)
        if not callable(execute):
            return f"工具 {tc.name} 没有 execute 方法"

        result = await execute(**(tc.arguments or {}))

        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return str(result.get("content", result.get("text", result)))
        return str(result)

    async def _summarize_incomplete_progress(
        self,
        messages: list[dict[str, Any]],
        *,
        reason: str,
        iteration: int,
    ) -> str:
        prompt = (
            f"[收尾原因] {reason}\n"
            f"[已执行轮次] {iteration}\n\n" + _INCOMPLETE_SUMMARY_PROMPT
        )
        try:
            resp = await self._provider.chat(
                messages=messages + [{"role": "user", "content": prompt}],
                tools=None,
                model=self._model,
                max_tokens=min(_SUMMARY_MAX_TOKENS, self._max_tokens),
            )
            text = (resp.content or "").strip()
            if text:
                return text
        except Exception as e:
            logger.warning("[subagent] 生成收尾总结失败: %s", e)
        return "本轮步骤预算已用完：已完成部分关键步骤，但仍有未完成项，下一轮将从当前检查点继续推进。"

    async def _force_final_summary(
        self,
        messages: list[dict[str, Any]],
        *,
        reason: str,
        iteration: int,
    ) -> str:
        prompt = (
            f"[结束原因] {reason}\n"
            f"[已执行任务轮次] {iteration}\n\n" + _FORCED_FINAL_SUMMARY_PROMPT
        )
        try:
            resp = await self._provider.chat(
                messages=messages + [{"role": "user", "content": prompt}],
                tools=None,
                model=self._model,
                max_tokens=min(_SUMMARY_MAX_TOKENS, self._max_tokens),
            )
            text = (resp.content or "").strip()
            if text:
                self.last_exit_reason = "forced_summary"
                return text
        except Exception as e:
            logger.warning("[subagent] 强制最终总结失败: %s", e)
        self.last_exit_reason = "forced_summary_fallback"
        return _FORCED_FINAL_SUMMARY_FALLBACK
