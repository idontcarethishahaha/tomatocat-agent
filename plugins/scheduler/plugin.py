"""定时任务插件 - 设置提醒、闹钟、周期任务

ScheduleTool 实现，支持：
- 三种触发模式：at（指定时间）、after（延迟）、every（周期）
- 两种执行模式：instant（直接发消息）、soft（让AI生成）
- request_time 延迟补偿（从用户发消息时刻计算）
- 时区支持（默认 Asia/Shanghai）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as _tz_utc
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

from tomatocat.plugins import Plugin, tool

# 固定 UTC+8 偏移量，作为 ZoneInfo 不可用时的 fallback
_TZ_CN = _tz_utc(timedelta(hours=8))


def _safe_tz(tz_name: str):
    """安全获取时区对象"""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return _TZ_CN


class SchedulerPlugin(Plugin):
    name = "scheduler"
    desc = "番茄猫定时提醒助手"

    def __init__(self) -> None:
        super().__init__()
        self._scheduler = None
        self._default_channel = "cli"
        self._default_chat_id = ""
        self._default_tz = "Asia/Shanghai"

    async def initialize(self) -> None:
        pass

    def set_scheduler(self, scheduler: Any) -> None:
        self._scheduler = scheduler

    def set_default_target(self, channel: str, chat_id: str) -> None:
        self._default_channel = channel
        self._default_chat_id = chat_id

    def set_timezone(self, tz: str) -> None:
        self._default_tz = tz

    def _get_channel_chat_id(self, channel: str = "", chat_id: str = "") -> tuple[str, str]:
        ch = channel or self._default_channel
        cid = chat_id or self._default_chat_id
        return ch, cid

    def _format_time(self, dt: datetime, tz_name: str | None = None) -> str:
        """格式化时间为本地时区显示"""
        tzinfo = _safe_tz(tz_name or self._default_tz)
        local = dt.astimezone(tzinfo)
        return local.strftime("%Y-%m-%d %H:%M:%S")

    @tool(
        name="schedule",
        description=(
            "注册定时任务。\n"
            "触发模式：at=指定时间（如'14:30'），after=延迟（如'5m'），every=循环（如'1h'或cron'0 9 * * *'）\n"
            "执行模式：instant=直接发固定消息，soft=AI生成内容\n"
            "注意：tier 只能是 instant 或 soft；channel/chat_id 从 system prompt 获取；request_time 从消息头获取"
        ),
    )
    async def schedule(
        self,
        event: object,
        tier: str,
        trigger: str,
        when: str,
        message: str = "",
        prompt: str = "",
        channel: str = "",
        chat_id: str = "",
        tz_name: str = "",
        name: str = "",
        request_time: str = "",
    ) -> str:
        """
        设置定时任务

        Args:
            tier: 执行模式 - 'instant'(直接发消息) / 'soft'(让AI生成内容)
            trigger: 触发类型 - 'at'(指定时间) / 'after'(延迟多久) / 'every'(周期性)
            when: 时间描述
            message: tier=instant 时的消息内容（必填）
            prompt: tier=soft 时触发 AI 的提示词（必填）
            channel: 目标渠道，从 system prompt 的「当前会话」获取
            chat_id: 目标会话 ID，从 system prompt 的「当前会话」获取
            tz_name: 时区（默认 Asia/Shanghai）
            name: 任务名称（可选）
            request_time: 来自消息头的 request_time，用于延迟补偿
        """
        if not self._scheduler:
            return "喵... 定时服务还没准备好 (・_・;)"

        # 参数验证
        tier = tier.lower().strip()
        if tier not in ("instant", "soft"):
            return f"喵？tier 须为 instant 或 soft，收到 {tier!r} (=｀ω´=)"

        if trigger not in ("at", "after", "every"):
            return f"喵？trigger 须为 at/after/every，收到 {trigger!r} (=｀ω´=)"

        if tier == "instant" and not message:
            return "喵？tier=instant 时 message 为必填项 (・_・;)"

        if tier == "soft" and not prompt:
            return "喵？tier=soft 时 prompt 为必填项 (・_・;)"

        ch, cid = self._get_channel_chat_id(channel, chat_id)
        if not ch or not cid:
            return "喵？需要指定目标渠道和会话 ID (・_・;)"

        job_tz = tz_name or self._default_tz

        try:
            job = self._scheduler.add_job(
                trigger=trigger,
                when=when,
                channel=ch,
                chat_id=cid,
                mode=tier,
                message=message if tier == "instant" else None,
                prompt=prompt if tier == "soft" else None,
                name=name or None,
                job_tz=job_tz,
                request_time=request_time if trigger == "after" else None,
            )

            time_str = self._format_time(job.fire_at, job_tz)
            trigger_text = {
                "at": f"在 {time_str}",
                "after": f"{when}后（约 {time_str}）",
                "every": f"周期性（下次 {time_str}）",
            }.get(trigger, when)

            label = f"「{name}」" if name else job.id[:8]

            if tier == "instant":
                content_preview = message[:40]
                return f"⏰ 已设置定时任务 {label}\n{trigger_text}\n内容: {content_preview}"
            else:
                prompt_preview = prompt[:40]
                return f"⏰ 已设置 AI 定时任务 {label}\n{trigger_text}\n提示词: {prompt_preview}"

        except ValueError as e:
            return f"喵？时间格式不对哦：{e} (=｀ω´=)"
        except Exception as e:
            return f"设置定时任务失败了喵... {e} (・_・;)"

    @tool(name="list_schedules", description="列出所有待执行的定时任务")
    async def list_schedules(self, event: object) -> str:
        """列出所有定时任务"""
        if not self._scheduler:
            return "喵... 定时服务还没准备好 (・_・;)"

        jobs = self._scheduler.list_jobs()
        if not jobs:
            return "目前没有待执行的定时任务哦 (｡•ᴗ-｡)♡"

        lines = [f"⏰ 定时任务列表（共 {len(jobs)} 个）："]
        for i, job in enumerate(jobs, 1):
            try:
                time_str = self._format_time(job.fire_at, job.job_tz)
            except Exception:
                time_str = job.fire_at.isoformat()

            trigger_text = {
                "at": "一次性",
                "after": "倒计时",
                "every": "周期性",
            }.get(job.trigger, job.trigger)

            name = job.name or job.id[:8]

            if job.mode == "instant":
                content = (job.message or "")[:30]
            else:
                content = f"[AI] {(job.prompt or '')[:30]}"

            lines.append(
                f"{i}. {name} [{job.mode}/{trigger_text}]\n"
                f"   下次: {time_str}\n"
                f"   内容: {content}...\n"
                f"   已运行: {job.run_count}次"
            )

        return "\n".join(lines)

    @tool(name="cancel_schedule", description="取消定时任务。可按任务 ID 或名称取消")
    async def cancel_schedule(
        self,
        event: object,
        id: str = "",
        name: str = "",
    ) -> str:
        """取消定时任务"""
        if not self._scheduler:
            return "喵... 定时服务还没准备好 (・_・;)"

        if not id and not name:
            return "喵？id 或 name 至少提供一个 (=｀ω´=)"

        if id:
            all_ids = list(self._scheduler._jobs.keys())
            matches = [jid for jid in all_ids if jid == id or jid.startswith(id)]
            if not matches:
                return f"没有找到 ID 为 {id!r} 的任务哦 (・_・;)"
            for jid in matches:
                self._scheduler.cancel_job(jid)
            return f"已取消 {len(matches)} 个任务 (｡•ᴗ-｡)♡"

        if name:
            count = self._scheduler.cancel_by_name(name)
            if count > 0:
                return f"已取消 {count} 个名为 '{name}' 的任务 (｡•ᴗ-｡)♡"
            return f"没有找到名为 '{name}' 的任务哦 (・_・;)"

        return "喵？要取消哪个任务呀？ (=｀ω´=)"
