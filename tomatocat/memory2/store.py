"""向量记忆存储 - SQLite + numpy 余弦相似度

参考 tomatocat 的 memory2/store.py，做了简化：
- SQLite 存元数据（id, type, summary, content_hash, extra_json 等）
- embedding 向量存到单独文件（每个向量一个 .npy 文件），或存到 SQLite BLOB
- 用 numpy 做余弦相似度计算（纯 Python，不需要 sqlite_vec 扩展）
- 支持记忆类型：preference / event / procedure / profile
- 支持强化计数（reinforcement）
- 支持语义检索 + 关键词匹配混合排序
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as _tz_utc
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_items (
    id            TEXT PRIMARY KEY,
    memory_type   TEXT NOT NULL,
    summary       TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    reinforcement INTEGER NOT NULL DEFAULT 1,
    emotional_weight INTEGER NOT NULL DEFAULT 0,
    extra_json    TEXT,
    source_ref    TEXT,
    happened_at   TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_items_hash
    ON memory_items (content_hash, memory_type);
CREATE INDEX IF NOT EXISTS ix_items_type_status
    ON memory_items (memory_type, status);
CREATE INDEX IF NOT EXISTS ix_items_happened_at
    ON memory_items (happened_at);
CREATE TABLE IF NOT EXISTS memory_embeddings (
    item_id    TEXT PRIMARY KEY,
    embedding  BLOB NOT NULL,
    dims       INTEGER NOT NULL,
    FOREIGN KEY (item_id) REFERENCES memory_items(id) ON DELETE CASCADE
);
"""


