import asyncio

import pytest

from tomatocat.memory2.retriever import Retriever
from tomatocat.memory2.store import VectorMemoryStore
from tomatocat.core.memory.engine import MemoryQuery
from tomatocat.plugins.default_memory.engine import DefaultMemoryEngine


class StaticEmbedder:
    async def embed(self, _text):
        return [1.0, 0.0]


def test_store_supports_separate_semantic_and_keyword_search(tmp_path):
    store = VectorMemoryStore(tmp_path / "memory.db", vec_dim=2)

    async def seed():
        await store.add("preference", "likes green tea", embedding=[1.0, 0.0])
        await store.add("event", "visited Hangzhou", embedding=[0.0, 1.0])

    asyncio.run(seed())

    semantic = store.search_by_embedding([1.0, 0.0], top_k=1)
    keyword = store.search_by_keywords(["green", "tea"], top_k=1)

    assert semantic[0].item.summary == "likes green tea"
    assert semantic[0].match_type == "semantic"
    assert keyword[0].item.summary == "likes green tea"
    assert keyword[0].match_type == "keyword"
    store.close()


def test_retriever_normalizes_rrf_scores_above_threshold(tmp_path):
    store = VectorMemoryStore(tmp_path / "memory.db", vec_dim=2)
    asyncio.run(store.add(
        "preference",
        "likes green tea",
        embedding=[1.0, 0.0],
    ))
    retriever = Retriever(store, StaticEmbedder(), score_threshold=0.45)

    hits = asyncio.run(retriever.query("likes green tea"))

    assert len(hits) == 1
    assert hits[0].score > 0.9
    assert hits[0].match_type == "hybrid"
    store.close()


def test_retriever_falls_back_to_keywords_when_embedding_fails(tmp_path):
    class FailingEmbedder:
        async def embed(self, _text):
            raise RuntimeError("offline")

    store = VectorMemoryStore(tmp_path / "memory.db", vec_dim=2)
    asyncio.run(store.add(
        "preference",
        "likes green tea",
        embedding=[1.0, 0.0],
    ))
    retriever = Retriever(store, FailingEmbedder(), score_threshold=0.45)

    hits = asyncio.run(retriever.query("likes green tea"))

    assert len(hits) == 1
    assert hits[0].score == pytest.approx(0.8)
    assert hits[0].match_type == "keyword"
    store.close()


def test_default_engine_query_uses_retriever_results(tmp_path):
    store = VectorMemoryStore(tmp_path / "memory.db", vec_dim=2)
    asyncio.run(store.add(
        "preference",
        "likes green tea",
        embedding=[1.0, 0.0],
    ))
    engine = DefaultMemoryEngine.__new__(DefaultMemoryEngine)
    engine._retriever = Retriever(store, StaticEmbedder(), score_threshold=0.45)
    engine._event_bus = None

    result = asyncio.run(engine.query(MemoryQuery(text="likes green tea")))

    assert result.trace == {"hit_count": 1}
    assert result.records[0].summary == "likes green tea"
    assert "likes green tea" in result.text_block
    store.close()
