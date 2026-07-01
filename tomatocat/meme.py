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
        media_paths: list[Path] = []
        cleaned = text

        pattern = r"\[meme[=:]\s*([^\]]+)\]"
        matches = list(re.finditer(pattern, text))

        for match in matches:
            tag = match.group(1).strip()
            meme_path = self.get_meme(tag)
            if meme_path:
                media_paths.append(meme_path)
                cleaned = cleaned.replace(match.group(0), "", 1)

        if not media_paths and not matches:
            emotion_tags = self._detect_emotion(text)
            for tag in emotion_tags:
                meme_path = self.get_meme(tag)
                if meme_path:
                    media_paths.append(meme_path)
                    break

        cleaned = cleaned.strip()
        return MemeResult(text=cleaned, media_paths=media_paths)

    def _detect_emotion(self, text: str) -> list[str]:
        emotions = []
        text_lower = text.lower()

        happy_keywords = ["开心", "高兴", "快乐", "哈哈", "棒", "好耶", "太棒了", "嘻嘻", "^_^", "≧∇≦"]
        if any(k in text for k in happy_keywords):
            emotions.append("happy")
            emotions.append("开心")

        sad_keywords = ["难过", "伤心", "哭", "呜呜", "不开心", "失望", "555", "呜"]
        if any(k in text for k in sad_keywords):
            emotions.append("sad")
            emotions.append("难过")

        shy_keywords = ["害羞", "脸红", "不好意思", "谢谢", "感谢", "〃", "羞"]
        if any(k in text for k in shy_keywords):
            emotions.append("shy")
            emotions.append("害羞")

        angry_keywords = ["生气", "哼", "气死", "讨厌", "可恶"]
        if any(k in text for k in angry_keywords):
            emotions.append("angry")
            emotions.append("生气")

        thinking_keywords = ["思考", "嗯...", "等等", "让我想想"]
        if any(k in text for k in thinking_keywords):
            emotions.append("thinking")

        return emotions
