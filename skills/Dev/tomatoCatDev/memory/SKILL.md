---
name: Dev/tomatoCatDev/memory
description: TomatoCat 五层记忆、SQLite/Numpy 存储、双路召回与整合开发指南
metadata: {"skill": {"parent": "Dev/tomatoCatDev", "triggers": ["五层记忆", "memory", "记忆", "SQLite", "Numpy", "向量", "召回", "检索", "RRF", "PENDING", "情感权重"]}}
---

# Memory 模块 - 五层记忆代码导航

## 模块定位

Memory 决定 Agent 能否长期理解用户。它包含对话后的记忆提取与整合、五层记忆持久化、轻量向量存储，以及语义和关键词双路检索。该模块既有兼容的 `MemoryEngine`，也有较新的 `memory2` 实现，修改前必须确认调用方使用哪一条链路。

## 目录与文件职责

```text
tomatocat/
├─ memory.py                    # 兼容引擎：Markdown/PENDING/整合触发
└─ memory2/
   ├─ store.py                  # SQLite 元数据 + Numpy 向量
   ├─ embedder.py               # embedding 请求与降级
   └─ retriever.py              # 双路召回 + RRF + 注入块
plugins/memory2/plugin.py       # Agent 工具接口
```

| 文件 | 职责 | 关键函数/类 |
|---|---|---|
| `tomatocat/memory.py` | 对话计数、PENDING、抽取、整合和兼容上下文 | `MemoryEngine`、`extract_and_pending`、`consolidate` |
| `tomatocat/memory2/store.py` | 数据库初始化、hash 去重、向量增删查、关键词分数 | `VectorMemoryStore`、`MemoryItem` |
| `tomatocat/memory2/embedder.py` | embedding 模型调用、维度和不可用降级 | `Embedder` |
| `tomatocat/memory2/retriever.py` | semantic/keyword 搜索、RRF、上下文格式化 | `Retriever`、`MemoryHit` |
| `plugins/memory2/plugin.py` | `memory_search/add/list/delete` 工具和配置读取 | Plugin 工具方法 |

## 五层记忆数据流

```text
对话完成
  → extract_and_pending
  → PENDING（可恢复候选）
  → consolidate：确认/去重/强化/情感权重
  → memory_type/layer
  → SQLite metadata + Numpy vector
```

五层表达记忆生命周期和用途；`memory_type` 表达内容类别。新增类别不能只改写入处，必须同步检索过滤、统计、展示和上下文分组。

## 检索链路

```text
query
 → normalize + embedding
 → _semantic_search()      ┐
 → _keyword_search()       ├→ _merge_with_rrf()
 → filters/top-k           ┘
 → build_inject_block()
 → Agent system/context
```

语义和关键词是互补路径，任一路失败时另一条仍可返回结果。RRF 要处理空候选、重复 item、并列分数和 top-k 边界；不能用未经验证的“加权平均”替换既有排序契约。

## 持久化约定

- `content_hash` 用于规范化内容去重，不能只按标题或向量相似度去重。
- 向量维度、归一化方式、SQLite 文件路径必须与历史数据兼容。
- PENDING 只有在持久化成功后才能清理；整合失败要保留原文和错误信息。
- 强化计数和情感权重必须有上下限，重复对话不能无限放大重要性。
- 用户/会话隔离要在写入、召回、删除和管理接口全部生效。

## 常见问题与定位

| 现象 | 定位顺序 |
|---|---|
| 刚写入搜不到 | 写入完成 → embedding → DB 路径 → 类型/用户过滤 → top-k |
| 相关性差 | query 规范化 → 关键词提取 → cosine → 两路候选 → RRF |
| 记忆重复 | content hash → 并发写入 → PENDING 去重 → 整合触发 |
| 上下文过长 | 各层配额 → top-k → 单条截断 → Agent token 压力 |
| 重启丢失 | SQLite 文件 → schema 初始化 → vector 文件/列 → workspace 配置 |
| 情感记忆异常 | 抽取结果 → 权重边界 → 时间衰减 → 注入排序 |

## 变更边界

改 `store.py` 先考虑已有数据库迁移；改评分必须保留单路/双路/空库样例；改整合必须验证 PENDING、计数重置和 `MemoryWritten`；改注入格式必须检查 Agent 系统提示和 token 截断。不要为了“看起来更智能”引入新的向量数据库或依赖，除非先完成方案评估。

## 验证清单

```text
[ ] add → search → list → delete
[ ] 语义命中、关键词命中、双路命中、空结果
[ ] embedding 不可用时降级
[ ] 重复写入与强化计数边界
[ ] PENDING 整合成功/失败恢复
[ ] 重启后 SQLite 与向量可读取
[ ] RetrievalCompleted/MemoryWritten 字段完整
```
