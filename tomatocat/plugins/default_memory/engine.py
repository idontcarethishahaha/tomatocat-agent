from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from tomatocat.core.memory.engine import (
    MemoryEngine,
    MemoryEngineDescriptor,
    MemoryIngestRequest,
    MemoryIngestResult,
    MemoryQuery,
    MemoryQueryResult,
    MemoryRecord,
    MemoryMutation,
    MemoryMutationResult,
)
from tomatocat.memory2.store import VectorMemoryStore, MemoryHit
from tomatocat.memory2.embedder import Embedder
from tomatocat.memory2.retriever import Retriever
from tomatocat.memory import MemoryEngine as FileMemoryEngine

logger = logging.getLogger("plugins.default_memory")


class DefaultMemoryEngine(MemoryEngine):
    DESCRIPTOR = MemoryEngineDescriptor(
        name="default",
        capabilities=frozenset([
            "ingest.text",
            "ingest.messages",
            "retrieve.semantic",
            "retrieve.context_block",
            "manage.update",
            "manage.delete",
        ]),
    )

    def __init__(self, workspace: Path, config: Any, llm_provider: Any) -> None:
        self._workspace = workspace
        self._config = config
        self._llm_provider = llm_provider
        self._init_stores()

    def _init_stores(self) -> None:
        db_path = self._workspace / "memory2" / "memory.db"
        self._vec_store = VectorMemoryStore(db_path, vec_dim=1024)

        if self._config.memory.vector_enabled and self._config.llm_embedding.model:
            self._embedder = Embedder(
                base_url=self._config.llm_embedding.base_url,
                api_key=self._config.llm_embedding.api_key,
                model=self._config.llm_embedding.model,
            )
            self._retriever = Retriever(self._vec_store, self._embedder)
            logger.info("[memory] 向量检索器已初始化")
        else:
            self._embedder = None
            self._retriever = None
            logger.info("[memory] 向量检索器未启用")

        self._file_memory = FileMemoryEngine(
            workspace=self._workspace,
            embedding=None,
            vector_enabled=False,
        )
        logger.info("[memory] 文件记忆引擎已初始化")

    async def ingest(self, request: MemoryIngestRequest) -> MemoryIngestResult:
        if not self._embedder:
            return MemoryIngestResult(accepted=False, raw={"reason": "embedding disabled"})

        try:
            content = str(request.content)
            summary = content[:100]

            embedding = await self._embedder.embed(content)
            memory_type = request.hints.get("memory_type", "general")

            item_id = await self._vec_store.add(
                memory_type=str(memory_type),
                summary=summary,
                embedding=embedding,
                extra=dict(request.metadata),
                source_ref=request.hints.get("source_ref", ""),
            )

            return MemoryIngestResult(
                accepted=True,
                created_ids=[item_id],
                summary=summary,
            )
        except Exception as e:
            logger.error("[memory] ingest 失败: %s", e)
            return MemoryIngestResult(accepted=False, raw={"error": str(e)})

    async def query(self, request: MemoryQuery) -> MemoryQueryResult:
        if not self._retriever:
            return MemoryQueryResult(trace={"mode": "disabled"})

        try:
            hits = await self._retriever.query(
                request.text,
                intent=request.intent,
                limit=request.limit,
            )

            records = []
            for hit in hits:
                records.append(MemoryRecord(
                    id=hit.item.id,
                    kind=hit.item.memory_type,
                    summary=hit.item.summary,
                    score=hit.score,
                    engine_kind="vector",
                    injected=True,
                ))

            inject_block = self._retriever.build_inject_block(hits)

            return MemoryQueryResult(
                text_block=inject_block,
                records=records,
                trace={"hit_count": len(hits)},
            )
        except Exception as e:
            logger.error("[memory] query 失败: %s", e)
            return MemoryQueryResult(trace={"error": str(e)})

    async def mutate(self, request: MemoryMutation) -> MemoryMutationResult:
        if request.kind == "remember":
            ingest_result = await self.ingest(MemoryIngestRequest(
                content=request.summary,
                source_kind="manual",
                hints={"memory_type": request.memory_kind},
                metadata=dict(request.metadata),
            ))
            if ingest_result.accepted:
                return MemoryMutationResult(
                    accepted=True,
                    item_id=ingest_result.created_ids[0] if ingest_result.created_ids else "",
                    actual_kind=request.memory_kind,
                )
            return MemoryMutationResult(accepted=False)

        elif request.kind == "forget":
            deleted_count = 0
            for item_id in request.ids:
                if self._vec_store.delete(item_id):
                    deleted_count += 1
            return MemoryMutationResult(
                accepted=True,
                affected_ids=list(request.ids),
                status="deleted",
            )

        return MemoryMutationResult(accepted=False)

    def reinforce_items_batch(self, ids: list[str]) -> None:
        for item_id in ids:
            self._vec_store.reinforce(item_id)

    def describe(self) -> MemoryEngineDescriptor:
        return self.DESCRIPTOR

    def list_items_for_dashboard(
        self,
        *,
        q: str = "",
        memory_type: str = "",
        status: str = "",
        page: int = 1,
        page_size: int = 50,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> tuple[list[dict[str, object]], int]:
        items = self._vec_store.list_items(
            query=q,
            memory_type=memory_type,
            status=status,
            page=page,
            page_size=page_size,
        )
        return items, len(items)

    def delete_item(self, item_id: str) -> bool:
        return self._vec_store.delete(item_id)

    async def consolidate(self) -> bool:
        try:
            result = await self._file_memory.consolidate(
                llm_call=self._llm_provider.simple_chat
            )
            return result
        except Exception as e:
            logger.error("[memory] 整合失败: %s", e)
            return False

    def get_context_block(self) -> str:
        return self._file_memory.get_context_block()

    def add_journal_entry(self, content: str, date: str | None = None) -> Path:
        return self._file_memory.add_journal_entry(content, date)

    async def extract_and_pending(
        self,
        user_text: str,
        assistant_text: str,
        llm_call,
    ) -> str | None:
        return await self._file_memory.extract_and_pending(user_text, assistant_text, llm_call)

    def tick_conversation(self) -> bool:
        return self._file_memory.tick_conversation()

    def reset_conversation_counter(self) -> None:
        self._file_memory.reset_conversation_counter()