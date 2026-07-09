"""
ShellSafety 插件 - Shell 安全防护

阻止 shell 工具执行容易卡住的交互式命令和危险操作：
- 交互式编辑器（vi/vim/nvim/nano）
- 需要密码的 sudo 命令
- 包管理器的写操作（需加 --noconfirm）
- 危险命令（rm -rf、格式化等）
"""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Any

from tomatocat.plugins import Plugin

logger = logging.getLogger("plugin.shell_safety")

INTERACTIVE_COMMANDS = {
    "vi",
    "vim",
    "nvim",
    "nano",
    "sudoedit",
    "visudo",
    "emacs",
    "gedit",
    "notepad",
    "notepad++",
}

DANGEROUS_COMMANDS = {
    "rm",
    "del",
    "erase",
    "format",
    "mkfs",
    "fdisk",
    "dd",
    "shred",
}

DANGEROUS_OPTIONS = {
    "-rf",
    "--recursive",
    "--force",
    "/s",
    "/q",
}

PACKAGE_MANAGERS = {"pacman", "yay", "paru", "apt-get", "apt", "dnf", "yum", "brew", "pip"}
PACKAGE_WRITE_OPTIONS = {
    "--sync",
    "--remove",
    "--upgrade",
    "--sysupgrade",
    "install",
    "remove",
    "upgrade",
    "update",
}


class ShellSafetyPlugin(Plugin):
    name = "shell_safety"
    version = "0.1.0"
    desc = "阻止 shell 工具执行危险命令和交互式命令"

    async def initialize(self) -> None:
        logger.info("[shell_safety] Shell 安全防护已启用")

    async def on_tool_pre(self, tool_name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """工具调用前检查，返回 (是否允许, 拒绝原因)"""
        if tool_name != "shell":
            return True, ""

        command = str(arguments.get("command") or "").strip()
        if not command:
            return True, ""

        reason = self._deny_reason(command)
        if reason:
            logger.warning(f"[shell_safety] 拦截命令: {command[:50]}... 原因: {reason}")
            return False, reason

        return True, ""

    def _deny_reason(self, command: str) -> str:
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            try:
                tokens = command.split()
            except Exception:
                return ""
        if not tokens:
            return ""

        editor = self._find_interactive_command(tokens)
        if editor:
            return f"shell_safety 拦截：{editor} 会打开交互式界面，请改用非交互命令。"

        if self._sudo_needs_password(tokens):
            return "shell_safety 拦截：sudo 可能等待密码，请改用 sudo -n，让它在没有缓存时立即失败。"

        package_manager = self._find_interactive_package_command(tokens)
        if package_manager:
            return f"shell_safety 拦截：{package_manager} 写操作需要加 --noconfirm，避免卡在确认提示。"

        if self._opens_system_editor(tokens):
            return "shell_safety 拦截：该命令会打开系统编辑器，请改用写文件或非交互参数。"

        dangerous = self._find_dangerous_command(tokens)
        if dangerous:
            return f"shell_safety 拦截：{dangerous} 是危险命令，不允许执行。"

        return ""

    def _find_interactive_command(self, tokens: list[str]) -> str:
        for token in tokens:
            name = Path(token).name
            if name in INTERACTIVE_COMMANDS:
                return name
        return ""

    def _sudo_needs_password(self, tokens: list[str]) -> bool:
        for index, token in enumerate(tokens):
            if Path(token).name != "sudo":
                continue
            if not self._sudo_has_non_interactive_option(tokens[index + 1 :]):
                return True
        return False

    def _sudo_has_non_interactive_option(self, tokens: list[str]) -> bool:
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == "--":
                return False
            if not token.startswith("-") or token == "-":
                return False
            if token == "-n" or (token.startswith("-") and not token.startswith("--") and "n" in token[1:]):
                return True
            if token in {"-u", "-g", "-p", "-C", "-D", "-R", "-T", "-h"}:
                index += 2
                continue
            index += 1
        return False

    def _find_interactive_package_command(self, tokens: list[str]) -> str:
        for index, token in enumerate(tokens):
            name = Path(token).name
            if name not in PACKAGE_MANAGERS:
                continue
            args = tokens[index + 1 :]
            if self._has_package_write_option(args) and "--noconfirm" not in args and "-y" not in args:
                return name
        return ""

    def _has_package_write_option(self, args: list[str]) -> bool:
        for arg in args:
            if arg in PACKAGE_WRITE_OPTIONS:
                return True
            if arg.startswith("-S") or arg.startswith("-R") or arg.startswith("-U"):
                return True
        return False

    def _opens_system_editor(self, tokens: list[str]) -> bool:
        for index, token in enumerate(tokens[:-1]):
            name = Path(token).name
            if name == "systemctl" and tokens[index + 1] == "edit":
                return True
            if name == "crontab" and tokens[index + 1] == "-e":
                return True
        return False

    def _find_dangerous_command(self, tokens: list[str]) -> str:
        for token in tokens:
            name = Path(token).name
            if name in DANGEROUS_COMMANDS:
                return name
        return ""
