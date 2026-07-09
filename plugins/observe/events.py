from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class RagHitLog:
    item_id: str
    memory_type: str
    score: float
    summary: str
    injected: bool
    confidence_label: str = ""
    forced: bool = False


@dataclass
class RagQueryLog:
    caller: str
    session_key: str
    query: str
    orig_query: str | None
    aux_queries: list[str]
    hits: list[RagHitLog]
    injected_count: int
    route_decision: str | None = None
    error: str | None = None


@dataclass
class TurnTrace:
    source: Literal["agent"]
    session_key: str
    user_msg: str | None
    llm_output: str
    raw_llm_output: str | None = None
    meme_tag: str | None = None
    meme_media_count: int | None = None
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None
    tool_chain_json: str | None = None
    history_window: int | None = None
    history_messages: int | None = None
    history_chars: int | None = None
    history_tokens: int | None = None
    prompt_tokens: int | None = None
    next_turn_baseline_tokens: int | None = None
    react_iteration_count: int | None = None
    react_input_sum_tokens: int | None = None
    react_input_peak_tokens: int | None = None
    react_final_input_tokens: int | None = None
    react_cache_prompt_tokens: int | None = None
    react_cache_hit_tokens: int | None = None


@dataclass
class GlobalErrorTrace:
    fingerprint: str
    bucket: str
    source: str
    logger_name: str
    error_type: str
    message: str
    traceback_text: str
    level: str
    first_ts: str
    last_ts: str
    count: int
    session_keys: list[str] = field(default_factory=list)


@dataclass
class MemoryWriteTrace:
    session_key: str
    source_ref: str
    action: str
    memory_type: str | None = None
    item_id: str | None = None
    summary: str | None = None
    superseded_ids: list[str] = field(default_factory=list)
    error: str | None = None