from __future__ import annotations

import logging
import re
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
    MemoryScope,
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

    def __init__(self, workspace: Path, config: Any, llm_provider: Any, event_bus: Any = None) -> None:
        self._workspace = workspace
        self._config = config
        self._llm_provider = llm_provider
        self._event_bus = event_bus
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
        try:
            content = str(request.content)
            summary = content[:100]
            embedding = None
            embedding_error = ""
            if self._embedder:
                try:
                    embedding = await self._embedder.embed(content)
                except Exception as exc:
                    embedding_error = str(exc)
                    logger.warning("[memory] embedding 失败，先保存结构化记忆: %s", exc)
            memory_type = request.hints.get("memory_type", "general")
            confidence = _clamp_score(request.hints.get("confidence", 1.0))
            source_trust = _clamp_score(
                request.hints.get("source_trust", _source_trust(request.source_kind))
            )

            item = await self._vec_store.add(
                memory_type=str(memory_type),
                summary=summary,
                embedding=embedding,
                extra=dict(request.metadata),
                source_ref=request.hints.get("source_ref", ""),
                happened_at=request.hints.get("happened_at") or datetime.now().astimezone().isoformat(),
                session_key=request.scope.session_key,
                source_kind=request.source_kind,
                confidence=confidence,
                source_trust=source_trust,
            )

            if self._event_bus:
                from tomatocat.bus import MemoryWritten
                self._event_bus.enqueue(
                    MemoryWritten(
                        session_key=request.scope.session_key,
                        source_ref=request.hints.get("source_ref", "ingest"),
                        action="write",
                        memory_type=str(memory_type),
                        item_id=item.id,
                        summary=summary,
                    )
                )

            return MemoryIngestResult(
                accepted=True,
                created_ids=[item.id],
                summary=summary,
                raw={"embedding_error": embedding_error} if embedding_error else {},
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
                session_key=request.scope.session_key,
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

            if self._event_bus:
                from tomatocat.bus import RetrievalCompleted
                self._event_bus.enqueue(
                    RetrievalCompleted(
                        session_key=request.scope.session_key,
                        query=request.text,
                        hits=[
                            {
                                "id": hit.item.id,
                                "category": hit.item.memory_type,
                                "similarity": hit.score,
                                "content": hit.item.summary,
                            }
                            for hit in hits
                        ],
                        injected_count=len(hits),
                    )
                )

            return MemoryQueryResult(
                text_block=inject_block,
                records=records,
                trace={"hit_count": len(hits)},
            )
        except Exception as e:
            logger.error("[memory] query 失败: %s", e)
            if self._event_bus:
                from tomatocat.bus import RetrievalCompleted
                self._event_bus.enqueue(
                    RetrievalCompleted(
                        session_key=request.scope.session_key,
                        query=request.text,
                        hits=[],
                        error=str(e),
                    )
                )
            return MemoryQueryResult(trace={"error": str(e)})

    async def mutate(self, request: MemoryMutation) -> MemoryMutationResult:
        if request.kind == "remember":
            ingest_result = await self.ingest(MemoryIngestRequest(
                content=request.summary,
                source_kind="manual",
                scope=request.scope,
                hints={"memory_type": request.memory_kind, "source_ref": request.source_ref},
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
            deleted_ids = []
            for item_id in request.ids:
                if self._vec_store.delete(
                    item_id,
                    session_key=request.scope.session_key or None,
                ):
                    deleted_ids.append(item_id)
            return MemoryMutationResult(
                accepted=bool(deleted_ids),
                affected_ids=deleted_ids,
                missing_ids=[item_id for item_id in request.ids if item_id not in deleted_ids],
                status="deleted" if deleted_ids else "not_found",
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
        session_key: str | None = None,
        page: int = 1,
        page_size: int = 50,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> tuple[list[dict[str, object]], int]:
        items = self._vec_store.list_items(
            query=q,
            memory_type=memory_type,
            status=status,
            session_key=session_key,
            page=page,
            page_size=page_size,
        )
        total = self._vec_store.count(
            memory_type or None,
            status=status,
            query=q,
            session_key=session_key,
        )
        return items, total

    def delete_item(self, item_id: str) -> bool:
        return self._vec_store.delete(item_id)

    def list_memory_versions(self) -> list[str]:
        return self._file_memory.list_memory_versions()

    def restore_memory_version(self, version_name: str) -> bool:
        return self._file_memory.restore_memory_version(version_name)

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
        scope: MemoryScope | None = None,
    ) -> str | None:
        extracted = await self._file_memory.extract_and_pending(
            user_text,
            assistant_text,
            llm_call,
        )
        if not extracted:
            return None

        active_scope = scope or MemoryScope()
        for summary in _parse_extracted_memories(extracted):
            await self.ingest(MemoryIngestRequest(
                content=summary,
                source_kind="conversation_extract",
                scope=active_scope,
                hints={
                    "memory_type": _infer_memory_type(summary),
                    "source_ref": "post_conversation",
                    "confidence": 0.75,
                    "source_trust": 0.8,
                },
                metadata={
                    "channel": active_scope.channel,
                    "chat_id": active_scope.chat_id,
                },
            ))
        return extracted

    def tick_conversation(self) -> bool:
        return self._file_memory.tick_conversation()

    def reset_conversation_counter(self) -> None:
        self._file_memory.reset_conversation_counter()

    async def close(self) -> None:
        self._vec_store.close()
        if self._embedder is not None:
            await self._embedder.close()


def _clamp_score(value: object) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 1.0


def _source_trust(source_kind: str) -> float:
    return {
        "manual": 1.0,
        "conversation_extract": 0.8,
        "import": 0.7,
    }.get(source_kind, 0.75)


def _parse_extracted_memories(text: str) -> list[str]:
    memories = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(("- ", "* ")):
            line = line[2:].strip()
        else:
            line = re.sub(r"^\d+[.、)]\s*", "", line)
        if line and line != "无" and not line.startswith("#"):
            memories.append(line)
    return list(dict.fromkeys(memories))


def _infer_memory_type(summary: str) -> str:
    lowered = summary.lower()
    if any(token in lowered for token in ("喜欢", "偏好", "习惯", "不喜欢", "prefer", "like")):
        return "preference"
    if any(token in lowered for token in ("步骤", "流程", "方法", "procedure")):
        return "procedure"
    if any(token in lowered for token in ("发生", "去了", "完成", "event", "visited")):
        return "event"
    return "profile"
