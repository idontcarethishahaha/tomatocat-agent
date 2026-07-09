from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from contextlib import suppress
from typing import Protocol, cast, runtime_checkable

from tomatocat.bus import TurnCommitted, MemoryWritten, RetrievalCompleted
from tomatocat.plugins import Plugin

logger = logging.getLogger("plugin.observe")


@runtime_checkable
class _ObserveWriter(Protocol):
    def emit(self, event: object) -> None: ...


class ObservePlugin(Plugin):
    name = "observe"

    def __init__(self) -> None:
        self._writer = None
        self._writer_task = None
        self._retention_task = None
        self._collector = None

    async def initialize(self) -> None:
        workspace = self.context.workspace
        if workspace is None:
            logger.warning("observe 插件缺少 workspace，跳过加载")
            return

        from plugins.observe.collector import GlobalErrorCollector
        from plugins.observe.retention import run_retention_if_needed
        from plugins.observe.writer import TraceWriter

        self._writer = TraceWriter(workspace / "observe" / "observe.db")
        self._writer_task = asyncio.create_task(
            self._writer.run(),
            name="observe_writer",
        )
        self._retention_task = asyncio.create_task(
            run_retention_if_needed(workspace / "observe" / "observe.db"),
            name="observe_retention",
        )
        self._collector = GlobalErrorCollector(self._writer)
        self._collector.install()
        self.context.event_bus.on(TurnCommitted, self._observe_turn_committed)
        self.context.event_bus.on(RetrievalCompleted, self._observe_retrieval)
        self.context.event_bus.on(MemoryWritten, self._observe_memory_written)

    async def terminate(self) -> None:
        collector = getattr(self, "_collector", None)
        if collector is not None:
            await collector.uninstall()
        for task in (
            getattr(self, "_retention_task", None),
            getattr(self, "_writer_task", None),
        ):
            if task is None:
                continue
            _ = task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def _observe_turn_committed(self, event: TurnCommitted) -> None:
        writer = getattr(self, "_writer", None)
        if not isinstance(writer, _ObserveWriter):
            return
        _emit_turn_trace(writer, event)

    def _observe_retrieval(self, event: RetrievalCompleted) -> None:
        writer = getattr(self, "_writer", None)
        if not isinstance(writer, _ObserveWriter):
            return
        writer.emit(_to_rag_query_log(event))

    def _observe_memory_written(self, event: MemoryWritten) -> None:
        writer = getattr(self, "_writer", None)
        if not isinstance(writer, _ObserveWriter):
            return
        writer.emit(_to_memory_write_trace(event))


def _emit_turn_trace(writer: _ObserveWriter, event: TurnCommitted) -> None:
    from plugins.observe.events import TurnTrace as TurnTraceEvent

    writer.emit(
        TurnTraceEvent(
            source="agent",
            session_key=event.session_key,
            user_msg=event.input_message,
            llm_output=event.assistant_response,
            raw_llm_output=None,
            meme_tag=event.meme_tag,
            meme_media_count=None,
            tool_calls=[
                {"name": tool_name, "args": "", "result": ""}
                for tool_name in event.tools_used
            ],
            tool_chain_json=None,
            history_window=None,
            history_messages=None,
            history_chars=None,
            history_tokens=None,
            prompt_tokens=None,
            next_turn_baseline_tokens=None,
            react_iteration_count=None,
            react_input_sum_tokens=None,
            react_input_peak_tokens=None,
            react_final_input_tokens=None,
            react_cache_prompt_tokens=None,
            react_cache_hit_tokens=None,
        )
    )
    logger.info(
        "[observe] turn_trace 已入队 session=%s tool_calls=%d",
        event.session_key,
        len(event.tools_used),
    )


def _to_rag_query_log(event: RetrievalCompleted):
    from plugins.observe.events import RagHitLog, RagQueryLog

    return RagQueryLog(
        caller="passive",
        session_key=event.session_key,
        query=event.query,
        orig_query=event.orig_query,
        aux_queries=list(event.aux_queries),
        hits=[
            RagHitLog(
                item_id=hit.get("id", ""),
                memory_type=hit.get("category", "general"),
                score=hit.get("similarity", 0.0),
                summary=hit.get("content", "")[:120],
                injected=True,
                confidence_label="",
                forced=False,
            )
            for hit in event.hits
        ],
        injected_count=event.injected_count,
        route_decision=event.route_decision,
        error=event.error,
    )


def _to_memory_write_trace(event: MemoryWritten):
    from plugins.observe.events import MemoryWriteTrace

    return MemoryWriteTrace(
        session_key=event.session_key,
        source_ref=event.source_ref,
        action=event.action,
        memory_type=event.memory_type,
        item_id=event.item_id,
        summary=event.summary,
        superseded_ids=list(event.superseded_ids),
        error=event.error,
    )