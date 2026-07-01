"""记忆工具插件 - 让 AI 可以主动记忆、搜索和遗忘"""

from __future__ import annotations

from tomatocat.plugins import Plugin, tool


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
            item_id = await memory.add_memory(content, category)
            return f"已记住喵~ [{category}] {content[:50]}"
        except Exception as e:
            return f"记忆失败: {e}"

    @tool(name="recall_memory", description="搜索记忆，找到和关键词相关的内容")
    async def recall_memory(
        self,
        event: object,
        query: str,
        top_k: int = 5,
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
            results = await memory.search(query, top_k=top_k)
            if not results:
                return f"没有找到和 '{query}' 相关的记忆"

            lines = [f"找到 {len(results)} 条相关记忆："]
            for i, r in enumerate(results, 1):
                sim = r.get("similarity", 0)
                lines.append(f"{i}. [{r['category']}] {r['content'][:80]} (相似度: {sim:.2f})")
            return "\n".join(lines)
        except Exception as e:
            return f"搜索失败: {e}"

    @tool(name="forget_memory", description="删除一条记忆")
    async def forget_memory(
        self,
        event: object,
        memory_id: str,
    ) -> str:
        """
        删除指定 ID 的记忆

        Args:
            memory_id: 记忆ID
        """
        memory = self._get_memory()
        if not memory:
            return "记忆系统未就绪"

        ok = memory.remove_memory(memory_id)
        if ok:
            return f"已遗忘记忆 {memory_id[:12]}..."
        return f"没有找到ID为 {memory_id[:12]}... 的记忆"

    @tool(name="get_memory_summary", description="查看当前记忆概览")
    async def get_memory_summary(self, event: object) -> str:
        """查看记忆概览"""
        memory = self._get_memory()
        if not memory:
            return "记忆系统未就绪"

        recent = memory.get_recent(limit=5)
        memory_md = memory.get_memory_md()

        lines = ["📝 记忆概览："]
        lines.append(f"\n长期记忆 (MEMORY.md):\n{memory_md[:300]}")

        if recent:
            lines.append(f"\n最近 {len(recent)} 条向量记忆：")
            for r in recent:
                lines.append(f"  - [{r['category']}] {r['content'][:50]}")
        else:
            lines.append("\n暂无向量记忆")

        return "\n".join(lines)
