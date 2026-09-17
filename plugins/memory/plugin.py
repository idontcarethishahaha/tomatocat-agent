"""记忆工具插件 - 让 AI 可以主动记忆、搜索和遗忘"""

from __future__ import annotations

from tomatocat.plugins import Plugin, tool
from tomatocat.core.memory.engine import MemoryMutation, MemoryQuery, MemoryScope


class MemoryPlugin(Plugin):
    name = "memory"
    desc = "番茄猫记忆管理工具"

    def __init__(self) -> None:
        super().__init__()

    def _get_memory(self):
        if self.context and self.context.memory:
            return self.context.memory
        return None

    @tool(name="memorize", description="记住一条信息到长期记忆中")
    async def memorize(
        self,
        event: object,
        content: str,
        category: str = "general",
        _session_key: str = "",
        _channel: str = "",
    ) -> str:
        """
        记住一条信息

        Args:
            content: 要记住的内容
            category: 记忆分类（general/fact/preference/event）
        """
        memory = self._get_memory()
        if not memory:
            return "记忆系统未就绪"

        try:
            result = await memory.mutate(MemoryMutation(
                kind="remember",
                scope=_memory_scope(_session_key, _channel),
                summary=content,
                memory_kind=category,
                source_ref="memory_tool",
            ))
            if not result.accepted:
                return "记忆失败: 记忆引擎拒绝了写入"
            return f"已记住喵~ [{category}] {content[:50]}"
        except Exception as e:
            return f"记忆失败: {e}"

    @tool(name="recall_memory", description="搜索记忆，找到和关键词相关的内容")
    async def recall_memory(
        self,
        event: object,
        query: str,
        top_k: int = 5,
        _session_key: str = "",
        _channel: str = "",
    ) -> str:
        """
        搜索记忆

        Args:
            query: 搜索关键词
            top_k: 返回最多几条结果
        """
        memory = self._get_memory()
        if not memory:
            return "记忆系统未就绪"

        try:
            result = await memory.query(MemoryQuery(
                text=query,
                intent="answer",
                limit=top_k,
                scope=_memory_scope(_session_key, _channel),
            ))
            if not result.records:
                return f"没有找到和 '{query}' 相关的记忆"

            lines = [f"找到 {len(result.records)} 条相关记忆："]
            for i, record in enumerate(result.records, 1):
                lines.append(f"{i}. [{record.kind}] {record.summary[:80]} (相似度: {record.score:.2f})")
            return "\n".join(lines)
        except Exception as e:
            return f"搜索失败: {e}"

    @tool(name="forget_memory", description="删除一条记忆")
    async def forget_memory(
        self,
        event: object,
        memory_id: str,
        _session_key: str = "",
        _channel: str = "",
    ) -> str:
        """
        删除指定 ID 的记忆

        Args:
            memory_id: 记忆ID
        """
        memory = self._get_memory()
        if not memory:
            return "记忆系统未就绪"

        result = await memory.mutate(MemoryMutation(
            kind="forget",
            ids=(memory_id,),
            scope=_memory_scope(_session_key, _channel),
        ))
        if result.accepted and memory_id in result.affected_ids:
            return f"已遗忘记忆 {memory_id[:12]}..."
        return f"没有找到ID为 {memory_id[:12]}... 的记忆"

    @tool(name="get_memory_summary", description="查看当前记忆概览")
    async def get_memory_summary(self, event: object) -> str:
        """查看记忆概览"""
        memory = self._get_memory()
        if not memory:
            return "记忆系统未就绪"

        recent, _ = memory.list_items_for_dashboard(page=1, page_size=5)
        memory_md = memory.get_context_block()

        lines = ["📝 记忆概览："]
        lines.append(f"\n长期记忆 (MEMORY.md):\n{memory_md[:300]}")

        if recent:
            lines.append(f"\n最近 {len(recent)} 条向量记忆：")
            for r in recent:
                lines.append(f"  - [{r['memory_type']}] {r['summary'][:50]}")
        else:
            lines.append("\n暂无向量记忆")

        return "\n".join(lines)


def _memory_scope(session_key: str, channel: str) -> MemoryScope:
    chat_id = session_key.split(":", 1)[1] if ":" in session_key else session_key
    return MemoryScope(session_key=session_key, channel=channel, chat_id=chat_id)
