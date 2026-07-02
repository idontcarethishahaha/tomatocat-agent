"""向量记忆插件 - memory2

基于 embedding 的语义记忆系统。
- 自动从对话中提取关键信息存入向量记忆
- 支持语义检索（相似度匹配）+ 关键词匹配
- 记忆类型：preference（偏好）/ event（事件）/ procedure（流程）/ profile（画像）

依赖：
- numpy（必选，做向量计算）
- httpx（必选，调用 embedding API）

配置 config.toml:
[memory2]
enabled = true
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key = "your-api-key"
model = "text-embedding-v3"
vec_dim = 1024
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from tomatocat.plugins import Plugin, tool

log = logging.getLogger(__name__)

_EMBEDDER = None
_STORE = None
_ENABLED = False


class Memory2Plugin(Plugin):
    name = "memory2"
    desc = "向量记忆系统（语义检索）"

    def __init__(self) -> None:
        super().__init__()
        self._embedder = None
        self._store = None
        self._enabled = False

    async def initialize(self) -> None:
        global _EMBEDDER, _STORE, _ENABLED

        # 从 config 读取配置
        config = self._get_config()
        if not config.get("enabled", False):
            log.info("[memory2] 未启用（enabled=false）")
            self._enabled = False
            _ENABLED = False
            return

        try:
            from tomatocat.memory2.embedder import Embedder
            from tomatocat.memory2.store import VectorMemoryStore

            self._embedder = Embedder(
                base_url=config.get("base_url", ""),
                api_key=config.get("api_key", ""),
                model=config.get("model", "text-embedding-v3"),
                output_dimensionality=config.get("vec_dim", 1024),
            )

            db_path = self.context.workspace / "memory2" / "memory2.db"
            self._store = VectorMemoryStore(
                db_path=db_path,
                vec_dim=config.get("vec_dim", 1024),
            )

            _EMBEDDER = self._embedder
            _STORE = self._store
            self._enabled = True
            _ENABLED = True

            count = self._store.count()
            log.info("[memory2] 已启用，共 %d 条向量记忆", count)
        except Exception as e:
            log.error("[memory2] 初始化失败: %s", e)
            self._enabled = False
            _ENABLED = False

    async def terminate(self) -> None:
        if self._embedder:
            try:
                await self._embedder.close()
            except Exception:
                pass
        if self._store:
            try:
                self._store.close()
            except Exception:
                pass

    def _get_config(self) -> dict[str, Any]:
        """从配置文件读取 memory2 配置"""
        try:
            import tomllib
            # 尝试从工作区根目录找 config.toml
            config_path = Path("D:/ai学习项目/akashic-agent-study/tomatocat-agent-v2/config.toml")
            if config_path.exists():
                with open(config_path, "rb") as f:
                    cfg = tomllib.load(f)
                return cfg.get("memory2", {})
        except Exception as e:
            log.warning("[memory2] 读取配置失败: %s", e)
        return {}

    # ── 工具 ──────────────────────────────────────────────────

    @tool(
        name="memory_search",
        description=(
            "在向量记忆中搜索相关记忆（语义检索）。\n"
            "根据语义相似度返回最相关的记忆条目。\n"
            "记忆类型：preference（用户偏好）、event（事件记录）、procedure（操作流程）、profile（人物画像）。\n"
            "当你需要回忆用户以前说过的偏好、发生过的事件、或学到的知识时使用此工具。"
        ),
        risk="read-only",
    )
    async def memory_search(
        self,
        event: object,
        query: str,
        memory_type: str = "",
        top_k: int = 5,
    ) -> str:
        """语义搜索记忆

        Args:
            query: 搜索查询文本
            memory_type: 限定记忆类型（preference/event/procedure/profile），空表示不限
            top_k: 返回前 N 条，默认 5
        """
        if not self._enabled or not self._store or not self._embedder:
            return "向量记忆未启用（请在 config.toml 中配置 [memory2]）"

        try:
            query_emb = await self._embedder.embed(query)
        except Exception as e:
            return f"embedding 失败: {e}"

        types = [memory_type] if memory_type else None
        hits = self._store.search(
            query_embedding=query_emb,
            query_text=query,
            memory_types=types,
            top_k=top_k,
        )

        if not hits:
            return "未找到相关记忆"

        lines = [f"找到 {len(hits)} 条相关记忆：", ""]
        for i, hit in enumerate(hits, 1):
            item = hit.item
            score_pct = f"{hit.score * 100:.0f}%"
            icon = _type_icon(item.memory_type)
            lines.append(
                f"{i}. {icon} [{item.memory_type}] {item.summary}  "
                f"(相似度: {score_pct}, 强化: {item.reinforcement})"
            )
            if item.extra:
                for k, v in item.extra.items():
                    val_str = str(v)[:80]
                    lines.append(f"   {k}: {val_str}")
            lines.append("")

        return "\n".join(lines)

    @tool(
        name="memory_add",
        description=(
            "向向量记忆中添加一条记忆。\n"
            "支持四种类型：\n"
            "- preference: 用户的偏好、习惯、喜欢/不喜欢的东西\n"
            "- event: 发生过的事件、对话中的重要信息\n"
            "- procedure: 操作流程、步骤、方法\n"
            "- profile: 人物画像、用户基本信息\n"
            "系统会自动去重，相同内容会强化计数而不是重复添加。"
        ),
        risk="read-write",
    )
    async def memory_add(
        self,
        event: object,
        memory_type: str,
        summary: str,
        detail: str = "",
    ) -> str:
        """添加记忆

        Args:
            memory_type: 记忆类型（preference/event/procedure/profile）
            summary: 记忆摘要（一句话概括，用于检索和显示）
            detail: 详细内容（可选，存到 extra.detail）
        """
        if not self._enabled or not self._store or not self._embedder:
            return "向量记忆未启用（请在 config.toml 中配置 [memory2]）"

        valid_types = {"preference", "event", "procedure", "profile"}
        if memory_type not in valid_types:
            return f"错误：memory_type 必须是 {valid_types} 之一"

        if not summary.strip():
            return "错误：summary 不能为空"

        try:
            full_text = summary + ("\n" + detail if detail else "")
            embedding = await self._embedder.embed(full_text)
        except Exception as e:
            return f"embedding 失败: {e}"

        extra = {}
        if detail:
            extra["detail"] = detail

        item = await self._store.add(
            memory_type=memory_type,
            summary=summary,
            embedding=embedding,
            extra=extra,
        )

        icon = _type_icon(memory_type)
        return (
            f"✅ 记忆已添加\n"
            f"类型: {icon} {memory_type}\n"
            f"ID: {item.id}\n"
            f"摘要: {item.summary}\n"
            f"强化计数: {item.reinforcement}"
        )

    @tool(
        name="memory_list",
        description=(
            "列出所有记忆条目。\n"
            "可按类型筛选，按强化次数和更新时间排序。"
        ),
        risk="read-only",
    )
    async def memory_list(
        self,
        event: object,
        memory_type: str = "",
        limit: int = 50,
    ) -> str:
        """列出记忆

        Args:
            memory_type: 筛选类型，空表示全部
            limit: 最多返回条数，默认 50
        """
        if not self._enabled or not self._store:
            return "向量记忆未启用"

        items = self._store.get_all(
            memory_type=memory_type or None,
            limit=limit,
        )

        if not items:
            return "暂无记忆"

        total = self._store.count()
        lines = [f"共 {total} 条记忆，显示前 {len(items)} 条：", ""]

        by_type: dict[str, list] = {}
        for item in items:
            by_type.setdefault(item.memory_type, []).append(item)

        for mtype, type_items in by_type.items():
            icon = _type_icon(mtype)
            lines.append(f"### {icon} {mtype} ({len(type_items)})")
            for item in type_items:
                lines.append(f"- {item.summary}  [强化×{item.reinforcement}]")
            lines.append("")

        return "\n".join(lines)

    @tool(
        name="memory_delete",
        description="删除一条记忆（按 ID）。",
        risk="read-write",
    )
    async def memory_delete(
        self,
        event: object,
        item_id: str,
    ) -> str:
        """删除记忆

        Args:
            item_id: 记忆 ID
        """
        if not self._enabled or not self._store:
            return "向量记忆未启用"

        self._store.delete(item_id)
        return f"✅ 记忆 {item_id} 已删除"

    # ── 对外 API（供其他模块调用） ─────────────────────────────

    def get_store(self) -> Any:
        return self._store

    def get_embedder(self) -> Any:
        return self._embedder


def _type_icon(memory_type: str) -> str:
    return {
        "preference": "💖",
        "event": "📅",
        "procedure": "📋",
        "profile": "👤",
    }.get(memory_type, "💭")
