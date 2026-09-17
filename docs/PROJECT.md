# TomatoCat 项目文档

> 版本：0.2.0  ·  Python：3.12+

TomatoCat 是一个带有像素猫桌面形象的个人 AI 助手。它通过统一的 Agent 核心处理对话、工具调用和记忆，并可接入 CLI Socket、Telegram、QQ 以及 PyQt6 桌面宠物界面。

## 1. 功能概览

- **对话 Agent**：调用 OpenAI 兼容的聊天模型，支持流式输出、思考过程和多轮工具调用。
- **工具与 MCP**：内置文件、Shell、网页搜索/抓取、图片理解、记忆和子 Agent 工具；可选加载 MCP 服务。
- **长期记忆**：维护 `SELF.md`、`MEMORY.md`、`PENDING.md`、`HISTORY.md` 和 `journal/`，并可使用 SQLite/numpy 向量检索与关键词检索混排。
- **多通道接入**：CLI Socket 默认监听 `127.0.0.1:8771`（以配置为准）、Telegram Bot、QQ/NapCat。
- **主动任务**：调度器支持定时提醒；Proactive 引擎可按 profile 周期性生成并推送消息。
- **桌面宠物**：PyQt6 像素猫、GIF/Meme 表情、番茄钟和后台文件分析。
- **插件系统**：从 `plugins/` 动态加载插件，插件可以注册工具并订阅生命周期事件。

## 2. 系统架构

```text
消息通道 (CLI / Telegram / QQ / Desktop)
             │
             ▼
        Agent 主循环
   ┌─────────┼─────────┐
   ▼         ▼         ▼
记忆检索   工具/MCP   子 Agent
   │         │         │
   └──────► LLM ◄──────┘
             │
             ▼
       流式响应与事件总线
             │
             ▼
  记忆写入 / 日志 / 定时与主动推送
```

程序入口是仓库根目录的 `main.py`。启动时会读取 TOML 配置，创建 EventBus、SessionManager、Memory、PluginManager、各消息通道及可选调度器，然后进入常驻服务或桌面模式。

## 3. 安装与启动

### 环境准备

```powershell
pip install uv
uv venv --python 3.12
.venv\Scripts\Activate.ps1
uv sync
```

复制 `config.toml.example` 为 `config.toml`，至少填写主模型的 `model`、`api_key` 和（如需要）`base_url`。

### 默认后端模式

```powershell
uv run python main.py --workspace .\workspace
```

后端会启动配置中启用的通道并持续运行。按 `Ctrl+C` 退出。

### 桌面宠物模式

```powershell
uv run python main.py --desktop --workspace .\workspace
```

桌面模式在 Qt 主线程运行界面，在后台线程运行 asyncio Agent。需要已安装 PyQt6（项目依赖会自动安装）。

### 命令行参数

| 参数 | 说明 |
| --- | --- |
| `--workspace DIR` | 运行时数据目录；默认 `./workspace` |
| `--config PATH` | TOML 配置文件；默认 `config.toml` |
| `--desktop` | 启用桌面宠物模式 |

### 后台任务恢复

```powershell
# 查看任务、重试次数和结果投递状态
uv run python main.py jobs --workspace .\workspace

# research 任务是只读/隔离写入任务，可直接显式重试
uv run python main.py retry JOB_ID --workspace .\workspace

# scripting/general 和旧任务可能产生外部副作用，确认安全后才能强制重试
uv run python main.py retry JOB_ID --force --workspace .\workspace
```

任务通知使用持久化 outbox。`pending` 表示尚未尝试发送，正常启动时会恢复；`delivered` 表示已发送；`failed` 表示发送明确失败；`sending` 表示进程可能在发送期间中断，结果不确定。系统不会自动重发 `sending`，以免目标通道收到重复消息。

## 4. 配置说明

配置模板见 [`config.toml.example`](../config.toml.example)，加载逻辑见 [`tomatocat/config.py`](../tomatocat/config.py)。常用配置如下：

```toml
[llm.main]
model = "your-model"
api_key = "sk-..."
base_url = "https://api.openai.com/v1"

[channels.cli]
enabled = true
socket = "127.0.0.1:8771"

[channels.telegram]
enabled = false
token = ""
allow_from = []

[memory]
enabled = true
memory_window = 40
vector_enabled = true
```

