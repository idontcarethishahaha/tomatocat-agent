"""
Spawn 插件 - 让主 Agent 可以创建和管理后台子 Agent 任务

提供工具：
- spawn: 创建后台子 Agent 任务（异步，不阻塞主对话）
- spawn_sync: 同步执行子任务（阻塞，等待完成）
- list_spawns: 列出运行中的后台任务
- cancel_spawn: 取消后台任务
"""

from __future__ import annotations

import logging
from typing import Any

from tomatocat.plugins import Plugin, tool

log = logging.getLogger("plugin.spawn")


class SpawnPlugin(Plugin):
    name = "spawn"
    desc = "后台子 Agent 任务管理"
    version = "0.1.0"

    def __init__(self) -> None:
        super().__init__()
        self._subagent_manager: Any = None
        self._delegation_policy: Any = None

    async def initialize(self) -> None:
        shared = getattr(self.context, "shared", {})
        self._subagent_manager = shared.get("subagent_manager")
        self._delegation_policy = shared.get("delegation_policy")

        if self._subagent_manager is None:
            log.warning("spawn 插件：未找到 subagent_manager，跳过注册")
            return

        log.info("[spawn] 后台任务插件已加载")

    @tool(
        "spawn",
        description=(
            "创建一个后台子 Agent 任务（异步，不阻塞当前对话）。"
            "适合预计需要多次工具调用或较长时间才能完成的复杂任务（如深度调研、批量处理、长时间搜索）。"
            "任务完成后会自动通知用户。简单问题请直接回答，不要调用此工具。"
        ),
    )
    async def _tool_spawn(
        self,
        event: object,
        task: str,
        label: str = "",
        profile: str = "",
        **kwargs,
    ) -> str:
        mgr = self._subagent_manager
        if mgr is None:
            return "😿 后台任务管理器未启用"

        session_key = kwargs.get("_session_key", "")
        origin_channel = kwargs.get("_channel", "unknown")
        # session_key 格式通常是 "channel:chat_id"，拆分开
        if ":" in session_key:
            origin_channel, origin_chat_id = session_key.split(":", 1)
        else:
            origin_chat_id = session_key or "unknown"

        running_count = mgr.get_running_count()

        decision = None
        if self._delegation_policy:
            decision = self._delegation_policy.decide(
                task=task,
                label=label,
                running_count=running_count,
                session_key=session_key,
            )

        if decision and not decision.should_spawn:
            return f"😿 {decision.block_reason}"

        selected_profile = profile or (decision.profile if decision else "research")

        result = await mgr.spawn(
            task=task,
            label=label,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            profile=selected_profile,
        )
        return result

    @tool(
        "spawn_sync",
        description="同步执行一个子任务，等待完成后返回结果。适合需要立即得到结果的短任务。",
    )
    async def _tool_spawn_sync(
        self,
        event: object,
        task: str,
        label: str = "",
        profile: str = "research",
        **kwargs,
    ) -> str:
        mgr = self._subagent_manager
        if mgr is None:
            return "😿 后台任务管理器未启用"

        result = await mgr.spawn_sync(
            task=task,
            label=label,
            profile=profile,
        )
        return result

    @tool(
        "list_spawns",
        description="列出当前运行中的后台子 Agent 任务",
    )
    async def _tool_list_spawns(self, event: object, **kwargs) -> str:
        mgr = self._subagent_manager
        if mgr is None:
            return "😿 后台任务管理器未启用"

        jobs = mgr.list_running_jobs()
        if not jobs:
            return "📭 当前没有运行中的后台任务"

        lines = [f"📋 运行中的后台任务（共 {len(jobs)} 个）："]
        for job in jobs:
            lines.append(
                f"  - [{job.get('job_id', '?')}] {job.get('label', '?')}\n"
                f"    profile: {job.get('profile', '?')} | 状态: {job.get('status', '?')}"
            )
        return "\n".join(lines)

    @tool(
        "cancel_spawn",
        description="取消一个正在运行的后台任务",
    )
    async def _tool_cancel_spawn(self, event: object, job_id: str, **kwargs) -> str:
        mgr = self._subagent_manager
        if mgr is None:
            return "😿 后台任务管理器未启用"

        ok = await mgr.cancel(job_id)
        if ok:
            return f"✅ 已请求取消任务 {job_id}"
        else:
            return f"❌ 未找到任务 {job_id} 或任务已完成"
