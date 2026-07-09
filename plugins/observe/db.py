"""SQLite 连接管理与 schema 初始化。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;

CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    session_key TEXT    NOT NULL,
    user_msg    TEXT,
    llm_output  TEXT    NOT NULL DEFAULT '',
    raw_llm_output TEXT,
    meme_tag    TEXT,
    meme_media_count INTEGER,
    tool_calls  TEXT,
    tool_chain_json TEXT,
    history_window INTEGER,
    history_messages INTEGER,
    history_chars INTEGER,
    history_tokens INTEGER,
    prompt_tokens INTEGER,
    next_turn_baseline_tokens INTEGER,
    react_iteration_count INTEGER,
    react_input_sum_tokens INTEGER,
    react_input_peak_tokens INTEGER,
    react_final_input_tokens INTEGER,
    react_cache_prompt_tokens INTEGER,
    react_cache_hit_tokens INTEGER,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS ix_turns_sk_ts  ON turns (session_key, ts);
CREATE INDEX IF NOT EXISTS ix_turns_source ON turns (source, ts);

CREATE TABLE IF NOT EXISTS rag_queries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT    NOT NULL,
    caller         TEXT    NOT NULL,
    session_key    TEXT    NOT NULL,
    query          TEXT    NOT NULL,
    orig_query     TEXT,
    aux_queries    TEXT,
    hits_json      TEXT,
    injected_count INTEGER NOT NULL DEFAULT 0,
    route_decision TEXT,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS ix_rq_sk_ts  ON rag_queries (session_key, ts);
CREATE INDEX IF NOT EXISTS ix_rq_caller ON rag_queries (caller, ts);

CREATE TABLE IF NOT EXISTS memory_writes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    session_key     TEXT    NOT NULL,
    source_ref      TEXT,
    action          TEXT    NOT NULL,
    memory_type     TEXT,
    item_id         TEXT,
    summary         TEXT,
    superseded_ids  TEXT,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS ix_mw_sk_ts ON memory_writes (session_key, ts);
CREATE INDEX IF NOT EXISTS ix_mw_action ON memory_writes (action, ts);

CREATE TABLE IF NOT EXISTS global_errors (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint    TEXT    NOT NULL,
    bucket         TEXT    NOT NULL,
    source         TEXT    NOT NULL,
    logger_name    TEXT,
    error_type     TEXT,
    message        TEXT,
    traceback_text TEXT,
    level          TEXT,
    first_ts       TEXT    NOT NULL,
    last_ts        TEXT    NOT NULL,
    count          INTEGER NOT NULL DEFAULT 1,
    session_keys   TEXT,
    status         TEXT    NOT NULL DEFAULT 'active'
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_gerr_fp_bucket ON global_errors (fingerprint, bucket);
CREATE INDEX IF NOT EXISTS ix_gerr_last_ts ON global_errors (last_ts);
CREATE INDEX IF NOT EXISTS ix_gerr_type ON global_errors (error_type, last_ts);
"""


_TURNS_COLUMNS: dict[str, str] = {
    "tool_chain_json": "TEXT",
    "raw_llm_output": "TEXT",
    "meme_tag": "TEXT",
    "meme_media_count": "INTEGER",
    "history_window": "INTEGER",
    "history_messages": "INTEGER",
    "history_chars": "INTEGER",
    "history_tokens": "INTEGER",
    "prompt_tokens": "INTEGER",
    "next_turn_baseline_tokens": "INTEGER",
    "react_iteration_count": "INTEGER",
    "react_input_sum_tokens": "INTEGER",
    "react_input_peak_tokens": "INTEGER",
    "react_final_input_tokens": "INTEGER",
    "react_cache_prompt_tokens": "INTEGER",
    "react_cache_hit_tokens": "INTEGER",
}


def _ensure_turns_columns(conn: sqlite3.Connection) -> None:
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(turns)").fetchall()
    }
    for col, ddl in _TURNS_COLUMNS.items():
        if col in cols:
            continue
        _ = conn.execute(f"ALTER TABLE turns ADD COLUMN {col} {ddl}")


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    _ = conn.executescript(_SCHEMA_SQL)
    _ensure_turns_columns(conn)
    conn.commit()
    return conn