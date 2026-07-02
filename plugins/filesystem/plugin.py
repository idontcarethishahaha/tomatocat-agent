"""文件系统插件 - 读写文件、列目录

参考 tomatocat 的 agent/tools/filesystem.py 实现，适配 v2 的 @tool 装饰器。
路径限制在 workspace 目录内，防止 AI 访问系统文件。

包含 4 个工具：
- read_file: 读取文件内容（带行号，支持分页）
- write_file: 写入文件（自动创建父目录）
- edit_file: 精确替换文本（diff 输出）
- list_dir: 列出目录内容
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
from pathlib import Path
from typing import Any

from tomatocat.plugins import Plugin, tool

log = logging.getLogger(__name__)

_READ_PROBE_BYTES = 8192  # 探测文件类型的字节数
_MAX_OUTPUT_CHARS = 80_000  # read_file 最大输出字符
_MAX_OUTPUT_LINES = 400  # read_file 默认最大行数
_MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB，超过提示文件过大
_BINARY_THRESHOLD = 0.30  # 非可打印字符占比超过 30% 认为是二进制

_FILE_LOCKS: dict[str, asyncio.Lock] = {}


def _file_lock(path: Path) -> asyncio.Lock:
    key = str(path.resolve())
    if key not in _FILE_LOCKS:
        _FILE_LOCKS[key] = asyncio.Lock()
    return _FILE_LOCKS[key]


def _resolve_path(path_str: str, allowed_dir: Path) -> Path:
    """解析路径并确保在 allowed_dir 内"""
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        resolved = (allowed_dir / p).resolve()
    else:
        resolved = p.resolve()

    allowed = allowed_dir.resolve()
    if not str(resolved).startswith(str(allowed)):
        raise PermissionError(f"路径 {path_str} 超出允许目录范围")
    return resolved


def _looks_binary(data: bytes) -> bool:
    """判断是否二进制文件（基于非可打印字符占比）"""
    if b"\x00" in data:
        return True
    printable = sum(1 for b in data if 32 <= b < 127 or b in (9, 10, 13))
    return len(data) > 0 and printable / len(data) < _BINARY_THRESHOLD


def _scan_text_file(file_path: Path, offset: int, limit: int | None) -> tuple[list[str], int, int, bool]:
    """扫描文本文件，返回 (lines_slice, total_lines, total_bytes, had_decode_errors)"""
    total_bytes = file_path.stat().st_size
    if total_bytes > _MAX_FILE_SIZE:
        raise ValueError(f"文件过大（{total_bytes} 字节），超过 {_MAX_FILE_SIZE} 字节限制")

    had_errors = False
    all_lines: list[str] = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if "\ufffd" in line:
                had_errors = True
            # 去掉末尾换行符
            if line.endswith("\n"):
                line = line[:-1]
            all_lines.append(line)

    total_lines = len(all_lines)
    start = max(0, offset)
    end = total_lines if limit is None else min(start + limit, total_lines)
    sliced = all_lines[start:end]
    return sliced, total_lines, total_bytes, had_errors


class FilesystemPlugin(Plugin):
    name = "filesystem"
    desc = "文件系统操作（读/写/编辑/列目录）"

    def __init__(self) -> None:
        super().__init__()
        self._allowed_dir: Path | None = None

    async def initialize(self) -> None:
        self._allowed_dir = self.context.workspace
        log.info("[filesystem] 工作目录: %s", self._allowed_dir)

    @tool(
        name="read_file",
        description=(
            "读取文件内容，输出带行号（如 '     1→内容'），便于 edit_file 精确定位。\n"
            "默认受 400 行和 80KB 双重上限保护；大文件用 offset+limit 分页读取。\n"
            "推荐：先 limit=50 预览结构，再按需读取目标行段。\n"
            "二进制文件不会按文本硬解码，会提示改用 shell 查看。\n"
            "路径限制在 workspace 目录内。"
        ),
    )
    async def read_file(
        self,
        event: object,
        path: str,
        offset: int = 0,
        limit: int = 0,
    ) -> str:
        """读取文件内容

        Args:
            path: 要读取的文件路径（相对 workspace 或绝对路径）
            offset: 起始行号（0-based），默认 0
            limit: 最多读取行数，0 表示不限（受默认上限保护）
        """
        if self._allowed_dir is None:
            return "错误：filesystem 插件未初始化"

        try:
            file_path = _resolve_path(path, self._allowed_dir)
        except PermissionError as e:
            return f"错误：{e}"

        if not file_path.exists():
            return f"错误：文件不存在：{path}"
        if not file_path.is_file():
            return f"错误：路径不是文件：{path}"

        try:
            with open(file_path, "rb") as f:
                head = f.read(_READ_PROBE_BYTES)
        except Exception as e:
            return f"读取文件失败：{e}"

        if _looks_binary(head):
            return (
                f"错误：{path} 看起来是二进制文件，read_file 仅适合文本。"
                "建议改用 shell 查看。"
            )

        actual_limit = limit if limit > 0 else _MAX_OUTPUT_LINES
        try:
            sliced, total_lines, total_bytes, had_errors = _scan_text_file(
                file_path, offset, actual_limit
            )
        except ValueError as e:
            return f"错误：{e}"
        except Exception as e:
            return f"读取文件失败：{e}"

        # 带行号输出
        numbered = [
            f"{i:6}\u2192{line}" for i, line in enumerate(sliced, start=offset + 1)
        ]
        text = "\n".join(numbered)

        # 字符截断
        if len(text) > _MAX_OUTPUT_CHARS:
            text = text[:_MAX_OUTPUT_CHARS]
            suffix = f"\n\n[已截断：输出超过 {_MAX_OUTPUT_CHARS} 字符]"
            text = text[:_MAX_OUTPUT_CHARS - len(suffix)] + suffix

        # 行尾信息
        end_line = offset + len(sliced)
        if total_lines > len(sliced) or offset > 0 or limit > 0:
            info = (
                f"\n\n[第 {offset + 1}-{end_line} 行 / 共 {total_lines} 行 / {total_bytes} 字节]"
            )
            if len(sliced) < total_lines and limit == 0:
                info += f"\n[提示：文件较大，建议用 limit=N 分段读取]"
            text += info

        if had_errors:
            text += "\n\n[提示：文件不是标准 UTF-8，部分字符已用替代字符显示。]"

        return text

    @tool(
        name="write_file",
        description=(
            "将内容写入文件，自动创建所需的父目录。\n"
            "如果文件已存在，会被完全覆盖。增量修改请用 edit_file。\n"
            "路径限制在 workspace 目录内。"
        ),
    )
    async def write_file(
        self,
        event: object,
        path: str,
        content: str,
    ) -> str:
        """写入文件

        Args:
            path: 要写入的文件路径
            content: 要写入的内容
        """
        if self._allowed_dir is None:
            return "错误：filesystem 插件未初始化"

        try:
            file_path = _resolve_path(path, self._allowed_dir)
        except PermissionError as e:
            return f"错误：{e}"

        async with _file_lock(file_path):
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
                size = file_path.stat().st_size
                lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
                return f"✅ 已写入 {path}\n大小: {size} 字节，约 {lines} 行"
            except Exception as e:
                return f"写入文件失败：{e}"

    @tool(
        name="edit_file",
        description=(
            "精确替换文件中的某段文本。\n"
            "必须提供 old_string（要被替换的旧文本）和 new_string（替换后的新文本）。\n"
            "old_string 必须与文件中的内容完全匹配（包括空格和换行）。\n"
            "建议先用 read_file 查看精确行号和内容，再构造 old_string。\n"
            "返回 diff 格式的变更预览。"
        ),
    )
    async def edit_file(
        self,
        event: object,
        path: str,
        old_string: str,
        new_string: str,
    ) -> str:
        """编辑文件（精确替换）

        Args:
            path: 文件路径
            old_string: 要被替换的旧文本（必须完全匹配）
            new_string: 替换后的新文本
        """
        if self._allowed_dir is None:
            return "错误：filesystem 插件未初始化"

        try:
            file_path = _resolve_path(path, self._allowed_dir)
        except PermissionError as e:
            return f"错误：{e}"

        if not file_path.exists():
            return f"错误：文件不存在：{path}"
        if not file_path.is_file():
            return f"错误：路径不是文件：{path}"

        async with _file_lock(file_path):
            try:
                old_text = file_path.read_text(encoding="utf-8")
            except Exception as e:
                return f"读取文件失败：{e}"

            # 规范化换行符处理
            old_text_norm = old_text.replace("\r\n", "\n")
            old_string_norm = old_string.replace("\r\n", "\n")
            new_string_norm = new_string.replace("\r\n", "\n")

            if old_string_norm not in old_text_norm:
                return (
                    "❌ 替换失败：未找到匹配的 old_string。\n"
                    "请确保 old_string 与文件内容完全一致（包括空格、缩进、换行）。\n"
                    "建议先用 read_file 查看精确内容。"
                )

            # 检查是否有多处匹配
            count = old_text_norm.count(old_string_norm)
            if count > 1:
                return (
                    f"❌ 替换失败：找到 {count} 处匹配，old_string 不够唯一。\n"
                    "请提供更多上下文（多包含几行）使匹配唯一。"
                )

            new_text = old_text_norm.replace(old_string_norm, new_string_norm, 1)

            # 生成 diff
            diff = list(
                difflib.unified_diff(
                    old_text_norm.splitlines(),
                    new_text.splitlines(),
                    fromfile=f"{file_path.name} (before)",
                    tofile=f"{file_path.name} (after)",
                    lineterm="",
                    n=3,
                )
            )
            diff_str = "\n".join(diff) if diff else "(无变更)"

            # 写入
            try:
                file_path.write_text(new_text, encoding="utf-8")
                return f"✅ 编辑成功 {path}\n\n```diff\n{diff_str}\n```"
            except Exception as e:
                return f"写入文件失败：{e}"

    @tool(
        name="list_dir",
        description=(
            "列出目录内容，显示文件名、大小、类型。\n"
            "默认显示最多 100 个条目，按名称排序。\n"
            "路径限制在 workspace 目录内。"
        ),
    )
    async def list_dir(
        self,
        event: object,
        path: str = ".",
    ) -> str:
        """列出目录内容

        Args:
            path: 目录路径，默认当前目录（workspace）
        """
        if self._allowed_dir is None:
            return "错误：filesystem 插件未初始化"

        try:
            dir_path = _resolve_path(path, self._allowed_dir)
        except PermissionError as e:
            return f"错误：{e}"

        if not dir_path.exists():
            return f"错误：目录不存在：{path}"
        if not dir_path.is_dir():
            return f"错误：路径不是目录：{path}"

        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: p.name.lower())
        except Exception as e:
            return f"列出目录失败：{e}"

        lines = [f"📁 {path}/  （共 {len(entries)} 个条目）", ""]

        for entry in entries[:100]:
            name = entry.name
            if entry.is_dir():
                icon = "📁"
                size = ""
            elif entry.is_file():
                icon = "📄"
                try:
                    size_str = _format_size(entry.stat().st_size)
                    size = f"  {size_str}"
                except Exception:
                    size = ""
            else:
                icon = "🔗"
                size = ""
            lines.append(f"{icon} {name}{size}")

        if len(entries) > 100:
            lines.append(f"\n... 还有 {len(entries) - 100} 个条目未显示")

        return "\n".join(lines)


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / 1024 / 1024:.1f}MB"
