---
name: Dev
description: TomatoCat Agent 全局开发总路由
metadata: {"skill": {"always": true, "triggers": ["开发", "改代码", "TomatoCat", "项目", "bug", "排查"]}}
---

# TomatoCat Agent - 开发总路由 Skill

## 项目定位

面向个人用户、主打拟人陪伴的多端 AI 助手。桌面宠物、Telegram、QQ 和 CLI 的请求进入同一个异步 Agent 循环，结合五层记忆、插件工具、MCP、后台子 Agent 和主动推送完成陪伴、记账、学习规划、调研与定时任务。

## 技术栈速查

| 模块 | 技术栈 | 入口 |
|---|---|---|
| Agent | Python 3.12 + asyncio + OpenAI 兼容接口 | `tomatocat/agent/agent.py` |
| 记忆 | SQLite + Numpy，自研 embedding store，语义/关键词双路召回 + RRF | `tomatocat/memory.py`、`tomatocat/memory2/` |
| 插件 | Python Plugin + `@tool` + PluginManager + EventBus | `plugins/`、`tomatocat/plugins/`、`tomatocat/bus/` |
| 后台任务 | research/scripting/general profile + sandbox task dir | `tomatocat/agent/background/` |
| 接入 | Telegram、QQ、CLI Socket、桌面回调 | `tomatocat/channels/`、`main.py` |
| 外部能力 | MCP stdio servers（weather/arxiv 等） | `tomatocat/mcp_client.py`、`mcp/` |
| 管理与观测 | FastAPI + uvicorn + structured logs | `tomatocat/dashboard_api.py`、`plugins/observe/` |

## 模块路由表

| 需求类型 | 必读位置 |
|---|---|
| 主 Agent、LLM、上下文、工具循环、子 Agent | `Dev/tomatoCatDev/agent/SKILL.md` |
| 五层记忆、持久化、双路检索、RRF、整合 | `Dev/tomatoCatDev/memory/SKILL.md` |
| 插件加载、工具 Schema、EventBus、MCP、安全 | `Dev/tomatoCatDev/plugin/SKILL.md` |
| 会话、多端消息和回调 | `tomatocat/session/`、`tomatocat/channels/` |
| 主动推送、MCP 数据源、定时任务 | `tomatocat/proactive/`、`mcp/`、`tomatocat/scheduler.py` |
| 管理接口、任务状态、故障排查 | `tomatocat/dashboard_api.py`、`plugins/observe/` |

## 架构设计原则

1. **边界清晰**：Agent 编排，Plugin 提供能力，Memory 管理长期状态，Channel 只负责接入与发送。
2. **失败隔离**：单个工具、MCP 服务、观察器或后台任务失败，不得拖垮主对话循环。
3. **状态可恢复**：会话、PENDING、任务状态、主动推送去重和观测写入都要考虑重启恢复。
4. **安全优先**：Shell、文件写入、网络访问和后台脚本遵循风险检查、profile 权限和资源预算。
5. **可观察**：关键状态转移通过 EventBus 和结构化日志记录，不能依赖猜测或临时 print。

## 六步开发工作流

### 第一步：读取路由 Skills

每次开发请求先阅读本文件，再阅读对应 `tomatoCatDev` 模块文档。即使是续接会话，也重新确认路由、约束和真实入口，不凭记忆直接修改。

### 第二步：需求分析与根因定位

列出复现输入、实际日志、调用链和影响范围。区分配置/环境问题、局部 Bug、数据兼容问题和架构问题；先定位根因，不以症状修补替代分析。

### 第三步：方案制定与授权

涉及架构、协议、数据格式、权限或大范围重构时，给出 2–3 个方案，说明做法、优缺点、风险、影响范围和验证方式，等待用户明确执行授权。简单且根因明确的局部 Bug 可直接修复，但仍需说明影响。

### 第四步：执行实现

只修改已确认范围；复用已有抽象和配置，不硬编码 key、路径、用户 ID 或环境参数。保持异步、session_key、事件字段、错误语义和向后兼容。

### 第五步：交付验证

运行编译检查和最小 smoke test；对网络、LLM、MCP 等外部依赖分别说明“已验证/未验证”。交付时列出修改文件、根因、验证结果和未覆盖风险。

### 第六步：截图/日志兜底

若问题无法仅凭代码定位，明确要求用户提供具体日志区间、命令输出或界面截图，说明需要观察的位置和预期现象，不反复进行无证据尝试。

## 项目目录概览

```text
tomatocat-agent/
├─ main.py                         # 应用启动与组件装配
├─ config.toml                     # LLM、渠道、MCP、主动推送和 scheduler 配置
├─ tomatocat/
│  ├─ agent/                       # 主/子 Agent、后台任务和策略
│  ├─ memory.py, memory2/          # 兼容记忆与向量记忆
│  ├─ plugins/                     # PluginManager、@tool、EventBus
│  ├─ channels/                    # Telegram/QQ/CLI
│  ├─ proactive/                   # 主动推送引擎
│  └─ dashboard_api.py             # FastAPI 管理接口
├─ plugins/                        # 具体插件实现
├─ mcp/                            # weather/arxiv MCP server
└─ skills/Dev/                     # 本开发技能体系
```

## 启动与验证

```powershell
python -m compileall -q tomatocat plugins
python main.py
```

建议验证顺序：启动与配置 → 普通对话 → 记忆 add/search → 单工具与多工具循环 → 后台 spawn/cancel/callback → MCP/主动推送 → Dashboard 状态接口。

## 全局红线

- 普通工具不接收内部 `_session_key` / `_channel`，只有显式声明或 `**kwargs` 的工具可以接收。
- 不绕过 `shell_safety`、Token 管控、loop guard、delegation policy 或 sandbox。
- 不把 MCP 错误文本直接当 JSON；外部响应必须容错，单源失败继续运行。
- 不在 PENDING 未持久化成功时清理它，不在任务未结束时删除 task directory。
- 不提交真实 API key、token、私聊 ID、机器绝对路径或临时日志。
