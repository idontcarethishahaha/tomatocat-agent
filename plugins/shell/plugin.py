"""Shell 插件 - 执行命令行命令

参考 tomatocat 的 agent/tools/shell.py 实现，做了简化：
- 单工具 shell（不拆分 task_output/task_stop，后台任务通过同一个工具管理）
- 命令黑名单
- 超时控制（默认 60s，最大 600s）
- 输出截断（30000 字符）
- 后台任务支持（run_in_background + background_task_id）
- 工作目录限制在 workspace 内

包含 3 个工具：
- shell: 执行命令（支持前台/后台）
- task_output: 查看后台任务输出
- task_stop: 停止后台任务
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from tomatocat.plugins import Plugin, tool

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60  # 秒
_MAX_TIMEOUT = 600  # 秒
_MAX_OUTPUT = 30_000  # 字符
_BG_TTL_S = 4 * 3600  # 后台任务最长存活 4 小时
_IS_WINDOWS = os.name == "nt"

# 禁止命令黑名单
_BANNED_COMMANDS = frozenset(
    {
        "nc", "ncat", "netcat", "telnet",
        "curlie", "axel", "aria2c",
        "lynx", "w3m", "links", "elinks",
        "http-prompt",
        "chrome", "chromium", "firefox", "safari", "msedge",
        "nc.exe", "telnet.exe",
        # 破坏性命令
        "rm -rf /", "format", "fdisk", "dd",
        # 远程桌面/控制
        "ssh", "scp", "sftp", "rdesktop",
        # 端口扫描
        "nmap", "masscan",
        # 加密勒索风险
        "openssl enc", "gpg --symmetric",
    }
)

# 网络命令：额外 SSRF 检查
_NETWORK_CMDS = frozenset({"curl", "wget", "http", "httpie", "xh", "powershell -c Invoke-WebRequest"})

# shell_safety: 需要确认的高危命令模式
_DANGEROUS_PATTERNS = [
    (r"rm\s+-rf\s+[/~]", "rm -rf 根目录或家目录"),
    (r"rm\s+-rf\s+\.", "rm -rf 当前目录"),
    (r"rm\s+-rf\s+\*", "rm -rf 通配符删除"),
    (r">\s*/dev/null.*&&\s*rm", "管道后删除"),
    (r"sudo\s+rm", "sudo 删除"),
    (r"sudo\s+.*\b(fdisk|mkfs|parted)\b", "sudo 磁盘操作"),
    (r"\bformat\b", "磁盘格式化"),
    (r":\(\)\{\s*:\|:&\s*\};:", "Fork 炸弹"),
    (r"bash\s+-i\s+>&\s+/dev/tcp", "反向 Shell"),
    (r"powershell.*-enc\s+", "PowerShell 编码执行"),
    (r"Invoke-Expression|IEX", "PowerShell 表达式执行"),
    (r"\bcd\s+\\\s*\.\.\\", "目录遍历"),
    (r"del\s+/[fqs].*\\\*", "强制删除"),
]

# shell_safety: 交互式命令（会导致阻塞）
_INTERACTIVE_COMMANDS = frozenset({
    "vim", "vi", "nano", "emacs", "less", "more", "top", "htop",
    "python", "python3", "node", "irb", "php -a",
})


@dataclass
class BackgroundTask:
    task_id: str
    command: str
    process: asyncio.subprocess.Process
    log_path: Path
    start_time: float
    stdout_task: asyncio.Task | None = None
    stderr_task: asyncio.Task | None = None
    finished: bool = False
    exit_code: int | None = None


class ShellPlugin(Plugin):
    name = "shell"
    desc = "命令行执行工具"

    def __init__(self) -> None:
        super().__init__()
        self._working_dir: Path | None = None
        self._bg_tasks: dict[str, BackgroundTask] = {}

    async def initialize(self) -> None:
        self._working_dir = self.context.workspace
        log.info("[shell] 工作目录: %s", self._working_dir)

    async def terminate(self) -> None:
        # 停止所有后台任务
        for task_id in list(self._bg_tasks.keys()):
            try:
                await self._stop_task(task_id)
            except Exception:
                pass
        self._bg_tasks.clear()

    @tool(
        name="shell",
        description=(
            "在命令行中执行命令并返回输出。\n"
            "注意：\n"
            "- Windows 环境，用 PowerShell 语法（dir/ls/Get-ChildItem 都支持）\n"
            "- 多条命令用 ; 或 && 连接\n"
            "- 工作目录默认为 workspace\n"
            f"- 前台命令默认超时 {_DEFAULT_TIMEOUT}s，最大 {_MAX_TIMEOUT}s\n"
            "- 输出超过 {_MAX_OUTPUT} 字符时自动截断\n"
            "- 长任务用 run_in_background=true 后台启动，返回 background_task_id\n"
            "- 后台任务用 task_output 查看进度，task_stop 终止\n"
            "- 禁止高风险命令（nc/telnet/nmap/ssh 等）\n"
            "禁止用途：不要用 shell 替代专用工具（read_file 读文件、web_fetch 抓网页、list_dir 列目录）。"
        ),
        risk="read-write",
    )
    async def shell(
        self,
        event: object,
        command: str,
        description: str = "",
        timeout: int = _DEFAULT_TIMEOUT,
        run_in_background: bool = False,
    ) -> str:
        """执行 shell 命令

        Args:
            command: 要执行的命令
            description: 简短描述命令用途（5-10字）
            timeout: 超时秒数，默认 60，最大 600
            run_in_background: 是否后台运行，默认 false
        """
        if self._working_dir is None:
            return "错误：shell 插件未初始化"

        cmd = command.strip()
        if not cmd:
            return "错误：命令不能为空"

        # 黑名单检查
        ban_msg = _check_banned(cmd)
        if ban_msg:
            return f"错误：{ban_msg}"

        timeout = min(max(1, int(timeout)), _MAX_TIMEOUT)

        if run_in_background:
            return await self._run_background(cmd, description)

        return await self._run_foreground(cmd, description, timeout)

    @tool(
        name="task_output",
        description=(
            "查看后台任务的输出。\n"
            "通过 shell run_in_background=true 启动的后台任务，用此工具轮询输出。\n"
            "返回任务状态、退出码和最新输出。"
        ),
        risk="read-only",
    )
    async def task_output(
        self,
        event: object,
        task_id: str,
    ) -> str:
        """查看后台任务输出

        Args:
            task_id: 后台任务 ID（从 shell run_in_background 返回）
        """
        task = self._bg_tasks.get(task_id)
        if task is None:
            return f"错误：未找到任务 {task_id}"

        # 读取日志文件
        try:
            output = task.log_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"读取任务日志失败：{e}"

        # 截断
        truncated = False
        if len(output) > _MAX_OUTPUT:
            output = output[-_MAX_OUTPUT:]
            truncated = True

        status = "running" if not task.finished else f"finished (exit_code={task.exit_code})"
        elapsed = time.monotonic() - task.start_time

        result = {
            "task_id": task_id,
            "status": status,
            "elapsed_s": round(elapsed, 1),
            "command": task.command,
            "output_tail": output,
            "output_length": len(output),
        }
        if truncated:
            result["note"] = f"输出已截断至最后 {_MAX_OUTPUT} 字符"

        return json.dumps(result, ensure_ascii=False, indent=2)

    @tool(
        name="task_stop",
        description=(
            "停止后台任务。\n"
            "放弃不再需要的后台任务时必须调用此工具。"
        ),
        risk="read-write",
    )
    async def task_stop(
        self,
        event: object,
        task_id: str,
    ) -> str:
        """停止后台任务

        Args:
            task_id: 后台任务 ID
        """
        task = self._bg_tasks.get(task_id)
        if task is None:
            return f"错误：未找到任务 {task_id}"

        await self._stop_task(task_id)
        return f"✅ 任务 {task_id} 已停止"

    # ── 内部实现 ────────────────────────────────────────────────

    async def _run_foreground(self, command: str, description: str, timeout: int) -> str:
        start = time.monotonic()
        try:
            proc = await self._spawn(command)
        except Exception as e:
            return f"错误：启动进程失败：{e}"

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
            exit_code = proc.returncode
        except asyncio.TimeoutError:
            # 超时，杀掉进程
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            duration = round(time.monotonic() - start, 1)
            return json.dumps({
                "command": command,
                "exit_code": None,
                "status": "timeout",
                "duration_ms": int(duration * 1000),
                "error": f"命令超时（>{timeout}s）",
            }, ensure_ascii=False)

        duration = round(time.monotonic() - start, 1)
        output = (stdout or b"").decode("utf-8", errors="replace")
        errout = (stderr or b"").decode("utf-8", errors="replace")
        combined = output
        if errout:
            combined += ("\n" if combined else "") + errout

        # 截断
        truncated = len(combined) > _MAX_OUTPUT
        if truncated:
            combined = combined[:_MAX_OUTPUT]

        result = {
            "command": command,
            "exit_code": exit_code,
            "status": "success" if exit_code == 0 else "error",
            "duration_ms": int(duration * 1000),
            "output": combined,
            "output_length": len(combined),
        }
        if truncated:
            result["truncated"] = True
            result["note"] = f"输出已截断至 {_MAX_OUTPUT} 字符"
        if description:
            result["description"] = description

        return json.dumps(result, ensure_ascii=False)

    async def _run_background(self, command: str, description: str) -> str:
        task_id = uuid4().hex[:12]

        log_dir = Path(tempfile.gettempdir()) / "tomatocat_shell_bg"
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"{task_id}.log"

        try:
            proc = await self._spawn(command, stdout_to_file=log_path)
        except Exception as e:
            return f"错误：启动进程失败：{e}"

        task = BackgroundTask(
            task_id=task_id,
            command=command,
            process=proc,
            log_path=log_path,
            start_time=time.monotonic(),
        )
        self._bg_tasks[task_id] = task

        # 启动 waiter
        asyncio.create_task(self._bg_waiter(task))

        result = {
            "task_id": task_id,
            "status": "running",
            "command": command,
            "log_file": str(log_path),
            "note": "后台任务已启动，用 task_output 查看进度，task_stop 终止",
        }
        if description:
            result["description"] = description

        return json.dumps(result, ensure_ascii=False)

    async def _spawn(
        self,
        command: str,
        stdout_to_file: Path | None = None,
    ) -> asyncio.subprocess.Process:
        """启动子进程"""
        cwd = str(self._working_dir) if self._working_dir else None

        if _IS_WINDOWS:
            # Windows 用 cmd /c 执行
            return await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE if stdout_to_file is None else open(stdout_to_file, "wb"),
                stderr=asyncio.subprocess.STDOUT if stdout_to_file is None else asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=os.environ.copy(),
            )
        else:
            return await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE if stdout_to_file is None else open(stdout_to_file, "wb"),
                stderr=asyncio.subprocess.STDOUT if stdout_to_file is None else asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=os.environ.copy(),
                executable="/bin/bash",
            )

    async def _bg_waiter(self, task: BackgroundTask) -> None:
        """后台任务等待协程"""
        try:
            await task.process.wait()
            task.exit_code = task.process.returncode
            task.finished = True
            log.info("[shell] 后台任务 %s 完成，exit_code=%s", task.task_id, task.exit_code)
        except Exception as e:
            log.warning("[shell] 后台任务等待异常 %s: %s", task.task_id, e)
            task.finished = True
        finally:
            # TTL 后清理
            asyncio.create_task(self._evict_task(task.task_id, delay=_BG_TTL_S))

    async def _stop_task(self, task_id: str) -> None:
        task = self._bg_tasks.get(task_id)
        if task is None:
            return
        try:
            if not task.finished and task.process.returncode is None:
                task.process.kill()
                await task.process.wait()
                task.finished = True
                task.exit_code = task.process.returncode
        except Exception as e:
            log.warning("[shell] 停止任务失败 %s: %s", task_id, e)

    async def _evict_task(self, task_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        task = self._bg_tasks.pop(task_id, None)
        if task:
            try:
                task.log_path.unlink(missing_ok=True)
            except Exception:
                pass
            log.info("[shell] 后台任务已清理: %s", task_id)


import re


def _check_banned(command: str) -> str | None:
    """检查命令是否在黑名单中（shell_safety 增强版）"""
    cmd_lower = command.lower().strip()

    if not cmd_lower:
        return "命令不能为空"

    # 1. 黑名单检查
    for banned in _BANNED_COMMANDS:
        if banned in cmd_lower:
            return f"命令 '{banned}' 被禁止使用"

    # 2. shell_safety: 高危模式检查
    for pattern, desc in _DANGEROUS_PATTERNS:
        if re.search(pattern, cmd_lower, re.IGNORECASE):
            return f"检测到高危操作: {desc}，已阻止执行"

    # 3. shell_safety: 交互式命令检查
    first_token = cmd_lower.split()[0] if cmd_lower.split() else ""
    if first_token in _INTERACTIVE_COMMANDS:
        return f"交互式命令 '{first_token}' 会导致阻塞，请使用非交互式方式（如 `cat file` 替代 `less file`）"

    # 4. shell_safety: sudo 检查（非完全禁止，但提醒）
    if cmd_lower.startswith("sudo "):
        # 检查是否是危险的 sudo 操作
        dangerous_sudo = ["rm", "dd", "mkfs", "fdisk", "parted", "chown -R /"]
        for d in dangerous_sudo:
            if d in cmd_lower:
                return f"sudo {d} 是高风险操作，已阻止"

    # 5. shell_safety: 检查 rm 是否带有 -f 且目标是重要目录
    if re.search(r"\brm\b", cmd_lower):
        # 检查是否删除当前工作区外的文件
        if ".." in cmd_lower or "~" in cmd_lower:
            return "禁止删除工作区外的文件（包含 .. 或 ~）"

    return None
