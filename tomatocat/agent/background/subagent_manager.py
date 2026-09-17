"""
SubagentManager — 后台子 Agent 任务管理器

管理后台子 Agent 任务，支持同步和异步两种模式：
- spawn_sync(): 同步执行，阻塞当前对话直到完成
- spawn(): 异步执行，后台独立运行，完成后通过事件总线通知
"""

from __future__ import annotations

import asyncio
import logging
import uuid
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone as _tz_utc
from pathlib import Path
from typing import Any

from ..llm import LLMProvider
from ..subagent import SubAgent
from .subagent_profiles import (
    PROFILE_RESEARCH,
    SubagentRuntime,
    SubagentSpec,
    build_spawn_spec,
)
from ...task_store import TaskStore

logger = logging.getLogger("subagent_manager")

_RESULT_MAX_CHARS = 12_000
_SYNC_RESULT_MAX_CHARS = 100_000
_SPAWN_MAX_ITERATIONS = 50
_SYNC_MAX_ITERATIONS = 10
_SPAWN_TIMEOUT_SECONDS = 30 * 60
_SYNC_TIMEOUT_SECONDS = 10 * 60
_DEFAULT_TOOL_TIMEOUT_SECONDS = 120
_SHELL_TOOL_TIMEOUT_SECONDS = 660


def _retry_policy_for_profile(profile: str) -> str:
    return "safe" if profile == PROFILE_RESEARCH else "confirm"


