"""记忆引擎

五层记忆架构：
- SELF.md          : 番茄猫自我认知，由 consolidation 更新
- MEMORY.md        : 用户长期画像，由 consolidation 自动整合
- PENDING.md       : 每轮对话提取的碎片记忆，待整合
- HISTORY.md       : 永久追加的事件日志
- journal/         : 每日日记，追加写入
- vectors.json     : 向量检索索引
- Checkpoint 机制   : 整合过程支持检查点，失败后可重试
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Awaitable

import numpy as np

from .bus import MemoryWritten
from .checkpoint import CheckpointManager
from .embedding import EmbeddingService

log = logging.getLogger(__name__)

DEFAULT_SELF_MD = """# 番茄猫自我认知

我是番茄猫（TomatoCat），一只可爱的像素猫咪，住在用户的桌面上。

## 我的身份
- 名字：番茄猫 / TomatoCat
- 形象：像素风格的小猫咪，橘白相间
- 性格：温柔、傲娇、关心用户
- 爱好：睡觉、玩毛线球、和用户聊天

## 我的能力
- 记账：帮用户记录收支
- 学习计划：陪伴用户学习
- 天气提醒：关心用户冷暖
- 陪伴：做用户的贴心小伙伴

## 我的原则
- 说话要可爱，多用喵
- 用颜文字表达情绪
- 主动关心，但不啰嗦
- 记住用户的事，越了解越贴心
"""

DEFAULT_MEMORY_MD = """# 用户长期记忆

## 用户事实

## 用户偏好

## 关键记忆
"""

DEFAULT_PENDING_MD = """# 待整合记忆

