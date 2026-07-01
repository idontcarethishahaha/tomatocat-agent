"""定时任务插件 - 设置提醒、闹钟、周期任务"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tomatocat.plugins import Plugin, tool

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


class SchedulerPlugin(Plugin):
    name = "scheduler"
    desc = "番茄猫定时提醒助手"

    def __init__(self) -> None:
        super().__init__()
        self._scheduler = None
        self._default_channel = "cli"
        self._default_chat_id = ""
        self._timezone = "Asia/Shanghai"

    async def initialize(self) -> None:
        pass

    def set_scheduler(self, scheduler: Any) -> None:
        self._scheduler = scheduler

    def set_default_target(self, channel: str, chat_id: str) -> None:
        self._default_channel = channel
        self._default_chat_id = chat_id

    def _get_channel_chat_id(self, channel: str = "", chat_id: str = "") -> tuple[str, str]:
        ch = channel or self._default_channel
        cid = chat_id or self._default_chat_id
        return ch, cid

    def _format_time(self, dt: datetime) -> str:
        if ZoneInfo:
            tzinfo = ZoneInfo(self._timezone)
        else:
            from datetime import timedelta
            tzinfo = timezone(timedelta(hours=8))
        local = dt.astimezone(tzinfo)
        return local.strftime("%Y-%m-%d %H:%M:%S")

    @tool(name="schedule_reminder", description="设置一个定时提醒，支持一次性或周期性")
    async def schedule_reminder(
        self,
        event: object,
        message: str,
        trigger: str = "after",
        when: str = "10m",
        name: str = "",
        mode: str = "instant",
    ) -> str:
        """
        设置定时提醒

        Args:
            message: 提醒内容
            trigger: 触发类型 - 'at'(指定时间) / 'after'(延迟多久) / 'every'(周期性)
            when: 时间描述 - trigger=at时如'14:30'或'2025-06-01T09:00'；trigger=after时如'30s'/'5m'/'2h'；trigger=every时如'1h'或cron表达式'0 9 * * *'
            name: 提醒名称（可选，方便取消）
            mode: 模式 - 'instant'(直接发消息) / 'soft'(让AI生成提醒内容)
        """
        if not self._scheduler:
            return "喵... 定时服务还没准备好 (・_・;)"

        channel, chat_id = self._get_channel_chat_id()

        try:
            job = self._scheduler.add_job(
                trigger=trigger,
                when=when,
                channel=channel,
                chat_id=chat_id,
                mode=mode,
                message=message if mode == "instant" else None,
                prompt=message if mode == "soft" else None,
                name=name or None,
            )
            time_str = self._format_time(job.fire_at)
            trigger_text = {
                "at": f"在 {time_str}",
                "after": f"{when}后（约 {time_str}）",
                "every": f"每 {when}（下次 {time_str}）",
            }.get(trigger, when)
            msg = f"⏰ 已设置提醒：{name or '未命名'}\n{trigger_text}\n内容：{message[:50]}"
            if len(message) > 50:
                msg += "..."
            return msg
        except ValueError as e:
            return f"喵？时间格式不对哦：{e} (=｀ω´=)"
        except Exception as e:
            return f"设置提醒失败了喵... {e} (・_・;)"

    @tool(name="list_reminders", description="查看当前所有定时提醒")
    async def list_reminders(self, event: object) -> str:
        """查看所有定时提醒"""
        if not self._scheduler:
            return "喵... 定时服务还没准备好 (・_・;)"

        jobs = self._scheduler.list_jobs()
        if not jobs:
            return "目前没有设置提醒哦 (｡•ᴗ-｡)♡"

        lines = ["⏰ 当前提醒列表："]
        for i, job in enumerate(jobs, 1):
            time_str = self._format_time(job.fire_at)
            trigger_text = {
                "at": "一次性",
                "after": "倒计时",
                "every": "周期性",
            }.get(job.trigger, job.trigger)
            name = job.name or f"提醒{job.id[:6]}"
            content = (job.message or job.prompt or "")[:30]
            lines.append(f"{i}. {name} [{trigger_text}] - {time_str}\n   {content}...")

        return "\n".join(lines)

    @tool(name="cancel_reminder", description="取消定时提醒，按名称或ID取消")
    async def cancel_reminder(
        self,
        event: object,
        name: str = "",
        job_id: str = "",
    ) -> str:
        """
        取消定时提醒

        Args:
            name: 按名称取消（取消所有同名提醒）
            job_id: 按ID取消（精确取消一个）
        """
        if not self._scheduler:
            return "喵... 定时服务还没准备好 (・_・;)"

        if job_id:
            ok = self._scheduler.cancel_job(job_id)
            if ok:
                return f"已取消提醒 {job_id[:8]} (｡•ᴗ-｡)♡"
            return f"没有找到ID为 {job_id[:8]} 的提醒哦 (・_・;)"

        if name:
            count = self._scheduler.cancel_by_name(name)
            if count > 0:
                return f"已取消 {count} 个名为 '{name}' 的提醒 (｡•ᴗ-｡)♡"
            return f"没有找到名为 '{name}' 的提醒哦 (・_・;)"

        return "喵？要取消哪个提醒呀？请告诉我名称或ID (=｀ω´=)"
