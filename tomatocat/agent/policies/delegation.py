"""
策略委托系统 - 智能决定何时使用子 Agent、何时使用工具

根据任务特征和系统状态，自动决策：
- 是否应该创建后台子 Agent
- 使用哪种 profile（research/scripting/general）
- 是否应该限制并发数量
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger("policy.delegation")

SpawnDecisionSource = Literal["heuristic", "llm", "manual_rule"]
SpawnDecisionConfidence = Literal["high", "medium", "low"]
SpawnDecisionReasonCode = Literal[
    "long_running",
    "context_isolation_needed",
    "tool_chain_heavy",
    "stay_inline",
    "fallback_inline",
]

_MAX_CONCURRENT_SPAWNS = 3


@dataclass(frozen=True)
class SpawnDecisionMeta:
    source: SpawnDecisionSource
    confidence: SpawnDecisionConfidence
    reason_code: SpawnDecisionReasonCode


@dataclass(frozen=True)
class SpawnDecision:
    should_spawn: bool
    label: str
    meta: SpawnDecisionMeta
    profile: str = "research"
    block_reason: str = ""


@dataclass(frozen=True)
class TaskAnalysis:
    is_long_running: bool
    needs_context_isolation: bool
    tool_chain_complexity: int
    estimated_steps: int
    requires_execution: bool
    requires_research: bool


class DelegationPolicy:
    """智能委托策略：根据任务特征决定是否使用子 Agent"""

    def __init__(self, max_concurrent: int = _MAX_CONCURRENT_SPAWNS) -> None:
        self._max_concurrent = max_concurrent

    def decide(
        self,
        *,
        task: str,
        label: str | None = None,
        running_count: int = 0,
        session_key: str = "",
    ) -> SpawnDecision:
        """做出是否创建子 Agent 的决策"""
        normalized_label = (label or (task or "")[:30] or "").strip()
        analysis = self._analyze_task(task)

        if running_count >= self._max_concurrent:
            return SpawnDecision(
                should_spawn=False,
                label=normalized_label,
                block_reason=(
                    f"已有 {running_count} 个并发子任务在运行，上限 {self._max_concurrent}，"
                    "请等待当前任务完成后再试"
                ),
                meta=SpawnDecisionMeta(
                    source="heuristic",
                    confidence="high",
                    reason_code="stay_inline",
                ),
            )

        if analysis.is_long_running or analysis.tool_chain_complexity >= 3:
            profile = self._select_profile(analysis)
            return SpawnDecision(
                should_spawn=True,
                label=normalized_label,
                profile=profile,
                meta=SpawnDecisionMeta(
                    source="heuristic",
                    confidence="high",
                    reason_code="tool_chain_heavy",
                ),
            )

        if analysis.needs_context_isolation:
            return SpawnDecision(
                should_spawn=True,
                label=normalized_label,
                profile=self._select_profile(analysis),
                meta=SpawnDecisionMeta(
                    source="heuristic",
                    confidence="medium",
                    reason_code="context_isolation_needed",
                ),
            )

        return SpawnDecision(
            should_spawn=True,
            label=normalized_label,
            profile=self._select_profile(analysis),
            meta=SpawnDecisionMeta(
                source="llm",
                confidence="high",
                reason_code="tool_chain_heavy",
            ),
        )

    def _analyze_task(self, task: str) -> TaskAnalysis:
        """分析任务特征"""
        lower_task = task.lower()

        is_long_running = any(
            keyword in lower_task
            for keyword in [
                "调研",
                "研究",
                "分析",
                "报告",
                "总结",
                "整理",
                "收集",
                "搜索",
                "查找",
                "学习",
                "教程",
                "论文",
                "资料",
            ]
        )

        needs_context_isolation = any(
            keyword in lower_task
            for keyword in [
                "后台",
                "异步",
                "稍后",
                "等待",
                "不阻塞",
                "独立",
            ]
        )

        tool_chain_complexity = 0
        if "搜索" in lower_task or "查找" in lower_task:
            tool_chain_complexity += 1
        if "阅读" in lower_task or "抓取" in lower_task or "获取" in lower_task:
            tool_chain_complexity += 1
        if "分析" in lower_task or "处理" in lower_task:
            tool_chain_complexity += 1
        if "生成" in lower_task or "创建" in lower_task or "写" in lower_task:
            tool_chain_complexity += 1

        estimated_steps = min(5 + tool_chain_complexity * 3, 30)

        requires_execution = any(
            keyword in lower_task
            for keyword in ["执行", "运行", "编译", "安装", "脚本", "命令"]
        )

        requires_research = any(
            keyword in lower_task
            for keyword in ["搜索", "调研", "研究", "查找", "论文", "资料"]
        )

        return TaskAnalysis(
            is_long_running=is_long_running,
            needs_context_isolation=needs_context_isolation,
            tool_chain_complexity=tool_chain_complexity,
            estimated_steps=estimated_steps,
            requires_execution=requires_execution,
            requires_research=requires_research,
        )

    def _select_profile(self, analysis: TaskAnalysis) -> str:
        """根据任务分析选择合适的 profile"""
        if analysis.requires_execution and analysis.requires_research:
            return "general"
        if analysis.requires_execution:
            return "scripting"
        return "research"


class DelegationPolicyPlugin:
    """策略委托系统插件"""

    name = "delegation_policy"

    def __init__(self) -> None:
        self._policy = DelegationPolicy()

    async def initialize(self) -> None:
        logger.info("[delegation_policy] 策略委托系统已启用")

    def get_policy(self) -> DelegationPolicy:
        return self._policy

    def analyze_task(self, task: str) -> TaskAnalysis:
        return self._policy._analyze_task(task)

    def decide(self, **kwargs) -> SpawnDecision:
        return self._policy.decide(**kwargs)
