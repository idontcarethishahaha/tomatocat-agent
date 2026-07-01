from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class MemeResult:
    text: str
    media_paths: list[Path]


class MemeService:
    def __init__(self, meme_dir: Path):
        self.meme_dir = meme_dir
        self.meme_dir.mkdir(exist_ok=True)
        self._categories: dict[str, list[Path]] = {}
        self._scan()

    def _scan(self) -> None:
        self._categories.clear()
        for subdir in self.meme_dir.iterdir():
            if subdir.is_dir():
                files = []
                for f in subdir.iterdir():
                    if f.suffix.lower() in (".gif", ".jpg", ".jpeg", ".png", ".webp", ".mp4"):
                        files.append(f)
                if files:
                    self._categories[subdir.name.lower()] = files
        log.info(f"[meme] 已加载 {len(self._categories)} 个分类")

    def _find_category(self, tag: str) -> str | None:
        tag_lower = tag.lower()
        if tag_lower in self._categories:
            return tag_lower
        for cat in self._categories:
            if tag_lower in cat or cat in tag_lower:
                return cat
        return None

    def get_meme(self, tag: str) -> Path | None:
        cat = self._find_category(tag)
        if cat is None:
            return None
        files = self._categories[cat]
        return random.choice(files)

    def decorate_reply(self, text: str) -> MemeResult:
        """从回复文本中提取 [meme:xxx] 标签并替换为媒体文件。

        参考 tomatocat 的做法：只有 AI 显式输出标签时才发表情包，
        不自动检测情绪，避免每次回复都匹配到媒体。
        """
        media_paths: list[Path] = []
        cleaned = text

        # 支持 [meme:xxx] 和 <meme:xxx> 两种格式
        pattern = r"[<\[]meme[=:]\s*([^\]>]+)[>\]]"
        matches = list(re.finditer(pattern, text, re.IGNORECASE))

        for match in matches:
            tag = match.group(1).strip()
            meme_path = self.get_meme(tag)
            if meme_path:
                media_paths.append(meme_path)
            cleaned = cleaned.replace(match.group(0), "", 1)

        cleaned = cleaned.strip()
        return MemeResult(text=cleaned, media_paths=media_paths)
