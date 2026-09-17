import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tomatocat.core.memory.engine import MemoryQuery, MemoryScope
from tomatocat.memory import MemoryEngine as FileMemoryEngine
from tomatocat.memory2.retriever import Retriever
from tomatocat.memory2.store import VectorMemoryStore
from tomatocat.plugins.default_memory.engine import DefaultMemoryEngine


class StaticEmbedder:
    async def embed(self, _text):
        return [1.0, 0.0]


def test_store_migrates_legacy_schema(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE memory_items (
                id TEXT PRIMARY KEY, memory_type TEXT NOT NULL, summary TEXT NOT NULL,
                content_hash TEXT NOT NULL, reinforcement INTEGER NOT NULL DEFAULT 1,
                emotional_weight INTEGER NOT NULL DEFAULT 0, extra_json TEXT,
                source_ref TEXT, happened_at TEXT, status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX ux_items_hash ON memory_items (content_hash, memory_type);
            CREATE TABLE memory_embeddings (
                item_id TEXT PRIMARY KEY, embedding BLOB NOT NULL, dims INTEGER NOT NULL
            );
        """)

    store = VectorMemoryStore(db_path, vec_dim=2)
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_items)")}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(memory_items)")}
    assert {"session_key", "source_kind", "confidence", "source_trust"} <= columns
    assert "ux_items_hash" not in indexes
    assert "ux_items_hash_scope" in indexes
    store.close()


def test_store_isolates_same_memory_between_sessions(tmp_path):
    store = VectorMemoryStore(tmp_path / "memory.db", vec_dim=2)

    async def seed():
        first = await store.add(
            "preference", "likes tea", embedding=[1.0, 0.0], session_key="telegram:1",
        )
        second = await store.add(
            "preference", "likes tea", embedding=[1.0, 0.0], session_key="telegram:2",
        )
        return first, second

    first, second = asyncio.run(seed())
    assert first.id != second.id
    hits = store.search_by_keywords(["likes tea"], session_key="telegram:1")
    assert [hit.item.session_key for hit in hits] == ["telegram:1"]
    assert not store.delete(first.id, session_key="telegram:2")
    assert store.delete(first.id, session_key="telegram:1")
    store.close()


def test_retrieval_applies_confidence_trust_and_time_decay(tmp_path):
    store = VectorMemoryStore(tmp_path / "memory.db", vec_dim=2)
    old_time = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    async def seed():
        await store.add(
            "event", "older reliable event", embedding=[1.0, 0.0],
            happened_at=old_time, confidence=1.0, source_trust=1.0,
        )
        await store.add(
            "event", "newer uncertain event", embedding=[1.0, 0.0],
            happened_at=now, confidence=0.4, source_trust=0.5,
        )

    asyncio.run(seed())
    hits = store.search_by_embedding([1.0, 0.0], min_score=0.0)
    scores = {hit.item.summary: hit.score for hit in hits}
    assert scores["older reliable event"] == pytest.approx(0.5)
    assert scores["newer uncertain event"] == pytest.approx(0.2)
    store.close()


def test_automatic_extraction_is_written_to_scoped_sqlite(tmp_path):
    class FileMemory:
        async def extract_and_pending(self, *_args):
            return "- 用户喜欢绿茶\n- 用户完成了毕业设计"

    engine = DefaultMemoryEngine.__new__(DefaultMemoryEngine)
    engine._file_memory = FileMemory()
    engine._vec_store = VectorMemoryStore(tmp_path / "memory.db", vec_dim=2)
    engine._embedder = StaticEmbedder()
    engine._retriever = Retriever(engine._vec_store, engine._embedder)
    engine._event_bus = None
    scope = MemoryScope(session_key="telegram:42", channel="telegram", chat_id="42")

    asyncio.run(engine.extract_and_pending("hello", "hi", lambda _p: None, scope=scope))
    result = asyncio.run(engine.query(MemoryQuery(text="绿茶", scope=scope)))
    other = asyncio.run(engine.query(MemoryQuery(
        text="绿茶",
        scope=MemoryScope(session_key="telegram:99"),
    )))

    assert result.trace == {"hit_count": 1}
    assert result.records[0].kind == "preference"
    assert other.records == []
    items, total = engine.list_items_for_dashboard(page_size=10)
    assert total == 2
    assert {item["source_kind"] for item in items} == {"conversation_extract"}
    engine._vec_store.close()


def test_ingest_persists_without_embedding_service(tmp_path):
    from tomatocat.core.memory.engine import MemoryIngestRequest

    engine = DefaultMemoryEngine.__new__(DefaultMemoryEngine)
    engine._vec_store = VectorMemoryStore(tmp_path / "memory.db", vec_dim=2)
    engine._embedder = None
    engine._event_bus = None
    result = asyncio.run(engine.ingest(MemoryIngestRequest(
        content="remember this",
        source_kind="manual",
        scope=MemoryScope(session_key="cli:1"),
    )))
    assert result.accepted
    assert engine._vec_store.count() == 1
    engine._vec_store.close()


def test_memory_markdown_versions_can_be_restored(tmp_path):
    memory = FileMemoryEngine(tmp_path, vector_enabled=False)
    original = memory.get_memory_md()
    memory.update_memory_md("# updated\n")
    versions = memory.list_memory_versions()

    assert len(versions) == 1
    assert memory.restore_memory_version(versions[0])
    assert memory.get_memory_md() == original
    assert not memory.restore_memory_version("../MEMORY.md")


def test_failed_extraction_response_is_not_persisted(tmp_path):
    memory = FileMemoryEngine(tmp_path, vector_enabled=False)

    async def scenario():
        return await memory.extract_and_pending(
            "hello",
            "hi",
            lambda _prompt: _return_text("调用失败: service unavailable"),
        )

    assert asyncio.run(scenario()) is None
    assert "service unavailable" not in memory.get_pending()


async def _return_text(text):
    return text
