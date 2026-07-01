"""网页搜索插件 - 基于 Exa MCP 公开端点，无需 API Key

参考 tomatocat 的 agent/tools/web_search.py 实现，
适配 v2 的 @tool 装饰器系统。
"""

from __future__ import annotations

import json
import logging

import httpx

from tomatocat.plugins import Plugin, tool

log = logging.getLogger(__name__)

_MCP_URL = "https://mcp.exa.ai/mcp"
_DEFAULT_NUM_RESULTS = 8


class WebSearchPlugin(Plugin):
    name = "web_search"
    desc = "网页搜索（基于 Exa MCP）"

    @tool(
        name="web_search",
        description=(
            "用关键词搜索互联网，返回最新的搜索结果（标题 + 摘要 + URL）。"
            "适合查询时效性信息：新闻、产品发布、价格、人物动态等。"
            "拿到 URL 后可用 web_fetch 获取完整内容。"
        ),
    )
    async def web_search(
        self,
        event: object,
        query: str,
        num_results: int = 8,
        livecrawl: str = "fallback",
        type: str = "auto",
    ) -> str:
        """用关键词搜索互联网

        Args:
            query: 搜索关键词
            num_results: 返回结果数量，默认 8，最大 20
            livecrawl: 实时抓取模式 fallback（缓存优先）/ preferred（优先实时）
            type: 搜索类型 auto（均衡）/ fast（快速）/ deep（深度）
        """
        num_results = min(max(1, int(num_results)), 20)

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "web_search_exa",
                "arguments": {
                    "query": query,
                    "numResults": num_results,
                    "livecrawl": livecrawl,
                    "type": type,
                },
            },
        }

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                response = await client.post(
                    _MCP_URL,
                    json=payload,
                    headers={
                        "accept": "application/json, text/event-stream",
                        "content-type": "application/json",
                    },
                )
                response.raise_for_status()
        except Exception as e:
            log.warning("[web_search] 搜索失败: %s", e)
            return json.dumps(
                {"error": f"搜索失败：{e}", "query": query}, ensure_ascii=False
            )

        # 解析 SSE 响应
        text = response.text
        for line in text.splitlines():
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    content = data.get("result", {}).get("content", [])
                    if content:
                        return json.dumps(
                            {"query": query, "result": content[0].get("text", "")},
                            ensure_ascii=False,
                        )
                except json.JSONDecodeError:
                    continue

        return json.dumps(
            {"query": query, "results": [], "count": 0}, ensure_ascii=False
        )