@dataclass
class MemoryItem:
    id: str
    memory_type: str  # preference / event / procedure / profile
    summary: str
    content_hash: str
    reinforcement: int = 1
    emotional_weight: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
    source_ref: str | None = None
    happened_at: str | None = None
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MemoryItem":
        extra = {}
        if row["extra_json"]:
            try:
                extra = json.loads(row["extra_json"])
            except Exception:
                extra = {}
        return cls(
            id=row["id"],
            memory_type=row["memory_type"],
            summary=row["summary"],
            content_hash=row["content_hash"],
            reinforcement=row["reinforcement"],
            emotional_weight=row["emotional_weight"],
            extra=extra,
            source_ref=row["source_ref"],
            happened_at=row["happened_at"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class MemoryHit:
    item: MemoryItem
    score: float  # 相似度分数 0-1
    match_type: str  # semantic / keyword / hybrid


def _now_iso() -> str:
    return datetime.now(_tz_utc.utc).isoformat()


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个向量的余弦相似度"""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


class VectorMemoryStore:
    """向量记忆存储"""

    def __init__(self, db_path: Path, vec_dim: int = 1024) -> None:
        self.db_path = Path(db_path)
        self.vec_dim = vec_dim
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._embedding_cache: dict[str, np.ndarray] = {}
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
            self._embedding_cache.clear()

    # ── 写入 ──────────────────────────────────────────────────

    async def add(
        self,
        memory_type: str,
        summary: str,
        embedding: list[float] | None = None,
        extra: dict[str, Any] | None = None,
        source_ref: str | None = None,
        happened_at: str | None = None,
        emotional_weight: int = 0,
    ) -> MemoryItem:
        """添加一条记忆。如果 content_hash 已存在，则强化计数 +1。"""
        content_h = _content_hash(summary + "|" + memory_type)

        with self._lock:
            assert self._conn is not None

            # 检查是否已存在
            row = self._conn.execute(
                "SELECT * FROM memory_items WHERE content_hash = ? AND memory_type = ?",
                (content_h, memory_type),
            ).fetchone()

            now = _now_iso()

            if row:
                # 已存在，强化
                item_id = row["id"]
                self._conn.execute(
                    "UPDATE memory_items SET reinforcement = reinforcement + 1, updated_at = ? WHERE id = ?",
                    (now, item_id),
                )
                self._conn.commit()
                updated = self._conn.execute(
                    "SELECT * FROM memory_items WHERE id = ?", (item_id,)
                ).fetchone()
                logger.info("[memory2] 记忆强化: %s (count=%d)", item_id, row["reinforcement"] + 1)
                return MemoryItem.from_row(updated)

            # 新建
            item_id = hashlib.md5((content_h + now).encode()).hexdigest()[:12]
            extra_json = json.dumps(extra or {}, ensure_ascii=False)

            self._conn.execute(
                """INSERT INTO memory_items
                   (id, memory_type, summary, content_hash, reinforcement, emotional_weight,
                    extra_json, source_ref, happened_at, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, 'active', ?, ?)""",
                (
                    item_id, memory_type, summary, content_h, emotional_weight,
                    extra_json, source_ref, happened_at, now, now,
                ),
            )

            # 存 embedding
            if embedding is not None:
                vec = np.array(embedding, dtype=np.float32)
                self._conn.execute(
                    "INSERT INTO memory_embeddings (item_id, embedding, dims) VALUES (?, ?, ?)",
                    (item_id, vec.tobytes(), len(embedding)),
                )
                self._embedding_cache[item_id] = vec

            self._conn.commit()
            logger.info("[memory2] 新记忆: [%s] %s (id=%s)", memory_type, summary[:50], item_id)

            row = self._conn.execute(
                "SELECT * FROM memory_items WHERE id = ?", (item_id,)
            ).fetchone()
            return MemoryItem.from_row(row)

    def delete(self, item_id: str) -> bool:
        """删除一条记忆"""
        with self._lock:
            assert self._conn is not None
            self._conn.execute("DELETE FROM memory_items WHERE id = ?", (item_id,))
            self._conn.commit()
            self._embedding_cache.pop(item_id, None)
            return True

    # ── 检索 ──────────────────────────────────────────────────

    def search(
        self,
        query_embedding: list[float] | None = None,
        query_text: str = "",
        memory_types: list[str] | None = None,
        top_k: int = 5,
        min_score: float = 0.3,
    ) -> list[MemoryHit]:
        """混合检索：语义 + 关键词

        Args:
            query_embedding: 查询向量（用于语义检索）
            query_text: 查询文本（用于关键词匹配）
            memory_types: 限定记忆类型，None 表示不限
            top_k: 返回前 N 条
            min_score: 最低相似度分数
        """
        with self._lock:
            assert self._conn is not None

            # 1. 加载所有活跃记忆的 embedding
            type_filter = ""
            params: list[Any] = []
            if memory_types:
                placeholders = ",".join("?" * len(memory_types))
                type_filter = f"AND memory_type IN ({placeholders})"
                params.extend(memory_types)

            rows = self._conn.execute(
                f"""SELECT mi.*, me.embedding, me.dims
                    FROM memory_items mi
                    LEFT JOIN memory_embeddings me ON mi.id = me.item_id
                    WHERE mi.status = 'active' {type_filter}
                    ORDER BY mi.reinforcement DESC, mi.updated_at DESC""",
                params,
            ).fetchall()

            if not rows:
                return []

            # 2. 计算每个记忆的分数
            query_vec = np.array(query_embedding, dtype=np.float32) if query_embedding else None
            hits: list[MemoryHit] = []

            for row in rows:
                item = MemoryItem.from_row(row)
                score = 0.0
                match_type = "keyword"

                # 语义相似度
                if query_vec is not None and row["embedding"] is not None:
                    vec_bytes = row["embedding"]
                    if item.id in self._embedding_cache:
                        vec = self._embedding_cache[item.id]
                    else:
                        vec = np.frombuffer(vec_bytes, dtype=np.float32)
                        self._embedding_cache[item.id] = vec

                    sim = _cosine_similarity(query_vec, vec)
                    # 映射到 0-1 范围（余弦相似度范围 -1 到 1）
                    semantic_score = (sim + 1) / 2
                    score = semantic_score
                    match_type = "semantic"

                # 关键词匹配加分
                if query_text:
                    kw_score = _keyword_score(query_text, item.summary)
                    if kw_score > 0:
                        if score > 0:
                            score = 0.7 * score + 0.3 * kw_score
                            match_type = "hybrid"
                        else:
                            score = kw_score
                            match_type = "keyword"

                # 强化加权
                boost = 1.0 + min(item.reinforcement - 1, 5) * 0.05  # 最多 +25%
                score *= boost

                if score >= min_score:
                    hits.append(MemoryHit(item=item, score=min(score, 1.0), match_type=match_type))

            # 3. 排序并返回 top_k
            hits.sort(key=lambda h: h.score, reverse=True)
            return hits[:top_k]

    def get_all(self, memory_type: str | None = None, limit: int = 100) -> list[MemoryItem]:
        """获取所有记忆（用于调试/查看）"""
        with self._lock:
            assert self._conn is not None
            if memory_type:
                rows = self._conn.execute(
                    """SELECT * FROM memory_items
                       WHERE status = 'active' AND memory_type = ?
                       ORDER BY reinforcement DESC, updated_at DESC
                       LIMIT ?""",
                    (memory_type, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM memory_items
                       WHERE status = 'active'
                       ORDER BY memory_type, reinforcement DESC, updated_at DESC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [MemoryItem.from_row(r) for r in rows]

    def list_items(
        self,
        query: str = "",
        memory_type: str = "",
        status: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        """列表查询（用于 Dashboard）"""
        with self._lock:
            assert self._conn is not None

            conditions = []
            params: list[Any] = []

            if memory_type:
                conditions.append("memory_type = ?")
                params.append(memory_type)

            if status:
                conditions.append("status = ?")
                params.append(status)

            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

            offset = (page - 1) * page_size
            rows = self._conn.execute(
                f"""SELECT * FROM memory_items
                   {where_clause}
                   ORDER BY created_at DESC
                   LIMIT ? OFFSET ?""",
                params + [page_size, offset],
            ).fetchall()

            result = []
            for row in rows:
                item = MemoryItem.from_row(row)
                result.append({
                    "id": item.id,
                    "memory_type": item.memory_type,
                    "summary": item.summary,
                    "reinforcement": item.reinforcement,
                    "emotional_weight": item.emotional_weight,
                    "status": item.status,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "source_ref": item.source_ref,
                })
            return result

    def count(self, memory_type: str | None = None) -> int:
        """统计记忆数量"""
        with self._lock:
            assert self._conn is not None
            if memory_type:
                row = self._conn.execute(
                    "SELECT COUNT(*) as c FROM memory_items WHERE status = 'active' AND memory_type = ?",
                    (memory_type,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) as c FROM memory_items WHERE status = 'active'"
                ).fetchone()
            return row["c"] if row else 0


def _keyword_score(query: str, text: str) -> float:
    """简单关键词匹配分数（基于字符级 n-gram 重叠）"""
    q = query.lower()
    t = text.lower()
    if not q or not t:
        return 0.0

    # 完全包含
    if q in t:
        return 0.8

    # 字符级 bigram 重叠
    def _bigrams(s: str) -> set[str]:
        return {s[i:i+2] for i in range(len(s) - 1)} if len(s) >= 2 else set(s)

    q_bi = _bigrams(q)
    t_bi = _bigrams(t)
    if not q_bi:
        return 0.0
    overlap = q_bi & t_bi
    return len(overlap) / len(q_bi) * 0.6  # 关键词最高 0.6 分
