"""向量记忆检索器：语义检索 + 关键词匹配 + RRF 混合排序"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from tomatocat.memory2.store import MemoryItem, MemoryHit, VectorMemoryStore
from tomatocat.memory2.embedder import Embedder

logger = logging.getLogger(__name__)

_RRF_K = 60
_EMBED_TIMEOUT_S = 8.0


class Retriever:
    INJECT_MAX_CHARS = 1200
    INJECT_MAX_FORCED = 3
    INJECT_MAX_EVENTS = 4

    def __init__(
        self,
        store: VectorMemoryStore,
        embedder: Embedder,
        top_k: int = 8,
        score_threshold: float = 0.45,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._top_k = top_k
        self._score_threshold = score_threshold

    async def query(
        self,
        text: str,
        *,
        intent: str = "answer",
        limit: int = 8,
        session_key: str = "",
    ) -> list[MemoryHit]:
        tasks = [
            self._semantic_search(text, session_key=session_key),
            self._keyword_search(text, session_key=session_key),
        ]
        semantic_hits, keyword_hits = await asyncio.gather(*tasks)

        combined = self._merge_with_rrf(semantic_hits, keyword_hits)
        filtered = [hit for hit in combined if hit.score >= self._score_threshold]

        return filtered[:limit]

    async def _semantic_search(self, text: str, *, session_key: str) -> list[MemoryHit]:
        try:
            query_embedding = await asyncio.wait_for(
                self._embedder.embed(text),
                timeout=_EMBED_TIMEOUT_S,
            )
            return await asyncio.to_thread(
                self._store.search_by_embedding,
                query_embedding,
                top_k=self._top_k,
                session_key=session_key,
            )
        except asyncio.TimeoutError:
            logger.warning("[retriever] embedding 超时")
            return []
        except Exception as e:
            logger.error("[retriever] 语义检索失败: %s", e)
            return []

    async def _keyword_search(self, text: str, *, session_key: str) -> list[MemoryHit]:
        try:
            tokens = _extract_keywords(text)
            if not tokens:
                return []
            return await asyncio.to_thread(
                self._store.search_by_keywords,
                tokens,
                top_k=self._top_k,
                session_key=session_key,
            )
        except Exception as e:
            logger.error("[retriever] 关键词检索失败: %s", e)
            return []

    def _merge_with_rrf(
        self,
        semantic_hits: list[MemoryHit],
        keyword_hits: list[MemoryHit],
    ) -> list[MemoryHit]:
        score_map: dict[str, float] = {}
        relevance_map: dict[str, float] = {}
        item_map: dict[str, MemoryItem] = {}
        match_type_map: dict[str, str] = {}
        active_weight = (0.7 if semantic_hits else 0.0) + (0.3 if keyword_hits else 0.0)
        if active_weight == 0:
            return []

        for rank, hit in enumerate(semantic_hits, start=1):
            score = 1.0 / (_RRF_K + rank)
            score_map[hit.item.id] = score_map.get(hit.item.id, 0.0) + score * 0.7
            relevance_map[hit.item.id] = relevance_map.get(hit.item.id, 0.0) + hit.score * 0.7
            item_map[hit.item.id] = hit.item
            match_type_map[hit.item.id] = "semantic"

        for rank, hit in enumerate(keyword_hits, start=1):
            score = 1.0 / (_RRF_K + rank)
            score_map[hit.item.id] = score_map.get(hit.item.id, 0.0) + score * 0.3
            relevance_map[hit.item.id] = relevance_map.get(hit.item.id, 0.0) + hit.score * 0.3
            item_map[hit.item.id] = hit.item
            match_type_map[hit.item.id] = (
                "hybrid" if hit.item.id in match_type_map else "keyword"
            )

        results = []
        normalization = (_RRF_K + 1) / active_weight
        for item_id, score in score_map.items():
            relevance = relevance_map[item_id] / active_weight
            results.append(MemoryHit(
                item=item_map[item_id],
                score=min(score * normalization * relevance, 1.0),
                match_type=match_type_map[item_id],
            ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def build_inject_block(
        self,
        hits: list[MemoryHit],
        *,
        max_chars: int = INJECT_MAX_CHARS,
    ) -> str:
        if not hits:
            return ""

        sections: dict[str, list[str]] = defaultdict(list)
        for hit in hits:
            section = _memory_type_section(hit.item.memory_type)
            sections[section].append(f"- [{hit.item.id}] {hit.item.summary}")

        lines = []
        for section, items in sections.items():
            lines.append(f"## {section}")
            lines.extend(items)

        block = "\n".join(lines)
        if len(block) > max_chars:
            block = block[:max_chars] + "..."

        return block


def _extract_keywords(text: str) -> list[str]:
    text = text.lower()
    tokens = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]+", text)
    tokens = [t for t in tokens if len(t) >= 2]
    return list(dict.fromkeys(tokens))


def _memory_type_section(memory_type: str) -> str:
    mapping = {
        "profile": "用户画像",
        "preference": "用户偏好",
        "event": "事件记录",
        "procedure": "操作流程",
    }
    return mapping.get(memory_type, memory_type)
