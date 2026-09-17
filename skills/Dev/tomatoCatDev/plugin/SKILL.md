---
name: Dev/tomatoCatDev/plugin
description: TomatoCat 插件、工具、EventBus、MCP 与安全边界开发指南
metadata: {"skill": {"parent": "Dev/tomatoCatDev", "triggers": ["插件", "plugin", "工具", "注册", "工具注册", "热加载", "EventBus", "事件总线", "MCP", "Shell", "安全"]}}
---

# Plugin 模块 - 插件生态代码导航

## 模块定位

插件层把记账、学习、文件、Shell、搜索、MCP 等能力接入 Agent。它同时维护工具 Schema、生命周期和风险边界；EventBus 连接 Agent、记忆、观测、主动推送和后台任务。新增插件不是“加一个函数”，而是增加一条完整的能力契约。

## 目录与文件职责

```text
tomatocat/plugins/
├─ base.py                 # Plugin、PluginContext、生命周期接口
├─ decorators.py           # @tool、ToolInfo、JSON Schema
└─ manager.py              # 扫描、加载、注册、执行、MCP 适配
tomatocat/bus/__init__.py  # EventBus、事件类型、observe 队列
plugins/
├─ accounting/ study_plan/ scheduler/ memory2/
├─ filesystem/ shell/ shell_safety/
├─ web_search/ web_fetch/ spawn/ tool_loop_guard/
└─ observe/ pixel_cat/ status_commands/
```

| 文件 | 职责 | 关键约定 |
|---|---|---|
| `plugins/manager.py` | `load_all`、插件实例、ToolInfo registry、`execute_tool`、MCP 注册 | 单插件失败隔离；同步/异步统一等待 |
| `plugins/decorators.py` | 从函数签名派生参数 Schema | `self/event` 不进 Schema，用户参数必须显式声明 |
| `plugins/base.py` | PluginContext、initialize/terminate | 插件通过 context 访问 workspace/manager，不自行寻找全局对象 |
| `bus/__init__.py` | on/emit/fanout/enqueue/drain、事件类 | observe 异常不反向拖垮主流程 |
| `plugins/shell_safety/` | 命令审查、交互命令和危险操作拒绝 | fail closed |
| `plugins/tool_loop_guard/` | 按会话/工具/参数签名限制循环 | 不得只按工具名粗暴拦截 |

## 生命周期

```text
plugins/<id>/plugin.py
 → import module
 → find Plugin subclass
 → PluginContext
 → initialize()
 → collect @tool → ToolInfo
 → Agent execute + hooks
 → terminate()/unload
```

热加载必须清理实例、工具 registry、事件订阅、旧 task 和模块缓存；否则会出现同名工具调用旧闭包、事件重复消费和资源泄漏。

## 工具契约

```text
@tool
  → ToolInfo(name, description, parameters, risk)
  → PluginManager.get_all_tools()
  → LLM function schema
  → execute_tool(tool_name, arguments)
```

- 工具通常接收 `(self, event, user_args...)`；`event` 是内部事件，不应暴露给模型。
- `_session_key`、`_channel` 是内部上下文，只向函数显式声明或接收 `**kwargs` 的工具注入。
- 普通工具参数错误、插件异常、超时都要转成可读结果并保留日志。
- 工具名冲突、Schema 缺参和默认值错误必须在加载/测试阶段暴露，不要等 LLM 调用才发现。

## EventBus 事件流

```text
TurnStart → RetrievalCompleted → tool hooks
         → MemoryWritten → TurnCommitted → TurnEnd
```

`emit/fanout` 用于主流程协作，`enqueue/drain` 用于异步观测。新增或修改事件字段时，要检查所有订阅者、observe writer、管理查询和关闭时 drain；观察器异常必须被隔离。

## MCP 适配

MCP 工具名统一为 `mcp_<server>__<tool>`。配置中的 command、脚本路径和环境变量必须真实存在；启动时应记录连接服务器和工具数量。返回值可能是 `content[].text`、`structuredContent`、空响应或错误文本，调用层必须容错，不得让主动推送或 Agent 主循环崩溃。

## 安全边界

```text
tool schema
 → risk 标记
 → shell_safety / token 管控 / loop guard
 → profile/sandbox（后台任务）
 → plugin execute
 → 结构化日志 + 结果回调
```

Shell、文件写入、后台脚本和外部网络请求必须检查路径、命令、权限、资源预算和取消行为。拒绝路径应无副作用；日志记录决策依据，但不得泄露 key、完整环境变量或敏感正文。

## 常见问题与定位

| 现象 | 定位顺序 |
|---|---|
| 工具不存在 | 插件目录 → import → Plugin 子类 → @tool → ToolInfo → MCP 连接 |
| unexpected keyword | 函数签名 → `_session_key`/`_channel` 注入条件 |
| 热加载仍旧代码 | registry → 事件 handler → task → `sys.modules` |
| 工具被拦截 | risk → shell_safety → token → loop guard |
| MCP 工具为 0 | config 路径 → command 文件 → 子进程 stderr → tools/list |
| 事件重复/丢失 | on 注册次数 → emit/fanout → observe queue → handler 异常 |

## 新增插件工作流

1. 先写工具职责、参数 Schema、风险等级和生命周期需求。
2. 在 `plugins/<id>/plugin.py` 实现，复用 PluginContext、EventBus 和现有安全策略。
3. 验证加载、Schema、普通/异常/并发调用，再验证卸载和重启。
4. 若涉及 MCP 或 Shell，额外覆盖服务不可用、空响应、拒绝路径和资源超限。

```powershell
python -m compileall -q tomatocat plugins
```
