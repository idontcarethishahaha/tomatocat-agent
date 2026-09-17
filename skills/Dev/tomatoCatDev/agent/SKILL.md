---
name: Dev/tomatoCatDev/agent
description: TomatoCat 主 Agent 循环、上下文、工具调用与后台子 Agent 开发指南
metadata: {"skill": {"parent": "Dev/tomatoCatDev", "triggers": ["主 Agent", "Agent 循环", "LLM", "上下文", "工具", "调用", "工具调用", "没有调用", "agent", "tool", "tool call", "子 Agent", "委派", "delegation", "后台任务"]}}
---

# Agent 模块 - 代码导航与工作手册

## 模块定位

Agent 是 TomatoCat 的编排层，不承载具体记账、搜索或文件业务。它负责把一次入站消息变成可观察、可恢复的处理回合：建立会话、注入记忆、请求 LLM、执行工具、限制迭代，并在复杂任务时把工作交给隔离的子 Agent。

## 现有代码结构

```text
tomatocat/agent/
├─ agent.py                         # 主循环与上下文
├─ llm.py                           # LLM provider、重试、thinking
├─ subagent.py                      # 单个子 Agent 循环
├─ background/
│  ├─ subagent_manager.py           # spawn、取消、状态、回调
│  └─ subagent_profiles.py          # research/scripting/general
└─ policies/delegation.py           # 是否委派、profile 决策
```

## 文件职责

| 文件 | 关键职责 | 不应放入的逻辑 |
|---|---|---|
| `agent.py` | `handle_message`、系统提示、memory search、tool loop、loop guard | 具体插件业务、数据库 SQL |
| `llm.py` | 模型配置、请求、重试、响应提取、429/超时错误 | 会话持久化、工具权限决策 |
| `subagent.py` | 子 Agent 消息循环、工具执行、结果裁剪、最终摘要 | 主会话发送和全局任务调度 |
| `background/subagent_manager.py` | 任务目录、profile 工具集、spawn/spawn_sync、cancel、完成事件 | 绕过 policy 的直接执行 |
| `policies/delegation.py` | 复杂度、并发上限和 profile 选择 | 实际任务执行 |

## 请求处理链

```text
InboundMessage
 → SessionManager.get_or_create(session_key)
 → TurnStartEvent
 → _build_system_with_memory()
 → memory.search() / RetrievalCompleted
 → LLM chat completion
 → tool safety → loop guard
 → PluginManager.execute_tool()
 → tool result 回填 messages
 → 最终文本 / TurnCommitted / TurnEndEvent
```

`session_key` 格式为 `channel:chat_id`，例如 `telegram:8824997206`。它同时用于会话历史、记忆隔离、后台回调目标和观测聚合，不能在模块之间改成裸用户 ID。

## 主循环约定

- 首轮才插入系统提示；后续轮次复用同一会话历史，避免重复注入。
- 记忆检索失败时仍需继续对话，并发出带 error 的 `RetrievalCompleted`。
- 工具异常要转成可读结果回填给 LLM；不能因为一个工具抛异常而丢失整轮会话。
- `max_iterations`、token 压力和 loop guard 是硬边界，重试不能无限延长循环。
- LLM 返回 thinking、content、tool calls 时要分别处理，不能把内部 reasoning 当作工具参数。

## 子 Agent 委派规则

```text
简单问答/单次查询 → 主 Agent 直接完成
多次搜索/批量处理/长耗时脚本 → delegation policy
  → profile(research|scripting|general)
  → 独立 task_dir + 受限工具集
  → 异步执行 → SpawnCompletionEvent
  → 原始 channel/chat_id 回调
```

异步 `spawn` 不阻塞当前会话；`spawn_sync` 只用于短任务。任务取消、异常、超时都必须从 running 状态移除并留下原因。

## 问题排查

### “没有调用工具”

按顺序检查：插件是否注册 → `get_all_tools()` Schema → LLM 是否返回 `tool_calls` → `_check_tool_safety` 是否拒绝 → `_check_tool_loop` 是否拦截 → `execute_tool` 结果是否回填。不要在没有证据时只改提示词。

### “工具重复调用/一直循环”

检查参数签名是否稳定、错误结果是否为空、loop guard 的 session/tool/signature 是否一致、迭代计数是否递增。保留原始工具名和参数摘要日志。

### “子 Agent 没启动”

检查 policy 决策、running count、profile 名称、task_dir、工具白名单和后台 task 异常；不要直接删除 policy 以“验证能否启动”。

### “回复卡住/重复发送”

检查 LLM 超时和重试、取消传播、消息提交点、`TurnEndEvent` 是否发出，以及异常分支是否再次调用 send。

## 修改边界与验证

改主循环必须覆盖普通对话、单工具、多工具、429/超时和最大迭代；改委派必须覆盖异步、同步、取消、失败回调和重启恢复；改上下文必须比较 token 长度、系统提示顺序和多端隔离。最小验证命令：

```powershell
python -m compileall -q tomatocat plugins
python test_cli.py
```
