---
name: Dev/tomatoCatDev
description: TomatoCat Agent 项目模块总入口与开发路由
metadata: {"skill": {"parent": "Dev", "triggers": ["TomatoCat", "番茄猫", "陪伴助手", "项目架构", "Agent", "记忆", "插件"]}}
---

# TomatoCat Agent 项目模块 - 开发总入口

> 本项目是一个面向个人用户的多端 AI 陪伴宠物助手。本文档只描述 TomatoCat 的真实实现、文件边界和开发约定；通用的开发决策先读 `Dev/SKILL.md`。

## 项目定位

用户可以从桌面宠物、Telegram、QQ 或 CLI 发送消息。所有请求进入同一个主 Agent 循环，由 LLM 决策是否调用工具、检索记忆或委派后台任务；插件和 MCP 提供外部能力，主动推送引擎在没有用户请求时也可产生事件。

## 技术栈速查

| 层级 | 技术/实现 | 关键入口 |
|---|---|---|
| 运行时 | Python 3.12、asyncio、uvicorn | `main.py`、`tomatocat/` |
| LLM | OpenAI 兼容 Chat Completions，支持 thinking、多模态配置 | `tomatocat/agent/llm.py`、`config.toml` |
| Agent | 主循环 + tool call + 子 Agent profile/policy | `tomatocat/agent/agent.py` |
| 记忆 | SQLite + Numpy embedding，语义/关键词双路召回 + RRF | `tomatocat/memory2/`、`tomatocat/memory.py` |
| 工具 | 插件目录、`@tool`、动态注册、MCP 适配 | `plugins/`、`tomatocat/plugins/` |
| 事件 | EventBus 核心事件 + 异步 observe 队列 | `tomatocat/bus/__init__.py` |
| 接入 | Telegram、QQ、CLI Socket、桌面端回调 | `tomatocat/channels/`、`main.py` |
| 运营 | 主动推送、定时任务、FastAPI 管理面板 | `tomatocat/proactive/`、`scheduler.py`、`dashboard_api.py` |

## 模块路由表

| 需求类型 | 必读位置 |
|---|---|
| 主 Agent、上下文、LLM、工具循环、子 Agent | `Dev/tomatoCatDev/agent/SKILL.md` |
| 五层记忆、SQLite/Numpy、召回、整合 | `Dev/tomatoCatDev/memory/SKILL.md` |
| 插件加载、Tool Schema、EventBus、MCP、安全 | `Dev/tomatoCatDev/plugin/SKILL.md` |
| Telegram/QQ/CLI 会话接入 | `tomatocat/channels/` + `tomatocat/session/` |
| 主动推送和数据源 | `tomatocat/proactive/engine.py`、`mcp/` |
| 定时任务和断点恢复 | `tomatocat/scheduler.py`、`plugins/scheduler/` |
| 管理面板和任务观测 | `tomatocat/dashboard_api.py`、`plugins/observe/` |

## 端到端架构

```text
Telegram / QQ / CLI / Desktop
            ↓
      session_key(channel:chat_id)
            ↓
       TomatoCatAgent
   ┌────────┼─────────┐
 memory   LLM      tools/plugins
   │        │          │
 SQLite   tool_call   EventBus
 Numpy      │          │
   └────────┴──────────┘
            ↓
   reply / background callback / proactive push
```

## 开发工作流

1. 先读本文件和对应模块 Skill，确认入口、调用链、状态持久化和事件消费者。
2. 做一次性调查：用日志、代码搜索和最小复现定位根因，区分配置/环境问题、局部 Bug 和架构问题。
3. 涉及架构、协议、数据格式或权限时，先给出 2–3 个方案及影响，再等待用户明确授权；普通局部 Bug 可在根因清楚后直接修复。
4. 修改严格限制在已确认范围内，保持现有会话隔离、异步行为、错误语义和向后兼容。
5. 运行编译检查和针对性 smoke test；不能把“代码能运行”当作外部网络、LLM 或 MCP 已验证。
6. 交付说明修改文件、根因、验证结果、未覆盖风险和需要用户手工操作的步骤。

## 项目级铁律

- 任何跨模块改动都要检查 `session_key`、EventBus 事件和结构化日志。
- 高风险工具、Shell、文件写入和后台任务必须经过安全策略；不能为了测试绕过检查。
- MCP 配置中的命令、工作目录和脚本路径必须真实存在；单个服务失败不能拖垮主循环。
- 记忆写入成功后才能清理 PENDING；任务完成/取消/失败都要释放状态并可追踪。
- 不硬编码 API key、用户 ID、机器路径和环境专属端口；使用 `config.toml`、环境变量或 workspace 配置。

## 常用验证

```powershell
python -m compileall -q tomatocat plugins
python main.py
```

验证顺序：普通对话 → 记忆写入/召回 → 单工具调用 → 多工具循环 → spawn 后台任务 → MCP/主动推送 → 管理面板接口。网络依赖不可用时，应单独验证降级和错误日志。
