"""定时任务模块

支持：
- 一次性提醒（at: "14:30" / "2025-06-01T09:00"）
- 延迟提醒（after: "30s" / "5m" / "2h"）
- 周期性提醒（every: cron 表达式 / 时间间隔）
- 两种模式：
  - instant: 到点直接发消息
  - soft: 到点让 AI 生成消息再发送
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

logger = logging.getLogger(__name__)

_DURATION_RE = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")


def parse_duration(s: str) -> timedelta:
    """解析时长字符串，如 '30s', '5m', '2h', '1h30m', '1d2h'。"""
    s = s.strip()
    m = _DURATION_RE.match(s)
    if not m or not any(m.groups()):
        raise ValueError(f"无效的时间间隔: {s!r}，示例: '30s', '5m', '2h', '1h30m'")
    days, hours, minutes, seconds = (int(x or 0) for x in m.groups())
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def parse_when_at(s: str, tz: str = "Asia/Shanghai") -> datetime:
    """解析 'at' 时间：HH:MM（自动判断今天/明天）或 ISO datetime。"""
    if ZoneInfo:
        tzinfo = ZoneInfo(tz)
    else:
        tzinfo = timezone(timedelta(hours=8))
    now = datetime.now(tzinfo)
    s = s.strip()

    if re.match(r"^\d{1,2}:\d{2}$", s):
        t = datetime.strptime(s, "%H:%M").time()
        dt = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return dt

    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tzinfo)
        return dt
    except ValueError:
        pass

    raise ValueError(f"无法解析时间: {s!r}，示例: '14:30', '2025-06-01T09:00'")


def is_cron_expr(s: str) -> bool:
    """判断字符串是否是 cron 表达式（5 字段）。"""
    parts = s.strip().split()
    return len(parts) == 5


def _parse_cron_field(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, step_str = part.split("/", 1)
            step = int(step_str)
            if step <= 0:
                raise ValueError(f"无效 cron step: {field!r}")
        if part == "*":
            start, end = minimum, maximum
        elif "-" in part:
            start_str, end_str = part.split("-", 1)
            start, end = int(start_str), int(end_str)
        else:
            start = end = int(part)
        if start < minimum or end > maximum or start > end:
            raise ValueError(f"无效 cron 字段: {field!r}")
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError(f"无效 cron 字段: {field!r}")
    return values


def next_cron_fire(cron_expr: str, tz: str, after: datetime) -> datetime:
    """计算 cron 下次触发时间（纯 Python 实现，无外部依赖）。"""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"无效的 cron 表达式: {cron_expr!r}（需要 5 字段）")

    minute_s, hour_s, dom_s, month_s, dow_s = parts

    minute_values = _parse_cron_field(minute_s, 0, 59)
    hour_values = _parse_cron_field(hour_s, 0, 23)
    dom_values = _parse_cron_field(dom_s, 1, 31)
    month_values = _parse_cron_field(month_s, 1, 12)
    dow_values = _parse_cron_field(dow_s.replace("7", "0"), 0, 6)

    if ZoneInfo:
        tzinfo = ZoneInfo(tz)
    else:
        tzinfo = timezone(timedelta(hours=8))

    current = after.astimezone(tzinfo).replace(second=0, microsecond=0) + timedelta(minutes=1)
    step = timedelta(minutes=1)

    for _ in range(366 * 24 * 60):
        cron_dow = (current.weekday() + 1) % 7
        if (
            current.minute in minute_values
            and current.hour in hour_values
            and current.day in dom_values
            and current.month in month_values
            and cron_dow in dow_values
        ):
            return current.astimezone(timezone.utc)
        current += step
    raise ValueError(f"无法在合理范围内解析 cron 表达式: {cron_expr!r}")


def compute_fire_at(
    trigger: str,
    when: str,
    tz: str = "Asia/Shanghai",
    request_time: datetime | None = None,
) -> datetime:
    """计算首次触发时间。"""
    if trigger == "at":
        return parse_when_at(when, tz)

    if trigger == "after":
        duration = parse_duration(when)
        base = request_time or datetime.now(timezone.utc)
        return base + duration

    if trigger == "every":
        if is_cron_expr(when):
            return next_cron_fire(when, tz, datetime.now(timezone.utc))
        interval = parse_duration(when)
        return datetime.now(timezone.utc) + interval

    raise ValueError(f"未知触发类型: {trigger!r}，须为 at/after/every")


@dataclass
class ScheduledJob:
    trigger: str  # "at" | "after" | "every"
    mode: str  # "instant" | "soft"
    fire_at: datetime  # 下次触发时间（UTC-aware）
    channel: str
    chat_id: str

    interval_seconds: int | None = None  # every + interval 模式
    cron_expr: str | None = None  # every + cron 模式

    message: str | None = None  # instant mode
    prompt: str | None = None  # soft mode

    name: str | None = None
    timezone: str = "Asia/Shanghai"

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    run_count: int = 0
    enabled: bool = True
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


class SchedulerService:
    """asyncio 定时任务服务。

    - 每秒 tick 一次，检查 fire_at <= now 的 job
    - instant: 直接调用 send_fn 发消息
    - soft: 调用 agent_fn 让 AI 生成消息，再发送
    - 持久化到 JSON，重启后自动恢复
    """

    GRACE_SECONDS = 300  # 5分钟内的 misfire 仍执行

    def __init__(
        self,
        store_path: Path,
        send_fn: Callable[[str, str, str], Any],
        agent_fn: Callable[[str, str, str], Any] | None = None,
        timezone: str = "Asia/Shanghai",
    ) -> None:
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.send_fn = send_fn
        self.agent_fn = agent_fn
        self.timezone = timezone
        self._jobs: dict[str, ScheduledJob] = {}
        self._in_flight: set[str] = set()
        self._running = False
        self._task: asyncio.Task | None = None

    # ── Public API ───────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._load_jobs()
        self._recover_misfires()
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[scheduler] 定时任务服务已启动，当前 %d 个任务", len(self._jobs))

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[scheduler] 定时任务服务已停止")

    def add_job(
        self,
        trigger: str,
        when: str,
        channel: str,
        chat_id: str,
        mode: str = "instant",
        message: str | None = None,
        prompt: str | None = None,
        name: str | None = None,
    ) -> ScheduledJob:
        """添加定时任务，返回 job 对象。"""
        fire_at = compute_fire_at(trigger, when, self.timezone)

        interval_seconds = None
        cron_expr = None
        if trigger == "every":
            if is_cron_expr(when):
                cron_expr = when
            else:
                interval_seconds = int(parse_duration(when).total_seconds())

        job = ScheduledJob(
            trigger=trigger,
            mode=mode,
            fire_at=fire_at,
            channel=channel,
            chat_id=chat_id,
            interval_seconds=interval_seconds,
            cron_expr=cron_expr,
            message=message,
            prompt=prompt,
            name=name,
            timezone=self.timezone,
        )
        self._jobs[job.id] = job
        self._save_jobs()
        logger.info(
            "[scheduler] 任务已添加: %s (%s) trigger=%s mode=%s fire_at=%s",
            job.id[:8],
            name or "unnamed",
            trigger,
            mode,
            fire_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
        )
        return job

    def cancel_job(self, job_id: str) -> bool:
        if job_id not in self._jobs:
            return False
        job = self._jobs.pop(job_id)
        self._save_jobs()
        logger.info("[scheduler] 任务已取消: %s (%s)", job_id[:8], job.name or "unnamed")
        return True

    def cancel_by_name(self, name: str) -> int:
        to_cancel = [jid for jid, j in self._jobs.items() if j.name == name]
        for jid in to_cancel:
            del self._jobs[jid]
        if to_cancel:
            self._save_jobs()
            logger.info("[scheduler] 已取消 %d 个名为 %r 的任务", len(to_cancel), name)
        return len(to_cancel)

    def list_jobs(self) -> list[ScheduledJob]:
        return sorted(self._jobs.values(), key=lambda j: j.fire_at)

    # ── Internal ────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(1)
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[scheduler] tick 异常: %s", e, exc_info=True)

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        for job in list(self._jobs.values()):
            if not job.enabled or job.id in self._in_flight:
                continue
            if job.fire_at <= now:
                label = job.name or job.id[:8]
                logger.info(
                    "[scheduler] 触发任务 %r mode=%s channel=%s:%s",
                    label,
                    job.mode,
                    job.channel,
                    job.chat_id,
                )
                self._in_flight.add(job.id)
                asyncio.create_task(self._execute_and_reschedule(job))

    async def _execute_and_reschedule(self, job: ScheduledJob) -> None:
        try:
            await self._execute(job)
            job.run_count += 1
        except Exception as e:
            logger.error("[scheduler] 任务 %s 执行失败: %s", job.id[:8], e, exc_info=True)
        finally:
            self._in_flight.discard(job.id)
            now = datetime.now(timezone.utc)
            if job.trigger == "every":
                reschedule_after = max(now, job.fire_at) + timedelta(microseconds=1)
                job.fire_at = self._advance_every(job, reschedule_after)
                self._jobs[job.id] = job
            else:
                self._jobs.pop(job.id, None)
            self._save_jobs()

    async def _execute(self, job: ScheduledJob) -> None:
        if job.mode == "instant":
            message = job.message or "(提醒)"
            await self._call_send(job.channel, job.chat_id, message)
        else:  # soft
            if self.agent_fn is None:
                logger.warning("[scheduler] soft 模式但无 agent_fn，跳过任务 %s", job.id[:8])
                return
            prompt = job.prompt or "请给用户发一条提醒消息"
            t0 = time.monotonic()
            content = await self._call_agent(
                content=prompt,
                channel=job.channel,
                chat_id=job.chat_id,
                session_key=f"scheduler:{job.id}",
            )
            elapsed = time.monotonic() - t0
            logger.info("[scheduler] soft AI 完成，耗时 %.1fs", elapsed)
            if content:
                await self._call_send(job.channel, job.chat_id, content)

    async def _call_send(self, channel: str, chat_id: str, message: str) -> None:
        result = self.send_fn(channel, chat_id, message)
        if hasattr(result, "__await__"):
            await result

    async def _call_agent(self, content: str, channel: str, chat_id: str, session_key: str) -> str:
        if self.agent_fn is None:
            return ""
        result = self.agent_fn(content, channel, chat_id, session_key)
        if hasattr(result, "__await__"):
            result = await result
        return str(result) if result else ""

    def _advance_every(self, job: ScheduledJob, after: datetime) -> datetime:
        if job.cron_expr:
            return next_cron_fire(job.cron_expr, job.timezone, after)
        interval = timedelta(seconds=job.interval_seconds or 3600)
        next_fire = job.fire_at + interval
        while next_fire <= after:
            next_fire += interval
        return next_fire

    def _recover_misfires(self) -> None:
        """启动时处理 misfire 的任务。"""
        now = datetime.now(timezone.utc)
        expired_count = 0
        for job in list(self._jobs.values()):
            if not job.enabled:
                continue
            if job.fire_at.tzinfo is None:
                job.fire_at = job.fire_at.replace(tzinfo=timezone.utc)

            if job.fire_at <= now:
                age = (now - job.fire_at).total_seconds()
                if job.trigger == "every":
                    job.fire_at = self._advance_every(job, now)
                    logger.info(
                        "[scheduler] 恢复周期任务 %s (%s)，下次触发: %s",
                        job.id[:8],
                        job.name or "unnamed",
                        job.fire_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
                    )
                elif age <= self.GRACE_SECONDS:
                    pass  # 保留，下次 tick 会执行
                else:
                    self._jobs.pop(job.id, None)
                    expired_count += 1
                    logger.info(
                        "[scheduler] 过期任务已丢弃: %s (%s) 过期 %.0fs",
                        job.id[:8],
                        job.name or "unnamed",
                        age,
                    )
        if expired_count:
            self._save_jobs()

    def _load_jobs(self) -> None:
        if not self.store_path.exists():
            return
        try:
            raw = json.loads(self.store_path.read_text(encoding="utf-8"))
            for d in raw:
                try:
                    d = dict(d)
                    d["fire_at"] = datetime.fromisoformat(d["fire_at"])
                    d["created_at"] = datetime.fromisoformat(d["created_at"])
                    if d["fire_at"].tzinfo is None:
                        d["fire_at"] = d["fire_at"].replace(tzinfo=timezone.utc)
                    if d["created_at"].tzinfo is None:
                        d["created_at"] = d["created_at"].replace(tzinfo=timezone.utc)
                    job = ScheduledJob(**d)
                    self._jobs[job.id] = job
                except Exception as e:
                    logger.warning("[scheduler] 任务反序列化失败: %s", e)
            logger.info("[scheduler] 已加载 %d 个任务", len(self._jobs))
        except Exception as e:
            logger.warning("[scheduler] 任务文件加载失败: %s", e)

    def _save_jobs(self) -> None:
        data = []
        for job in self._jobs.values():
            d = asdict(job)
            d["fire_at"] = job.fire_at.isoformat()
            d["created_at"] = job.created_at.isoformat()
            data.append(d)
        try:
            self.store_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("[scheduler] 任务保存失败: %s", e)