class SubAgentToolWrapper:
    """包装 PluginManager 的工具，让 SubAgent 可以调用。

    文件类工具（read_file/write_file/edit_file/list_dir）的路径会被限制在 task_dir 内，
    防止子 Agent 把文件写到 workspace 根目录造成混乱。
    """

    _FILE_TOOLS = {"read_file", "write_file", "edit_file", "list_dir"}

    def __init__(self, tool_info: Any, plugin_manager: Any, task_dir: Path) -> None:
        self._tool_info = tool_info
        self._plugin_manager = plugin_manager
        self._task_dir = task_dir

    @property
    def name(self) -> str:
        return self._tool_info.name

    @property
    def description(self) -> str:
        return getattr(self._tool_info, "description", "")

    def schema(self) -> dict[str, Any]:
        from tomatocat.plugins.decorators import get_tool_definition
        schema = get_tool_definition(self._tool_info)
        # 在 schema 描述中追加路径限制提示
        if self.name in self._FILE_TOOLS and "description" in schema.get("function", {}):
            orig = schema["function"]["description"]
            schema["function"]["description"] = (
                orig + f"\n⚠️ 所有路径必须在子任务目录内，当前目录: {self._task_dir}"
            )
        return schema

    async def execute(self, **kwargs: Any) -> str:
        # 文件类工具：把相对路径限制在 task_dir 内
        if self.name in self._FILE_TOOLS:
            kwargs = self._rewrite_file_paths(kwargs)
        timeout = _SHELL_TOOL_TIMEOUT_SECONDS if self.name in {"shell", "task_output", "task_stop"} else _DEFAULT_TOOL_TIMEOUT_SECONDS
        try:
            return await asyncio.wait_for(
                self._plugin_manager.execute_tool(self._tool_info.name, kwargs),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.error("[subagent] tool timeout tool=%s timeout=%ss", self.name, timeout)
            return f"工具执行超时（>{timeout}秒）: {self.name}"

    def _rewrite_file_paths(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """把文件路径参数限制在 task_dir 内，禁止写到 workspace 根目录"""
        kwargs = dict(kwargs)
        path_key = "path"
        if path_key not in kwargs:
            return kwargs

        raw_path = kwargs[path_key]
        if not raw_path:
            return kwargs

        p = Path(str(raw_path))
        task_root = self._task_dir.resolve()
        candidate = p.resolve() if p.is_absolute() else (task_root / p).resolve()
        try:
            candidate.relative_to(task_root)
            p = candidate
        except ValueError:
            # Never follow ../ or an absolute path outside the sandbox.
            p = task_root / p.name
        kwargs[path_key] = str(p)
        return kwargs


@dataclass(frozen=True)
class RunningSubagentJob:
    job_id: str
    label: str
    task: str
    profile: str
    origin_channel: str
    origin_chat_id: str
    task_dir: str
    retry_count: int
    started_at: str
    status: str = "running"


class SubagentManager:
    """Manage background subagent jobs and announce completion to the main loop."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        workspace: Path,
        event_bus: Any,
        model: str,
        max_tokens: int,
        plugin_manager: Any = None,
        send_fn: Any = None,
        task_store: TaskStore | None = None,
    ) -> None:
        self._workspace = workspace
        self._event_bus = event_bus
        self._plugin_manager = plugin_manager
        self._send_fn = send_fn
        self._task_store = task_store
        self._runtime = SubagentRuntime(
            provider=provider,
            model=model,
            max_tokens=max_tokens,
        )
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._running_jobs: dict[str, RunningSubagentJob] = {}
        self._cancel_announced: set[str] = set()
        self._stopping = False

    def _spawn_jobs_dir(self) -> Path:
        root = self._workspace / "subagent-runs"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _job_task_dir(self, job_id: str) -> Path:
        task_dir = self._spawn_jobs_dir() / job_id
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_dir

    async def spawn_sync(
        self,
        *,
        task: str,
        label: str | None = None,
        profile: str = PROFILE_RESEARCH,
    ) -> str:
        """同步执行 subagent，阻塞当前 turn 直到完成，结果作为 tool result 直接返回。

        适合：调研后需要立即回复用户的任务，预计 ≤ 10 次工具调用。
        """
        job_id = uuid.uuid4().hex
        display_label = (label or task[:30] or job_id).strip()
        task_dir = self._job_task_dir(job_id)
        if self._task_store:
            self._task_store.create(job_id, "subagent", payload=json.dumps({"task": task}, ensure_ascii=False), status="queued", retry_count=0, label=display_label, profile=profile, retry_policy=_retry_policy_for_profile(profile))

        logger.info(
            "[spawn_sync] started job_id=%s label=%r profile=%s",
            job_id,
            display_label,
            profile,
        )

        subagent = self._build_subagent(
            task_dir=task_dir,
            profile=profile,
            max_iterations=_SYNC_MAX_ITERATIONS,
        )
        try:
            result = await asyncio.wait_for(subagent.run(task), timeout=_SYNC_TIMEOUT_SECONDS)
            exit_reason = getattr(subagent, "last_exit_reason", None) or "completed"
        except asyncio.TimeoutError:
            logger.error("[spawn_sync] subagent timeout job_id=%s", job_id)
            result = "后台任务执行超时"
            exit_reason = "timeout"
        except Exception as e:
            logger.exception("[spawn_sync] subagent failed job_id=%s err=%s", job_id, e)
            result = f"执行出错：{e}"
            exit_reason = "error"

        if self._task_store:
            final_status = "failed" if exit_reason in {"error", "timeout"} else (
                "partial" if exit_reason in {"forced_summary", "forced_summary_fallback"} else "completed"
            )
            self._task_store.update(
                job_id,
                final_status,
                result=result,
                error=result if final_status == "failed" else None,
                expected_status="queued",
            )

        truncated = result
        if len(truncated) > _SYNC_RESULT_MAX_CHARS:
            original_len = len(truncated)
            truncated = (
                truncated[:_SYNC_RESULT_MAX_CHARS]
                + f"\n...[结果已截断，原始长度 {original_len}]"
            )

        logger.info(
            "[spawn_sync] completed job_id=%s exit_reason=%s result_len=%d",
            job_id,
            exit_reason,
            len(truncated),
        )
        return f"[子任务「{display_label}」结果]\n退出原因: {exit_reason}\n\n{truncated}"

    async def spawn(
        self,
        *,
        task: str,
        label: str | None = None,
        origin_channel: str,
        origin_chat_id: str,
        profile: str = PROFILE_RESEARCH,
        retry_count: int = 0,
    ) -> str:
        """创建后台 subagent 任务，并立即把控制权还给主 agent。"""
        job_id = uuid.uuid4().hex
        display_label = (label or task[:30] or job_id).strip()
        task_dir = self._job_task_dir(job_id)
        if self._task_store:
            self._task_store.create(
                job_id,
                "subagent",
                payload=json.dumps({"task": task, "profile": profile, "label": display_label, "origin_channel": origin_channel, "origin_chat_id": origin_chat_id}, ensure_ascii=False),
                status="queued",
                retry_count=retry_count,
                label=display_label,
                profile=profile,
                origin_channel=origin_channel,
                origin_chat_id=origin_chat_id,
                retry_policy=_retry_policy_for_profile(profile),
            )

        bg_task = asyncio.create_task(
            self._run_subagent(
                job_id=job_id,
                task=task,
                label=display_label,
                task_dir=task_dir,
                origin_channel=origin_channel,
                origin_chat_id=origin_chat_id,
                profile=profile,
                retry_count=retry_count,
            ),
            name=f"spawn:{job_id}",
        )

        self._register_running_job(
            job_id=job_id,
            bg_task=bg_task,
            label=display_label,
            task=task,
            profile=profile,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            task_dir=task_dir,
            retry_count=retry_count,
        )
        if self._task_store:
            self._task_store.update(job_id, "running", expected_status="queued")

        logger.info(
            "[spawn] started job_id=%s label=%r profile=%s retry_count=%d origin=%s:%s",
            job_id,
            display_label,
            profile,
            retry_count,
            origin_channel,
            origin_chat_id,
        )
        return (
            f"已创建后台任务「{display_label}」（job_id={job_id}）。"
            "不要等待其完成；请直接向用户说明你已开始处理，完成后会继续回复。"
        )

    def _register_running_job(
        self,
        *,
        job_id: str,
        bg_task: asyncio.Task[None],
        label: str,
        task: str,
        profile: str,
        origin_channel: str,
        origin_chat_id: str,
        task_dir: Path,
        retry_count: int,
    ) -> None:
        self._running_tasks[job_id] = bg_task
        self._running_jobs[job_id] = RunningSubagentJob(
            job_id=job_id,
            label=label,
            task=task,
            profile=profile,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            task_dir=str(task_dir),
            retry_count=retry_count,
            started_at=datetime.now(_tz_utc.utc).isoformat(),
        )
        bg_task.add_done_callback(lambda _: self._forget_running_job(job_id))

    def get_running_count(self) -> int:
        return len(self._running_tasks)

    async def retry_job(self, job_id: str, *, allow_side_effects: bool = False) -> asyncio.Task[None] | None:
        """Safely rebuild and execute one interrupted subagent job."""
        if not self._task_store:
            return None
        record = self._task_store.claim_retry(job_id, allow_side_effects=allow_side_effects)
        if not record:
            return None
        try:
            metadata = json.loads(record.payload)
        except (TypeError, json.JSONDecodeError):
            # Older records stored the task itself as plain text.
            metadata = {"task": record.payload}
        if not isinstance(metadata, dict):
            metadata = {"task": str(metadata)}
        task = str(metadata.get("task", "")).strip()
        if not task:
            self._task_store.update(job_id, "failed", error="empty task payload", expected_status="queued")
            return None
        label = record.label or str(metadata.get("label") or task[:30] or job_id)
        profile = record.profile or str(metadata.get("profile") or PROFILE_RESEARCH)
        origin_channel = record.origin_channel or str(metadata.get("origin_channel") or "unknown")
        origin_chat_id = record.origin_chat_id or str(metadata.get("origin_chat_id") or "")
        task_dir = self._job_task_dir(job_id)
        bg_task = asyncio.create_task(self._run_subagent(
            job_id=job_id, task=task, label=label, task_dir=task_dir,
            origin_channel=origin_channel, origin_chat_id=origin_chat_id,
            profile=profile, retry_count=record.retry_count,
        ), name=f"retry:{job_id}")
        self._register_running_job(
            job_id=job_id,
            bg_task=bg_task,
            label=label,
            task=task,
            profile=profile,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            task_dir=task_dir,
            retry_count=record.retry_count,
        )
        self._task_store.update(job_id, "running", expected_status="queued")
        return bg_task

    def list_running_jobs(self) -> list[dict[str, Any]]:
        return [asdict(job) for job in self._running_jobs.values()]

    async def resume_pending_deliveries(self) -> int:
        """Deliver notifications that were persisted but never attempted."""
        if not self._task_store or not self._send_fn:
            return 0
        delivered = 0
        for record in self._task_store.list_pending_deliveries():
            if await self._deliver_outbox_record(record.job_id) == "delivered":
                delivered += 1
        return delivered

    async def cancel(self, job_id: str) -> bool:
        task = self._running_tasks.get(job_id)
        if task is None or task.done():
            return False
        job = self._running_jobs.get(job_id)
        if job is not None:
            self._cancel_announced.add(job_id)
            await self._announce_cancelled_job(job)
        task.cancel()
        await asyncio.sleep(0)
        logger.info("[spawn] cancel requested job_id=%s", job_id)
        return True

    async def stop(self) -> None:
        """Cancel and await background jobs before their shared resources close."""
        active = [
            (job_id, task)
            for job_id, task in self._running_tasks.items()
            if not task.done()
        ]
        tasks = [task for _, task in active]
        if not tasks:
            return
        self._stopping = True
        try:
            if self._task_store:
                for job_id, _ in active:
                    self._task_store.update(
                        job_id,
                        "retry_wait",
                        error="service stopped during execution",
                        expected_status="running",
                    )
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._stopping = False

    def _forget_running_job(self, job_id: str) -> None:
        self._running_tasks.pop(job_id, None)
        self._running_jobs.pop(job_id, None)
        self._cancel_announced.discard(job_id)

    async def _run_subagent(
        self,
        *,
        job_id: str,
        task: str,
        label: str,
        task_dir: Path,
        origin_channel: str,
        origin_chat_id: str,
        profile: str,
        retry_count: int,
    ) -> None:
        """运行后台 subagent，并把统一结果协议回灌给主 agent。"""
        subagent = self._build_subagent(
            task_dir=task_dir,
            profile=profile,
            max_iterations=_SPAWN_MAX_ITERATIONS,
        )
        status = "completed"
        exit_reason = "completed"
        result_text = ""

        try:
            result_text = await asyncio.wait_for(subagent.run(task), timeout=_SPAWN_TIMEOUT_SECONDS)
            exit_reason = getattr(subagent, "last_exit_reason", None) or "completed"
            if exit_reason in ("error", "forced_summary", "forced_summary_fallback"):
                status = "partial"
        except asyncio.CancelledError:
            if not self._stopping and job_id not in self._cancel_announced:
                await self._announce_result(
                    job_id=job_id,
                    label=label,
                    task=task,
                    origin_channel=origin_channel,
                    origin_chat_id=origin_chat_id,
                    status="cancelled",
                    exit_reason="cancelled",
                    result="后台任务已按请求取消。",
                    profile=profile,
                    retry_count=retry_count,
                )
            raise
        except asyncio.TimeoutError:
            logger.error("[spawn] subagent timeout job_id=%s", job_id)
            status = "failed"
            exit_reason = "timeout"
            result_text = "后台任务执行超时"
        except Exception as e:
            logger.exception("[spawn] subagent failed job_id=%s err=%s", job_id, e)
            status = "failed"
            exit_reason = "error"
            result_text = f"执行出错：{e}"

        await self._announce_result(
            job_id=job_id,
            label=label,
            task=task,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            status=status,
            exit_reason=exit_reason,
            result=result_text,
            profile=profile,
            retry_count=retry_count,
        )

    async def _announce_cancelled_job(self, job: RunningSubagentJob) -> None:
        await self._announce_result(
            job_id=job.job_id,
            label=job.label,
            task=job.task,
            origin_channel=job.origin_channel,
            origin_chat_id=job.origin_chat_id,
            status="cancelled",
            exit_reason="cancelled",
            result="后台任务已按请求取消。",
            profile=job.profile,
            retry_count=job.retry_count,
        )

    def _build_subagent(
        self,
        *,
        task_dir: Path,
        profile: str = PROFILE_RESEARCH,
        max_iterations: int = _SPAWN_MAX_ITERATIONS,
    ) -> SubAgent:
        tools = self._get_tools_for_profile(profile, task_dir=task_dir)
        return SubAgent(
            provider=self._runtime.provider,
            model=self._runtime.model,
            tools=tools,
            system_prompt=self._build_subagent_prompt(task_dir, profile),
            max_iterations=max_iterations,
            max_tokens=self._runtime.max_tokens,
        )

    def _get_tools_for_profile(self, profile: str, task_dir: Path) -> list[Any]:
        """从 plugin_manager 获取指定 profile 的工具列表"""
        if self._plugin_manager is None:
            return []

        all_tools = self._plugin_manager._tools
        tools = []

        research_tools = {"web_search", "web_fetch", "read_file", "write_file", "list_dir"}
        scripting_tools = {"shell", "write_file", "read_file", "list_dir", "edit_file"}

        allowed_tools: set[str] = set()
        if profile == PROFILE_RESEARCH:
            allowed_tools = research_tools
        elif profile == PROFILE_SCRIPTING:
            allowed_tools = scripting_tools
        else:
            allowed_tools = research_tools | scripting_tools

        for tool_name, tool_info in all_tools.items():
            if tool_name in allowed_tools:
                tools.append(SubAgentToolWrapper(tool_info, self._plugin_manager, task_dir))

        return tools

    def _build_subagent_prompt(self, task_dir: Path, profile: str = PROFILE_RESEARCH) -> str:
        base_prompt = (
            "你是番茄猫的后台任务助手，正在独立执行一个后台任务。\n"
            "你的工作目录是：" + str(task_dir) + "\n"
            "请专注于完成任务，不要闲聊。\n"
            "任务完成后，直接输出最终结果，不要调用额外工具。\n"
        )
        if profile == PROFILE_RESEARCH:
            base_prompt += (
                "\n当前模式：调研模式\n"
                "你可以使用搜索、网页抓取、读写文件等工具。\n"
                "注意：不要执行 shell 命令。\n"
                "重要：调研完成后，必须把结果整理成文档写入工作目录，"
                "使用 write_file 工具保存。不要只输出到对话中。\n"
            )
        elif profile == PROFILE_SCRIPTING:
            base_prompt += (
                "\n当前模式：执行模式\n"
                "你可以运行命令和写文件，但不要访问外部网络。\n"
                "所有文件操作请限制在工作目录内。\n"
            )
        else:
            base_prompt += (
                "\n当前模式：通用模式\n"
                "你可以使用所有可用工具。\n"
                "请谨慎执行有风险的操作。\n"
            )
        return base_prompt

    async def _announce_result(
        self,
        *,
        job_id: str,
        label: str,
        task: str,
        origin_channel: str,
        origin_chat_id: str,
        status: str,
        exit_reason: str,
        result: str,
        profile: str,
        retry_count: int,
    ) -> None:
        """Persist a result, deliver its notification, and publish an observation event."""
        final_status = status if status in {"completed", "partial", "failed", "cancelled"} else "failed"
        payload_result = result
        if len(payload_result) > _RESULT_MAX_CHARS:
            original_len = len(payload_result)
            payload_result = (
                payload_result[:_RESULT_MAX_CHARS]
                + f"\n...[结果已截断，原始长度 {original_len}]"
            )

        delivery_message = ""
        if self._send_fn and origin_channel != "unknown" and origin_chat_id:
            delivery_message = (
                f"[后台任务完成]\n任务ID：{job_id}\n任务：{label}\n"
                f"状态：{status}\n\n{payload_result}"
            )

        persisted = False
        if self._task_store:
            persisted = self._task_store.finish_and_queue_delivery(
                job_id,
                final_status,
                result=result,
                error=result if final_status == "failed" else "",
                delivery_message=delivery_message,
            )

        event = SpawnCompletionEvent(
            job_id=job_id,
            label=label,
            task=task,
            status=status,
            exit_reason=exit_reason,
            result=payload_result,
            retry_count=retry_count,
            profile=profile,
            delivery_status="not_required",
        )

        logger.info(
            "[spawn] completed job_id=%s status=%s exit_reason=%s profile=%s retry_count=%d route=%s:%s",
            job_id,
            status,
            exit_reason,
            profile,
            retry_count,
            origin_channel,
            origin_chat_id,
        )

        if delivery_message and persisted:
            event.delivery_status = await self._deliver_outbox_record(job_id)
        elif delivery_message and not self._task_store:
            try:
                await self._send_fn(origin_channel, origin_chat_id, delivery_message)
                event.delivery_status = "delivered"
            except Exception as e:
                logger.warning("[spawn] 主动通知发送失败: %s", e)
                event.delivery_status = "failed"
        elif self._task_store:
            current = self._task_store.get(job_id)
            if current:
                event.delivery_status = current.delivery_status
        if self._event_bus:
            self._event_bus.enqueue(event)

    async def _deliver_outbox_record(self, job_id: str) -> str:
        if not self._task_store or not self._send_fn:
            return "not_required"
        record = self._task_store.claim_delivery(job_id)
        if record is None:
            current = self._task_store.get(job_id)
            return current.delivery_status if current else "not_required"
        try:
            await self._send_fn(
                record.origin_channel,
                record.origin_chat_id,
                record.delivery_message,
            )
            self._task_store.update_delivery(
                job_id,
                "delivered",
                expected_status="sending",
                increment_attempts=True,
            )
            return "delivered"
        except Exception as exc:
            logger.warning("[spawn] 主动通知发送失败 job_id=%s: %s", job_id, exc)
            self._task_store.update_delivery(
                job_id,
                "failed",
                error=str(exc),
                expected_status="sending",
                increment_attempts=True,
            )
            return "failed"


@dataclass
class SpawnCompletionEvent:
    job_id: str
    label: str
    task: str
    status: str
    exit_reason: str
    result: str
    retry_count: int
    profile: str
    delivery_status: str = "not_required"
