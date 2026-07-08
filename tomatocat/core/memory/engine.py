from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal, Protocol, runtime_checkable

MemoryQueryIntent = Literal["context", "answer", "timeline", "interest", "procedure"]
MemoryQueryEffect = Literal["stateful", "read_only"]


@dataclass(frozen=True)
class MemoryScope:
    session_key: str = ""
    channel: str = ""
    chat_id: str = ""


@dataclass(frozen=True)
class MemoryEngineDescriptor:
    name: str
    capabilities: frozenset[str]
    notes: dict[str, object] = field(default_factory=dict)


@dataclass
class MemoryIngestRequest:
    content: object
    source_kind: str
    scope: MemoryScope = field(default_factory=MemoryScope)
    hints: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class MemoryIngestResult:
    accepted: bool
    created_ids: list[str] = field(default_factory=list)
    summary: str = ""
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryQueryFilters:
    kinds: tuple[str, ...] = ()
    time_start: datetime | None = None
    time_end: datetime | None = None
    hints: MappingProxyType[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass
class MemoryQuery:
    text: str
    intent: MemoryQueryIntent = "answer"
    effect: MemoryQueryEffect = "stateful"
    scope: MemoryScope = field(default_factory=MemoryScope)
    filters: MemoryQueryFilters = field(default_factory=MemoryQueryFilters)
    context: dict[str, object] = field(default_factory=dict)
    limit: int = 8
    timestamp: datetime | None = None


@dataclass
class MemoryRecord:
    id: str
    kind: str
    summary: str
    score: float
    engine_kind: str
    evidence: list[dict] = field(default_factory=list)
    signals: dict[str, object] = field(default_factory=dict)
    injected: bool = False


@dataclass
class MemoryQueryResult:
    text_block: str = ""
    records: list[MemoryRecord] = field(default_factory=list)
    trace: dict[str, object] = field(default_factory=dict)
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryMutation:
    kind: Literal["remember", "forget"]
    scope: MemoryScope = field(default_factory=MemoryScope)
    summary: str = ""
    memory_kind: str = ""
    source_ref: str = ""
    ids: tuple[str, ...] = ()
    metadata: MappingProxyType[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass
class MemoryMutationResult:
    accepted: bool
    item_id: str = ""
    actual_kind: str = ""
    status: str = ""
    affected_ids: list[str] = field(default_factory=list)
    missing_ids: list[str] = field(default_factory=list)
    items: list[dict[str, object]] = field(default_factory=list)
    raw: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class MemoryIngestApi(Protocol):
    async def ingest(self, request: MemoryIngestRequest) -> MemoryIngestResult: ...


@runtime_checkable
class MemoryRetrievalApi(Protocol):
    async def query(self, request: MemoryQuery) -> MemoryQueryResult: ...


@runtime_checkable
class MemoryWriteApi(Protocol):
    async def mutate(self, request: MemoryMutation) -> MemoryMutationResult: ...

    def reinforce_items_batch(self, ids: list[str]) -> None: ...


@runtime_checkable
class MemoryAdminApi(Protocol):
    def describe(self) -> MemoryEngineDescriptor: ...

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
    ) -> tuple[list[dict[str, object]], int]: ...

    def delete_item(self, item_id: str) -> bool: ...

    async def consolidate(self) -> bool: ...


@runtime_checkable
class MemoryEngine(
    MemoryIngestApi,
    MemoryRetrievalApi,
    MemoryWriteApi,
    MemoryAdminApi,
    Protocol,
):
    pass