- `llm.main`：主对话模型；`llm.fast` 用于轻量后台任务；`llm.vl` 用于视觉输入；`llm.embedding` 用于向量嵌入。
- `channels.*`：按需启用通道。Telegram 需要 Bot Token；QQ 依赖 NapCat/WebSocket 环境。
- `memory.vector_enabled`：开启后需要可用 embedding 配置；不可用时仍可使用关键词检索。
- `mcp.enabled`：开启后从 `mcp.config_file` 加载 MCP 服务定义。
- `scheduler` / `proactive`：分别控制定时任务和主动推送，生产环境请同时配置目标 channel 与 chat id。
- `meme.meme_dir`：Meme/GIF 资源目录，默认 `memes`。

不要把包含 API Key、Bot Token 的 `config.toml` 提交到版本库；使用环境变量或本地配置管理工具保护密钥。

## 5. 工作区与数据

`--workspace` 下的内容与源码分离，典型结构如下：

```text
workspace/
├─ memory/
│  ├─ SELF.md          # 助手自我设定
│  ├─ MEMORY.md        # 用户长期事实与偏好
│  ├─ PENDING.md       # 待整合记忆
│  ├─ HISTORY.md       # 对话历史摘要
│  └─ journal/         # 每日归档
├─ memory2/            # 向量存储（如启用）
├─ subagent-runs/      # 子 Agent 隔离工作区
├─ tasks.db            # 子 Agent/调度执行状态与通知 outbox
├─ proactive_state.json # 主动推送去重与待 ack 状态
├─ schedules.json      # 定时任务
├─ checkpoints/        # 记忆整合检查点
├─ uploads/            # 通道上传文件
└─ logs/bot.log        # 运行日志
```

备份或迁移实例时，复制整个 workspace；其中可能包含个人对话和敏感数据。

## 6. 插件开发

插件目录位于仓库根目录 `plugins/`（也可通过配置调整）。每个插件至少包含一个 `plugin.py`，并定义 `tomatocat.plugins.base.Plugin` 的子类。插件初始化时可通过装饰器注册工具，工具会自动注入 Agent 工具列表。

```text
plugins/my_plugin/
└─ plugin.py
```

插件加载失败不会阻止主程序启动，具体原因会写入日志。开发插件时应限制文件访问范围、校验外部输入，并避免执行高风险 Shell 命令。

## 7. 安全与运维建议

- 仅在可信网络暴露 CLI Socket 或 Web 服务；需要远程访问时使用反向代理和鉴权。
- Telegram/QQ 使用 `allow_from` 白名单限制可交互用户。
- 子 Agent 使用独立的 `subagent-runs/<job_id>/` 工作区；定期清理已完成任务产生的临时文件。
- 关注 `workspace/logs/bot.log`，遇到模型、通道或插件错误先查看对应堆栈。
- 关闭不需要的 MCP、Shell、主动推送和外部通道，遵循最小权限原则。

## 8. 开发与测试

安装开发依赖并运行测试：

```powershell
uv sync --group dev
uv run pytest
```

关键模块：

| 模块 | 职责 |
| --- | --- |
| `tomatocat/agent/` | Agent 循环、LLM 适配、工具与子 Agent |
| `tomatocat/memory.py`、`tomatocat/memory2/` | 文件记忆与向量记忆 |
| `tomatocat/channels/` | CLI、Telegram、QQ 通道 |
| `tomatocat/plugins/` | 插件基类、装饰器、动态加载 |
| `tomatocat/proactive/`、`scheduler.py` | 主动推送与定时任务 |
| `tomatocat/lifecycle/`、`tomatocat/bus/` | 生命周期钩子与事件总线 |

## 9. 故障排查

1. **启动即提示配置错误**：确认 `config.toml` 存在且为合法 TOML；可从模板重新复制。
2. **模型无响应**：检查 `api_key`、`base_url`、模型名和网络/代理设置。
3. **Telegram/QQ 不收消息**：确认通道已启用、凭据有效，并检查 `allow_from` 是否误拦截。
4. **向量检索失败**：先确认 embedding 模型配置；临时将 `vector_enabled = false` 使用关键词检索。
5. **桌面窗口无法启动**：确认 Python 环境安装了 PyQt6，并在具有桌面会话的机器上运行 `--desktop`。

## 10. 许可证与贡献

当前仓库未声明明确开源许可证。对外发布前请补充 LICENSE，并在贡献代码时说明变更目的、测试方式和配置影响。
