"""状态命令插件 - /ping /status /version /help

在 agent.handle_message 开头拦截以 / 开头的命令，
直接返回状态信息，不经过 LLM。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone as _tz_utc

from tomatocat.plugins import Plugin

log = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

_TZ_CN = _tz_utc(timedelta(hours=8))
_VERSION = "0.1.0"


class StatusCommandsPlugin(Plugin):
    name = "status_commands"
    desc = "状态命令 (/ping /status /version /help)"
    version = _VERSION

    def __init__(self) -> None:
        super().__init__()
        self._start_time = time.monotonic()
        self._start_dt = datetime.now(_TZ_CN)

    def handle_command(self, text: str, session_key: str, channel: str) -> str | None:
        """处理 / 开头的命令。返回 None 表示不是状态命令，交给正常流程。"""
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None

        parts = stripped.split(maxsplit=1)
        cmd = parts[0].lower()
        if "@" in cmd:
            cmd = cmd.split("@", 1)[0]

        handlers = {
            "/ping": self._cmd_ping,
            "/status": self._cmd_status,
            "/version": self._cmd_version,
            "/help": self._cmd_help,
            "/proactive_test": self._cmd_proactive_test,
        }

        handler = handlers.get(cmd)
        if handler is None:
            return None

        log.info("[status_commands] 命中命令: %s (from %s:%s)", cmd, channel, session_key)
        try:
            return handler(session_key=session_key, channel=channel)
        except Exception as e:
            log.error("[status_commands] 命令处理失败 %s: %s", cmd, e)
            return f"喵... 命令执行出错了：{e} (・_・;)"

    def _cmd_ping(self, **kw) -> str:
        elapsed = time.monotonic() - self._start_time
        return f"pong! 🏓\n运行时长: {_format_uptime(elapsed)}"

    def _cmd_status(self, session_key: str = "", channel: str = "", **kw) -> str:
        lines = ["📊 番茄猫状态"]

        elapsed = time.monotonic() - self._start_time
        lines.append(f"⏱ 运行时长: {_format_uptime(elapsed)}")
        lines.append(f"🕐 启动时间: {self._start_dt.strftime('%Y-%m-%d %H:%M:%S')}")

        # 插件 & 工具
        try:
            mgr = self.context._manager
            lines.append(f"🔧 插件数: {len(mgr._plugins)}")
            lines.append(f"🛠 工具数: {len(mgr._tools)}")
        except Exception:
            pass

        # 会话
        try:
            sm = self.context.session_manager
            session_count = len(getattr(sm, "_sessions", {}))
            lines.append(f"💬 活跃会话: {session_count}")
        except Exception:
            pass

        # 记忆
        try:
            mem = self.context.memory
            lines.append(f"🧠 记忆系统: {'已启用' if mem is not None else '未启用'}")
        except Exception:
            pass

        lines.append(f"📍 当前渠道: {channel}")
        lines.append(f"📍 当前会话: {session_key}")

        return "\n".join(lines)

    def _cmd_version(self, **kw) -> str:
        return f"🍅🐱 番茄猫 TomatoCat v{_VERSION}\n基于 akashic-agent 架构学习实现"

    def _cmd_proactive_test(self, **kw) -> str:
        proactive = self.context.proactive
        if not proactive:
            return "😿 主动推送未启用！\n请检查 config.toml 中 proactive.enabled = true"

        import asyncio
        loop = asyncio.get_running_loop()

        async def _run_test():
            await proactive._tick()

        loop.create_task(_run_test())
        return "🚀 主动推送测试已触发！\n请查看日志和目标渠道是否收到推送消息~"

    def _cmd_help(self, **kw) -> str:
        return (
            "🍅🐱 番茄猫命令列表：\n"
            "/ping - 检查存活，显示运行时长\n"
            "/status - 查看系统状态（插件/会话/记忆）\n"
            "/version - 查看版本号\n"
            "/proactive_test - 测试主动推送功能\n"
            "/help - 显示本帮助\n"
            "\n"
            "其他消息会正常和番茄猫聊天喵~ (｡•ᴗ-｡)♡"
        )


def _format_uptime(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}秒"
    if seconds < 3600:
        return f"{int(seconds // 60)}分{int(seconds % 60)}秒"
    if seconds < 86400:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}小时{m}分"
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    return f"{d}天{h}小时"
