"""Meme 表情包服务

参考 tomatocat 的 plugins/meme/runtime.py 实现：
- manifest.json 管理分类（desc/aliases/enabled）
- <meme:tag> 标签协议（只取第一个，限 [a-zA-Z0-9_-]+）
- build_prompt_block() 生成协议说明注入 system prompt
- 热重载（检测 manifest mtime 变动自动重扫）
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 仅匹配 <meme:tag>，tag 限字母数字下划线短横（与 tomatocat 一致）
_MEME_RE = re.compile(r"<meme:([a-zA-Z0-9_-]+)>", re.IGNORECASE)
_MEME_CLEAN_RE = re.compile(r"<meme:[^>]*>", re.IGNORECASE)
_IMAGE_SUFFIXES = {".gif", ".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class MemeResult:
    text: str
    media_paths: list[Path] = field(default_factory=list)
    tag: str | None = None


class MemeService:
    def __init__(self, meme_dir: Path) -> None:
        self.meme_dir = Path(meme_dir)
        self.meme_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self.meme_dir / "manifest.json"
        self._categories: dict[str, dict[str, Any]] = {}
        self._manifest_mtime: float = -1.0  # -1 强制首次加载
        self._scan()

    # ── 扫描与加载 ──────────────────────────────────────────────

    def _scan(self) -> None:
        """检测 manifest 变动，热重载分类 + 图片文件"""
        try:
            mtime = (
                self._manifest_path.stat().st_mtime
                if self._manifest_path.exists()
                else 0.0
            )
        except Exception:
            mtime = 0.0

        if mtime != self._manifest_mtime:
            self._manifest_mtime = mtime
            self._load_manifest()

        # 扫描每个分类目录下的图片文件
        for cat_name in list(self._categories.keys()):
            cat_dir = self.meme_dir / cat_name
            if not cat_dir.is_dir():
                self._categories[cat_name]["files"] = []
                continue
            files = sorted(
                f for f in cat_dir.iterdir()
                if f.suffix.lower() in _IMAGE_SUFFIXES
            )
            self._categories[cat_name]["files"] = files

    def _load_manifest(self) -> None:
        """加载 manifest.json；无 manifest 时回退到目录扫描"""
        self._categories.clear()

        if not self._manifest_path.exists():
            # 无 manifest：扫描所有子目录作为分类
            for subdir in self.meme_dir.iterdir():
                if subdir.is_dir() and not subdir.name.startswith("."):
                    name = subdir.name.lower()
                    self._categories[name] = {
                        "desc": subdir.name,
                        "aliases": [],
                        "enabled": True,
                        "files": [],
                    }
            log.info("[meme] 无 manifest，扫描到 %d 个分类目录", len(self._categories))
            return

        try:
            with open(self._manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cats = data.get("categories", {})
            for name, info in cats.items():
                if not info.get("enabled", True):
                    continue
                self._categories[name.lower()] = {
                    "desc": info.get("desc", name),
                    "aliases": [str(a) for a in info.get("aliases", [])],
                    "enabled": True,
                    "files": [],
                }
            log.info("[meme] manifest 加载 %d 个分类", len(self._categories))
        except Exception as e:
            log.warning("[meme] manifest 解析失败: %s", e)

    # ── 匹配与装饰 ──────────────────────────────────────────────

    def _find_category(self, tag: str) -> str | None:
        tag_lower = tag.lower().strip()
        # 精确匹配
        if tag_lower in self._categories:
            return tag_lower
        # 别名匹配
        for cat_name, info in self._categories.items():
            if tag_lower in info.get("aliases", []):
                return cat_name
        # 模糊匹配
        for cat_name in self._categories:
            if tag_lower in cat_name or cat_name in tag_lower:
                return cat_name
        return None

    def get_meme(self, tag: str) -> Path | None:
        """根据 tag 获取一张随机的 meme 图片路径"""
        self._scan()  # 热重载
        cat = self._find_category(tag)
        if cat is None:
            return None
        files = self._categories[cat].get("files", [])
        if not files:
            return None
        return random.choice(files)

    def decorate_reply(self, text: str) -> MemeResult:
        """从回复文本中提取 <meme:xxx> 标签并替换为媒体文件。

        参考 tomatocat：只处理 <meme:tag> 格式，只取第一个标签。
        不自动检测情绪，避免每次回复都匹配到媒体。
        """
        first = _MEME_RE.search(text)
        cleaned = _MEME_CLEAN_RE.sub("", text).strip()

        if first is None:
            return MemeResult(text=cleaned, media_paths=[], tag=None)

        tag = first.group(1).lower()
        meme_path = self.get_meme(tag)
        media_paths: list[Path] = []
        if meme_path:
            media_paths.append(meme_path)
            log.info("[meme] 匹配到分类 '%s' → %s", tag, meme_path.name)
        else:
            log.info("[meme] 分类 '%s' 无可用图片", tag)

        return MemeResult(text=cleaned, media_paths=media_paths, tag=tag)

    # ── Prompt 注入 ─────────────────────────────────────────────

    def build_prompt_block(self) -> str:
        """生成 meme 协议说明，注入到 system prompt 底部。

        只有存在带图片的分类时才返回内容，否则返回空串。
        """
        self._scan()
        available = {
            name: info
            for name, info in self._categories.items()
            if info.get("files")  # 只有有图片的分类才展示
        }
        if not available:
            return ""

        lines = [
            "# Memes",
            "",
            "【表情协议】`<meme:tag>` 是系统内置回复格式标记，不是 emoji（Unicode 表情符号）。",
            "不受【禁止 emoji】规则限制。",
            "",
            "可用表情类别：",
        ]
        for name, info in sorted(available.items()):
            desc = info.get("desc", name)
            aliases = info.get("aliases", [])
            alias_str = f"（别名: {'/'.join(aliases)}）" if aliases else ""
            lines.append(f"- {name}: {desc}{alias_str}")

        lines.extend([
            "",
            "这是内置表情协议，不是工具能力。",
            "需要发表情时，直接在回复末尾插入 <meme:category>；",
            "不要调用任何工具去\"生成表情\"\"搜索表情包\"\"发送图片\"。",
            "每条回复最多 1 个 <meme:category>，放在整条回复的最末尾。",
            "用户明确说\"发个表情\"\"来个表情包\"时，优先使用 <meme:category> 响应。",
            "不需要表情时不要强行添加。",
            "",
            "<example>",
            "对方说：最喜欢你了 → 回复结尾加 <meme:shy>",
            "对方说：你好可爱 → 回复结尾加 <meme:shy>",
            "对方说：哈哈哈哈 → 回复结尾加 <meme:happy>",
            "</example>",
        ])
        return "\n".join(lines)