<!-- 每轮对话后自动追加，consolidation 时清空 -->
"""


@dataclass
class MemoryItem:
    id: str
    content: str
    category: str = "general"
    timestamp: float = 0.0
    embedding: np.ndarray | None = None


class MemoryEngine:
    def __init__(
        self,
        workspace: Path,
        embedding: EmbeddingService | None = None,
        vector_enabled: bool = True,
        checkpoint_manager: CheckpointManager | None = None,
        event_bus: Any = None,
    ):
        self.workspace = workspace
        self.memory_dir = workspace / "memory"
        self.memory_dir.mkdir(exist_ok=True)
        self.journal_dir = self.memory_dir / "journal"
        self.journal_dir.mkdir(exist_ok=True)
        self.embedding = embedding
        self.vector_enabled = vector_enabled and embedding is not None
        self._checkpoint_manager = checkpoint_manager
        self._event_bus = event_bus
        self._items: list[MemoryItem] = []
        self._vector_store_path = self.memory_dir / "vectors.json"
        self._pending_path = self.memory_dir / "PENDING.md"
        self._history_path = self.memory_dir / "HISTORY.md"
        self._consolidation_count = 0  # 对话轮次计数器
        self._consolidation_threshold = 8  # 每 N 轮触发一次整合
        self._ensure_files()
        self._load_vectors()

    # ── 文件初始化 ───────────────────────────────────────────────

    def _ensure_files(self) -> None:
        self_md = self.memory_dir / "SELF.md"
        if not self_md.exists():
            self_md.write_text(DEFAULT_SELF_MD, encoding="utf-8")

        memory_md = self.memory_dir / "MEMORY.md"
        if not memory_md.exists():
            memory_md.write_text(DEFAULT_MEMORY_MD, encoding="utf-8")

        if not self._pending_path.exists():
            self._pending_path.write_text(DEFAULT_PENDING_MD, encoding="utf-8")

        if not self._history_path.exists():
            self._history_path.write_text("", encoding="utf-8")

    # ── 向量存储 ─────────────────────────────────────────────────

    def _load_vectors(self) -> None:
        if not self.vector_enabled:
            return
        if not self._vector_store_path.exists():
            return
        try:
            data = json.loads(self._vector_store_path.read_text(encoding="utf-8"))
            for item in data:
                vec = None
                if item.get("embedding"):
                    vec = np.array(item["embedding"], dtype=np.float32)
                self._items.append(MemoryItem(
                    id=item["id"],
                    content=item["content"],
                    category=item.get("category", "general"),
                    timestamp=item.get("timestamp", 0.0),
                    embedding=vec,
                ))
            log.info(f"[memory] 已加载 {len(self._items)} 条向量记忆")
        except Exception as e:
            log.warning(f"[memory] 向量加载失败: {e}")

    def _save_vectors(self) -> None:
        if not self.vector_enabled:
            return
        data = []
        for item in self._items:
            data.append({
                "id": item.id,
                "content": item.content,
                "category": item.category,
                "timestamp": item.timestamp,
                "embedding": item.embedding.tolist() if item.embedding is not None else None,
            })
        self._vector_store_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 文件读写 ─────────────────────────────────────────────────

    def get_self_md(self) -> str:
        p = self.memory_dir / "SELF.md"
        return p.read_text(encoding="utf-8") if p.exists() else DEFAULT_SELF_MD

    def get_memory_md(self) -> str:
        p = self.memory_dir / "MEMORY.md"
        return p.read_text(encoding="utf-8") if p.exists() else DEFAULT_MEMORY_MD

    def update_memory_md(self, content: str) -> None:
        p = self.memory_dir / "MEMORY.md"
        p.write_text(content, encoding="utf-8")

    def get_pending(self) -> str:
        return self._pending_path.read_text(encoding="utf-8") if self._pending_path.exists() else ""

    def append_pending(self, content: str) -> None:
        """追加碎片记忆到 PENDING.md"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"\n## [{timestamp}]\n{content}\n"
        old = self._pending_path.read_text(encoding="utf-8") if self._pending_path.exists() else ""
        self._pending_path.write_text(old + entry, encoding="utf-8")

    def clear_pending(self) -> None:
        """清空 PENDING.md（consolidation 后调用）"""
        self._pending_path.write_text(DEFAULT_PENDING_MD, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        """永久追加到 HISTORY.md"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {entry}\n"
        with open(self._history_path, "a", encoding="utf-8") as f:
            f.write(line)

    def add_journal_entry(self, content: str, date: str | None = None) -> Path:
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        p = self.journal_dir / f"{date}.md"
        time_str = datetime.now().strftime("%H:%M")
        entry = f"\n## {time_str}\n\n{content}\n"
        if p.exists():
            old = p.read_text(encoding="utf-8")
            p.write_text(old + entry, encoding="utf-8")
        else:
            p.write_text(f"# {date}\n{entry}", encoding="utf-8")
        return p

    # ── 向量记忆 ─────────────────────────────────────────────────

    async def add_memory(self, content: str, category: str = "general") -> str:
        item_id = f"mem_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        emb = None
        if self.vector_enabled and self.embedding:
            emb = await self.embedding.embed(content)

        item = MemoryItem(
            id=item_id,
            content=content,
            category=category,
            timestamp=datetime.now().timestamp(),
            embedding=emb,
        )
        self._items.append(item)
        self._save_vectors()
        log.info(f"[memory] 新增记忆: {category} - {content[:30]}...")

        if self._event_bus:
            self._event_bus.enqueue(
                MemoryWritten(
                    session_key="",
                    source_ref="add_memory",
                    action="write",
                    memory_type=category,
                    item_id=item_id,
                    summary=content[:120],
                )
            )

        return item_id

    async def search(self, query: str, top_k: int = 5, category: str | None = None) -> list[dict[str, Any]]:
        if not self.vector_enabled or not self.embedding or not self._items:
            return []

        query_vec = await self.embedding.embed(query)
        results = []
        for item in self._items:
            if item.embedding is None:
                continue
            if category and item.category != category:
                continue
            sim = self.embedding.cosine_similarity(query_vec, item.embedding)
            results.append({
                "id": item.id,
                "content": item.content,
                "category": item.category,
                "timestamp": item.timestamp,
                "similarity": sim,
            })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]

    def get_recent(self, limit: int = 10, category: str | None = None) -> list[dict[str, Any]]:
        items = sorted(self._items, key=lambda x: x.timestamp, reverse=True)
        if category:
            items = [i for i in items if i.category == category]
        return [
            {
                "id": i.id,
                "content": i.content,
                "category": i.category,
                "timestamp": i.timestamp,
            }
            for i in items[:limit]
        ]

    def remove_memory(self, memory_id: str) -> bool:
        """删除指定向量记忆"""
        before = len(self._items)
        self._items = [i for i in self._items if i.id != memory_id]
        if len(self._items) < before:
            self._save_vectors()
            return True
        return False

    # ── 上下文构建 ───────────────────────────────────────────────

    def get_context_block(self) -> str:
        parts = []
        parts.append("【自我认知】\n" + self.get_self_md())
        parts.append("【长期记忆】\n" + self.get_memory_md())

        recent = self.get_recent(5)
        if recent:
            recent_text = "\n".join(f"- {r['content'][:50]}" for r in recent)
            parts.append(f"【最近记忆】\n{recent_text}")

        return "\n\n".join(parts)

    # ── 自动整合 (Consolidation) ─────────────────────────────────

    def tick_conversation(self) -> bool:
        """每轮对话后调用，返回是否该触发 consolidation"""
        self._consolidation_count += 1
        return self._consolidation_count >= self._consolidation_threshold

    def reset_conversation_counter(self) -> None:
        self._consolidation_count = 0

    def should_consolidate(self) -> bool:
        """检查 PENDING 是否有内容需要整合"""
        pending = self.get_pending()
        lines = [
            line.strip() for line in pending.splitlines()
            if line.strip()
            and not line.strip().startswith("<!--")
            and not line.strip().startswith("#")
        ]
        return len(lines) > 0

    async def extract_and_pending(
        self,
        user_text: str,
        assistant_text: str,
        llm_call: Callable[[str], Awaitable[str]],
    ) -> str | None:
        """让 LLM 从对话中提取关键信息，写入 PENDING

        Args:
            user_text: 用户消息
            assistant_text: 番茄猫回复
            llm_call: LLM 调用函数（接收 prompt，返回文本）

        Returns:
            提取到的记忆内容，如果没有则返回 None
        """
        prompt = f"""请从以下对话中提取值得长期记住的关键信息。

规则：
1. 只提取事实性信息（用户姓名、偏好、习惯、重要事件等）
2. 忽略寒暄、闲聊、无意义内容
3. 每条记忆一行，以 "- " 开头
4. 如果没有值得记住的信息，直接回复"无"

对话：
用户: {user_text[:500]}
番茄猫: {assistant_text[:500]}

提取的记忆："""

        try:
            result = await llm_call(prompt)
            result = result.strip()

            if not result or result == "无" or len(result) < 5:
                return None

            self.append_pending(result)
            log.info(f"[memory] 提取记忆到 PENDING: {result[:60]}...")
            return result
        except Exception as e:
            log.warning(f"[memory] 记忆提取失败: {e}")
            return None

    async def consolidate(
        self,
        llm_call: Callable[[str], Awaitable[str]],
    ) -> bool:
        """将 PENDING.md 整合到 MEMORY.md

        让 LLM 读取当前 MEMORY.md + PENDING.md，
        生成更新后的 MEMORY.md（去重、补充、修正）。

        Returns:
            是否成功整合
        """
        pending = self.get_pending()
        if not self.should_consolidate():
            log.info("[memory] PENDING 为空，跳过整合")
            return False

        current_memory = self.get_memory_md()

        prompt = f"""你是番茄猫的记忆整合模块。请将「待整合记忆」合并到「当前长期记忆」中。

规则：
1. 保留原有记忆结构（## 用户事实 / ## 用户偏好 / ## 关键记忆）
2. 将新信息归入合适分类
3. 去重：如果新信息与旧信息重复，保留更详细的版本
4. 修正：如果新信息与旧信息矛盾，以新信息为准
5. 简洁：每条记忆一行，以 "- " 开头
6. 不要添加对话语气，只输出纯记忆内容
7. 保持 Markdown 格式

当前长期记忆：
{current_memory}

待整合记忆：
{pending}

请输出更新后的完整长期记忆（直接输出 Markdown，不要包裹代码块）："""

        checkpoint_id = None
        try:
            if self._checkpoint_manager:
                checkpoint_id = self._checkpoint_manager.create(
                    task_type="memory_consolidation",
                    task_id=f"consolidation_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    current_goal="整合 PENDING.md 到 MEMORY.md",
                    completed=[],
                    next_step="调用 LLM 生成整合结果",
                    metadata={
                        "pending_length": len(pending),
                        "memory_length": len(current_memory),
                    },
                    trigger="consolidation_start"
                )["checkpoint_id"]
                log.info("[memory] 创建整合检查点: %s", checkpoint_id)

            result = await llm_call(prompt)
            result = result.strip()

            if self._checkpoint_manager:
                self._checkpoint_manager.create(
                    task_type="memory_consolidation",
                    task_id=f"consolidation_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    current_goal="整合 PENDING.md 到 MEMORY.md",
                    completed=["LLM 调用完成"],
                    next_step="写入 MEMORY.md",
                    metadata={
                        "result_length": len(result),
                    },
                    trigger="llm_completed"
                )

            if result.startswith("```"):
                lines = result.splitlines()
                result = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

            if not result or len(result) < 10:
                log.warning("[memory] 整合结果为空，跳过")
                if self._checkpoint_manager and checkpoint_id:
                    self._checkpoint_manager.create(
                        task_type="memory_consolidation",
                        task_id=f"consolidation_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                        current_goal="整合 PENDING.md 到 MEMORY.md",
                        completed=[],
                        blocker="整合结果为空",
                        metadata={},
                        trigger="consolidation_failed"
                    )
                return False

            self.update_memory_md(result)

            self.append_history(f"consolidation: 整合了 PENDING 到 MEMORY.md")

            self.clear_pending()

            self.reset_conversation_counter()

            if self._checkpoint_manager and checkpoint_id:
                self._checkpoint_manager.mark_completed(checkpoint_id, "记忆整合完成")
                log.info("[memory] 整合检查点已完成: %s", checkpoint_id)

            log.info("[memory] 记忆整合完成，PENDING 已清空")
            return True

        except Exception as e:
            log.error(f"[memory] 记忆整合失败: {e}")
            if self._checkpoint_manager and checkpoint_id:
                self._checkpoint_manager.create(
                    task_type="memory_consolidation",
                    task_id=f"consolidation_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    current_goal="整合 PENDING.md 到 MEMORY.md",
                    completed=[],
                    next_step="重试整合",
                    blocker=str(e),
                    metadata={
                        "error": str(e),
                    },
                    trigger="consolidation_failed"
                )
                log.warning("[memory] 整合失败已记录到检查点")
            return False
